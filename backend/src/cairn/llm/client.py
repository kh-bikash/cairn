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
from collections.abc import Collection
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


class LLMQuotaExhausted(LLMNotConfigured):
    """Every model in a role's chain has hit its daily quota; callers treat it like 'no LLM available'."""


_MAX_ATTEMPTS = 3
_MAX_WAIT_S = 65.0
_RETRY_DELAY = re.compile(r"retry(?:Delay|[ _-]after)['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s?", re.I)


_QUOTA_COOLDOWN_S = 15 * 60.0  # when a provider doesn't say when the quota resets, look again after this long
_TRY_AGAIN = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?", re.I)


def _is_daily_quota(exc: openai.RateLimitError) -> bool:
    text = str(exc)
    low = text.lower()
    # Gemini: "...PerDay...", Groq: "tokens per day (TPD)", Cloudflare: "used up your daily free allocation".
    return "PerDay" in text or "per day" in low or "daily free allocation" in low or "daily limit" in low


def _quota_reset_s(exc: openai.RateLimitError) -> float:
    """Seconds until a daily quota frees up. Groq's limits roll and say "try again in 7m12.5s"; Gemini's don't say."""
    m = _TRY_AGAIN.search(str(exc))
    if m and any(m.groups()):
        h, mins, secs = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mins * 60 + secs + 5.0
    return _retry_delay(exc) or _QUOTA_COOLDOWN_S


def _retry_delay(exc: openai.RateLimitError) -> float | None:
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = _RETRY_DELAY.search(str(exc))
    return float(m.group(1)) if m else None


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
            msg["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    # Gemini thought signatures must be echoed back on the next turn.
                    **({"extra_content": tc["extra_content"]} if tc.get("extra_content") else {}),
                }
                for tc in self.tool_calls
            ]
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


# Free tiers limited per minute rather than per day: requests are paced to stay under the limit instead of
# tripping it (a 429 from a sub-second model just wastes the call and, at the end of a chain, the answer).
PROVIDER_RPM: dict[str, int] = {"nvidia": 36, "openrouter": 18}


def same_model(a: str, b: str) -> bool:
    """Whether two refs are the same model behind different providers: 'nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b'
    and 'openrouter:nvidia/nemotron-3.5-super-vl:free' are one model, so one must not confirm the other's verdict."""

    def base(ref: str) -> str:
        return ref.partition(":")[2].rsplit("/", 1)[-1].split(":")[0].lower()

    x, y = base(a), base(b)
    return bool(x and y) and (x.startswith(y) or y.startswith(x))


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
        # Circuit breaker: "provider:model" -> monotonic time when its daily quota is expected back. A long-running
        # server must recover on its own once the quota resets, so models are benched, never banned.
        self._benched: dict[str, float] = {}
        self._pace_locks: dict[str, asyncio.Lock] = {}
        self._next_slot: dict[str, float] = {}
        self._cache_dir: Path | None = settings.cache_dir if settings.llm_cache else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def model_chain(self, role: Role) -> list[tuple[str, str]]:
        """A role may list fallbacks: 'gemini:gemini-2.5-flash,gemini:gemini-2.5-flash-lite'."""
        refs = str(getattr(self.settings, f"model_{role}")).split(",")
        return [parse_model_ref(r.strip()) for r in refs if r.strip()]

    def model_for(self, role: Role) -> tuple[str, str]:
        return self.model_chain(role)[0]

    @property
    def exhausted(self) -> set[str]:
        """Models currently benched for quota."""
        now = time.monotonic()
        return {ref for ref, until in self._benched.items() if until > now}

    def available(self, role: Role) -> bool:
        return any(self.settings.api_key_for(p) for p, _ in self.model_chain(role))

    def _client(self, provider: str) -> AsyncOpenAI:
        if provider not in self._clients:
            key = self.settings.api_key_for(provider)
            if not key:
                raise LLMNotConfigured(f"{provider.upper()}_API_KEY is not set")
            base_url = PROVIDER_BASE_URLS[provider]
            if provider == "typesafe" and self.settings.typesafe_base_url:
                base_url = self.settings.typesafe_base_url
            if provider == "cloudflare":
                if not self.settings.cloudflare_account_id:
                    raise LLMNotConfigured("CLOUDFLARE_ACCOUNT_ID is not set")
                base_url = base_url.format(account=self.settings.cloudflare_account_id)
            # The SDK retries 408/409/429/5xx with exponential backoff and honours Retry-After.
            self._clients[provider] = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=45.0)  # retries/failover are handled in chat()
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
        exclude: Collection[str] = (),
    ) -> ChatResult:
        """`exclude` lists "provider:model" refs that must not answer (e.g. the model whose verdict is being checked)."""
        chain = [(p, m) for p, m in self.model_chain(role) if not any(same_model(f"{p}:{m}", x) for x in exclude)]
        if not chain:
            raise LLMNotConfigured(f"no model for role {role!r} besides {', '.join(sorted(exclude))}")
        requests = [(p, m, self._request(p, m, messages, tools, json_mode, temperature, max_tokens)) for p, m in chain]
        cacheable = use_cache and temperature == 0

        # Cache first, for every model in the chain: a replay needs no API key and no quota.
        if cacheable:
            for provider, _, request in requests:
                path = self._cache_path({"provider": provider, **request})
                if path and path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    result = ChatResult(**{**data, "cached": True, "latency_ms": 0})
                    self.usage.record(role, result)
                    return result

        live = [(p, m, r) for p, m, r in requests if self.settings.api_key_for(p)]
        if not live:
            raise LLMNotConfigured(f"no API key for any model configured for role {role!r}")
        benched = self.exhausted
        available = [(p, m, r) for p, m, r in live if f"{p}:{m}" not in benched]
        if not available:
            raise LLMQuotaExhausted(f"every model for role {role!r} has used its daily quota: {', '.join(sorted(benched))}")
        last_error: Exception | None = None
        for index, (provider, model, request) in enumerate(available):
            is_last = index == len(available) - 1
            # Retries are ours, not the SDK's: a daily-quota 429 must fail over at once, not back off for a minute.
            client = self._client(provider).with_options(max_retries=0)
            for attempt in range(_MAX_ATTEMPTS):
                try:
                    result = await self._call(role, provider, model, client, request)
                except openai.RateLimitError as exc:
                    last_error = exc
                    if _is_daily_quota(exc):
                        wait = _quota_reset_s(exc)
                        self._benched[f"{provider}:{model}"] = time.monotonic() + wait
                        log.warning("%s:%s used its daily quota; skipping it for %.0f min", provider, model, wait / 60)
                        break
                    delay = _retry_delay(exc)
                    if delay is None and is_last:
                        delay = 6.0 * (attempt + 1)  # per-minute limit with no Retry-After: nothing to fail over to, so wait
                    if delay is None or delay > _MAX_WAIT_S or attempt == _MAX_ATTEMPTS - 1:
                        log.warning("%s:%s rate limited; trying next model", provider, model)
                        break
                    await asyncio.sleep(delay)
                    continue
                except openai.BadRequestError as exc:
                    # Groq validates JSON mode server-side and rejects malformed output; another model may do better.
                    if "json_validate_failed" not in str(exc) and "failed_generation" not in str(exc):
                        raise
                    last_error = exc
                    log.info("%s:%s produced invalid JSON; trying next model", provider, model)
                    break
                except (openai.InternalServerError, openai.APIConnectionError, openai.APITimeoutError) as exc:
                    last_error = exc
                    # Overloaded: switch models at once if we can; only the last model in the chain waits it out.
                    if not is_last or attempt == _MAX_ATTEMPTS - 1:
                        log.warning("%s:%s unavailable (%s); trying next model", provider, model, type(exc).__name__)
                        break
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                if cacheable and (path := self._cache_path({"provider": provider, **request})):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = {k: v for k, v in vars(result).items() if k not in {"cached", "latency_ms"}}
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return result
        assert last_error is not None
        if all(f"{p}:{m}" in self.exhausted for p, m, _ in available):
            raise LLMQuotaExhausted(f"every model for role {role!r} has used its daily quota") from last_error
        raise last_error

    @staticmethod
    def _request(
        provider: str,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        json_mode: bool,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"model": model, "messages": _adapt_messages(provider, messages), "temperature": temperature}
        if tools:
            request["tools"] = tools
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        # Gemini "thinking" tokens count against max_tokens and can leave the visible answer empty; Cloudflare's
        # gpt-oss likewise reasons inside the budget, so it gets low reasoning effort and no hard cap instead.
        if max_tokens and provider not in {"gemini", "cloudflare"}:
            request["max_tokens"] = max_tokens
        if provider == "cloudflare":
            request["extra_body"] = {"reasoning_effort": "low"}
        # Nemotron reasons before answering by default, which can spend the whole token budget and leave the answer
        # empty. Cairn needs the answer (tool calls, JSON), so reasoning is switched off: same model, sub-second replies.
        if "nemotron" in model:
            if provider == "nvidia":
                request["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
            elif provider == "openrouter":
                request["extra_body"] = {"reasoning": {"enabled": False}}
        return request

    async def _pace(self, provider: str) -> None:
        """Space request starts so a per-minute limit is never exceeded, across every caller in this process."""
        rpm = PROVIDER_RPM.get(provider)
        if not rpm:
            return
        lock = self._pace_locks.setdefault(provider, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            slot = max(now, self._next_slot.get(provider, 0.0))
            self._next_slot[provider] = slot + 60.0 / rpm
        if slot > now:
            await asyncio.sleep(slot - now)

    async def _call(self, role: Role, provider: str, model: str, client: AsyncOpenAI, request: dict[str, Any]) -> ChatResult:
        started = time.perf_counter()
        async with self._limits[provider]:
            await self._pace(provider)
            try:
                resp = await client.chat.completions.create(**request)
            except openai.APIError:
                self.usage.by_role[role].errors += 1
                raise
        choice = resp.choices[0].message
        result = ChatResult(
            content=choice.content or "",
            tool_calls=[
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                    "extra_content": (tc.model_extra or {}).get("extra_content"),
                }
                for tc in (choice.tool_calls or [])
                if tc.type == "function"
            ],
            model=f"{provider}:{model}",
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=getattr(resp.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(resp.usage, "completion_tokens", 0) or 0,
        )
        self.usage.record(role, result)
        return result

    async def structured(self, role: Role, messages: list[dict[str, Any]], schema: type[T], *, max_tokens: int | None = None, exclude: Collection[str] = ()) -> T:
        """JSON output validated against a Pydantic schema, with one self-repair attempt."""
        result = await self.chat(role, messages, json_mode=True, max_tokens=max_tokens, exclude=exclude)
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
            fixed = await self.chat(role, repair, json_mode=True, max_tokens=max_tokens, exclude=exclude)
            return schema.model_validate(_loads_lenient(fixed.content))


# Google's documented placeholder for function calls the current model didn't sign (e.g. made by a fallback model).
_SKIP_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}


def _flat_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content)


def _adapt_messages(provider: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini requires a thought signature on every prior function call; other providers reject the field.
    Cloudflare's schema wants every message content to be a plain string (no null, no content-part lists)."""
    if provider == "cloudflare":
        messages = [{**m, "content": _flat_text(m.get("content"))} for m in messages]
    if not any(m.get("tool_calls") for m in messages):
        return messages
    out = []
    for m in messages:
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                tc = {k: v for k, v in tc.items() if k != "extra_content"}
                if provider == "gemini":
                    original = next((c.get("extra_content") for c in m["tool_calls"] if c.get("id") == tc.get("id")), None)
                    tc["extra_content"] = original or _SKIP_SIGNATURE
                calls.append(tc)
            m = {**m, "tool_calls": calls}
        out.append(m)
    return out


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
