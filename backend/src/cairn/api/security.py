"""Access control and abuse protection for a public deployment.

- Reads are open. Anything that writes (approving actions, syncing repos) needs the admin token when
  CAIRN_ADMIN_TOKEN is set, sent as `Authorization: Bearer <token>`.
- /api/ask is rate limited per client so a public URL can't drain the LLM quota.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    token = request.app.state.cairn.settings.admin_token
    if token is None:
        return  # open mode (local development); writes are still limited to CAIRN_ACTION_REPOS
    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied, token.get_secret_value()):
        raise HTTPException(401, "admin token required", headers={"WWW-Authenticate": "Bearer"})


class RateLimiter:
    """Sliding-window limit per client key (in-process; one API replica is the deployment model)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            raise HTTPException(429, f"Too many questions; try again in {retry}s.", headers={"Retry-After": str(retry)})
        hits.append(now)
        if len(self._hits) > 10_000:  # bound memory under many distinct clients
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                del self._hits[k]


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
