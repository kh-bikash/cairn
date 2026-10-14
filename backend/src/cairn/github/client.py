"""Minimal GitHub GraphQL + REST client with retry and rate-limit handling."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {502, 503, 504} or _is_secondary_limit(exc.response)
    return False


def _is_secondary_limit(resp: httpx.Response) -> bool:
    return resp.status_code in {403, 429} and "rate limit" in resp.text.lower()


class GitHubClient:
    def __init__(self, token: str, *, timeout: float = 60.0) -> None:
        self._http = httpx.Client(
            base_url=API,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cairn-company-brain",
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @retry(retry=retry_if_exception(_retryable), wait=wait_exponential_jitter(multiplier=2, max=60), stop=stop_after_attempt(6), reraise=True)
    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        resp = self._http.post("/graphql", json={"query": query, "variables": variables})
        if _is_secondary_limit(resp):
            self._sleep_for_reset(resp)
        resp.raise_for_status()
        payload = resp.json()
        errors = payload.get("errors") or []
        # NOT_FOUND on optional blob lookups (CODEOWNERS etc.) is expected; anything else is fatal.
        fatal = [e for e in errors if e.get("type") != "NOT_FOUND"]
        if fatal:
            raise GitHubError("; ".join(e.get("message", str(e)) for e in fatal))
        data: dict[str, Any] = payload.get("data") or {}
        self._respect_budget(data.get("rateLimit"))
        return data

    @retry(retry=retry_if_exception(_retryable), wait=wait_exponential_jitter(multiplier=2, max=60), stop=stop_after_attempt(6), reraise=True)
    def rest(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if _is_secondary_limit(resp):
            self._sleep_for_reset(resp)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    @staticmethod
    def _respect_budget(rate: dict[str, Any] | None) -> None:
        if rate and rate.get("remaining", 1000) < 50:
            log.warning("GitHub GraphQL budget nearly exhausted (%s left); pausing 60s", rate.get("remaining"))
            time.sleep(60)

    @staticmethod
    def _sleep_for_reset(resp: httpx.Response) -> None:
        retry_after = resp.headers.get("retry-after")
        reset = resp.headers.get("x-ratelimit-reset")
        if retry_after:
            delay = float(retry_after)
        elif reset:
            delay = max(0.0, float(reset) - time.time())
        else:
            delay = 60.0
        delay = min(delay, 900.0)
        log.warning("GitHub rate limited; sleeping %.0fs", delay)
        time.sleep(delay)
        resp.raise_for_status()
