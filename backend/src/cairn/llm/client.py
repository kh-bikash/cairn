"""Provider-agnostic LLM access over OpenAI-compatible endpoints (Groq, Gemini, OpenAI, Anthropic, TypeSafe).

Every call goes through `LLM.chat`, which adds:
- a content-addressed disk cache (re-running ingestion costs zero quota),
- per-provider concurrency limits and retry/backoff on 429/5xx,
- usage accounting surfaced in the UI's cost panel.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from cairn.config import PROVIDER_BASE_URLS, Settings

log = logging.getLogger(__name__)

Role = Literal["decide", "extract", "agent"]
T = TypeVar("T", bound=BaseModel)


class LLMNotConfigured(RuntimeError):
    """Raised when a role's provider has no API key."""


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict[str, Any]]
    model: str
    cached: bool
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def message(self) -> dict[str, Any]:
        """The assistant message to append to the conversation."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in self.tool_calls]
        return msg


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    errors: int = 0


@dataclass
class UsageBook:
    by_role: dict[str, Usage] = field(default_factory=lambda: defaultdict(Usage))

    def record(self, role: str, r: ChatResult) -> None:
        u = self.by_role[role]
        u.calls += 1
        u.cache_hits += int(r.cached)
        u.prompt_tokens += r.prompt_tokens
        u.completion_tokens += r.completion_tokens
        u.latency_ms += r.latency_ms

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {role: vars(u).copy() for role, u in self.by_role.items()}


def parse_model_ref(ref: str) -> tuple[str, str]:
    provider, sep, model = ref.partition(":")
    if not sep or provider not in PROVIDER_BASE_URLS:
        raise ValueError(f"model ref must be 'provider:model' with provider in {sorted(PROVIDER_BASE_URLS)}; got {ref!r}")
    return provider, model


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.usage = UsageBook()
        self._clients: dict[str, AsyncOpenAI] = {}
        self._limits: dict[str, asyncio.Semaphore] = {}
        self._cache_dir: Path | None = settings.cache_dir if settings.llm_cache else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def model_for(self, role: Role) -> tuple[str, str]:
        return parse_model_ref(getattr(self.settings, f"model_{role}"))

    def available(self, role: Role) -> bool:
        provider, _ = self.model_for(role)
        return self.settings.api_key_for(provider) is not None

    def _client(self, provider: str) -> AsyncOpenAI:
        if provider not in self._clients:
            key = self.settings.api_key_for(provider)
            if not key:
                raise LLMNotConfigured(f"{provider.upper()}_API_KEY is not set")
            base_url = PROVIDER_BASE_URLS[provider]
            if provider == "typesafe" and self.settings.typesafe_base_url:
                base_url = self.settings.typesafe_base_url
            # The SDK retries 408/409/429/5xx with exponential backoff and honours Retry-After.
            self._clients[provider] = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=6, timeout=90.0)
            self._limits[provider] = asyncio.Semaphore(self.settings.llm_max_concurrency)
        return self._clients[provider]

    def _cache_path(self, payload: dict[str, Any]) -> Path | None:
        if not self._cache_dir:
            return None
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return self._cache_dir / digest[:2] / f"{digest}.json"

    # ------------------------------------------------------------------ calls
    async def chat(
        self,
        role: Role,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        use_cache: bool = True,
    ) -> ChatResult:
        provider, model = self.model_for(role)
        client = self._client(provider)
        request: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if tools:
            request["tools"] = tools
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        if max_tokens:
            request["max_tokens"] = max_tokens

        cache_path = self._cache_path({"provider": provider, **request}) if use_cache and temperature == 0 else None
        if cache_path and cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            result = ChatResult(**{**data, "cached": True, "latency_ms": 0})
            self.usage.record(role, result)
            return result

        started = time.perf_counter()
        async with self._limits[provider]:
            try:
                resp = await client.chat.completions.create(**request)
            except openai.APIError:
                self.usage.by_role[role].errors += 1
                raise
        choice = resp.choices[0].message
        result = ChatResult(
            content=choice.content or "",
            tool_calls=[
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"} for tc in (choice.tool_calls or []) if tc.type == "function"
            ],
            model=f"{provider}:{model}",
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        self.usage.record(role, result)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in vars(result).items() if k not in {"cached", "latency_ms"}}
            cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return result

    async def structured(self, role: Role, messages: list[dict[str, Any]], schema: type[T], *, max_tokens: int | None = None) -> T:
        """JSON output validated against a Pydantic schema, with one self-repair attempt."""
        result = await self.chat(role, messages, json_mode=True, max_tokens=max_tokens)
        try:
            return schema.model_validate(_loads_lenient(result.content))
        except (ValidationError, json.JSONDecodeError) as err:
            log.info("repairing invalid %s output: %s", schema.__name__, str(err)[:200])
            repair = [
                *messages,
                {"role": "assistant", "content": result.content},
                {
                    "role": "user",
                    "content": f"That output was invalid: {str(err)[:800]}\nReturn ONLY corrected JSON matching the schema.",
                },
            ]
            fixed = await self.chat(role, repair, json_mode=True, max_tokens=max_tokens)
            return schema.model_validate(_loads_lenient(fixed.content))


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def _loads_lenient(text: str) -> Any:
    """Parse JSON that may be wrapped in markdown fences or surrounded by prose."""
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise
