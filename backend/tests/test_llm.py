from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from cairn.config import Settings
from cairn.llm.client import LLM, LLMNotConfigured, parse_model_ref
from cairn.llm.decider import LLMDecider


class Pick(BaseModel):
    answer: str
    confidence: float


def _completion(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeProvider:
    """Stands in for AsyncOpenAI at the LLM boundary (the SDK's own HTTP stack isn't mockable with respx)."""

    def __init__(self, respond: Callable[[dict[str, Any]], SimpleNamespace]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._respond = respond
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_: Any) -> FakeProvider:
        return self

    async def _create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return self._respond(request)


def _wire(llm: LLM, respond: Callable[[dict[str, Any]], SimpleNamespace]) -> FakeProvider:
    fake = FakeProvider(respond)
    llm._clients["groq"] = fake  # type: ignore[assignment]
    llm._limits["groq"] = asyncio.Semaphore(4)
    return fake


def _settings(tmp_path: Path, **kw: object) -> Settings:
    fields: dict[str, object] = {"model_decide": "groq:small", "model_extract": "groq:big", "model_agent": "groq:big", **kw}
    return Settings(_env_file=None, GROQ_API_KEY="test", data_dir=tmp_path, **fields)  # type: ignore[call-arg, arg-type]


def test_parse_model_ref() -> None:
    assert parse_model_ref("gemini:gemini-2.5-flash") == ("gemini", "gemini-2.5-flash")
    with pytest.raises(ValueError):
        parse_model_ref("nope")


async def test_structured_caches_identical_calls(tmp_path: Path) -> None:
    llm = LLM(_settings(tmp_path))
    fake = _wire(llm, lambda _: _completion('{"answer": "yes", "confidence": 0.9}'))
    msgs = [{"role": "user", "content": "q"}]
    first = await llm.structured("decide", msgs, Pick)
    second = await llm.structured("decide", msgs, Pick)
    assert first == second == Pick(answer="yes", confidence=0.9)
    assert len(fake.requests) == 1
    assert llm.usage.snapshot()["decide"]["cache_hits"] == 1


async def test_structured_repairs_invalid_json(tmp_path: Path) -> None:
    replies = iter([_completion("Sure! here you go: {answer: yes}"), _completion('```json\n{"answer": "no", "confidence": 0.4}\n```')])
    llm = LLM(_settings(tmp_path, llm_cache=False))
    fake = _wire(llm, lambda _: next(replies))
    out = await llm.structured("decide", [{"role": "user", "content": "q"}], Pick)
    assert out == Pick(answer="no", confidence=0.4)
    assert fake.requests[0]["response_format"] == {"type": "json_object"}
    assert "invalid" in fake.requests[1]["messages"][-1]["content"]


async def test_decider_escalates_low_confidence(tmp_path: Path) -> None:
    def respond(request: dict[str, Any]) -> SimpleNamespace:
        small = request["model"] == "small"
        return _completion('{"answer": "yes", "confidence": 0.3}' if small else '{"answer": "NO", "confidence": 0.95}')

    llm = LLM(_settings(tmp_path, llm_cache=False))
    _wire(llm, respond)
    decider = LLMDecider(llm, threshold=0.7)
    verdict = await decider.decide("q", "ctx", ["yes", "no"])
    assert verdict.answer == "no" and verdict.escalated and verdict.decided_by == "groq:big"


async def test_missing_key_raises(tmp_path: Path) -> None:
    llm = LLM(Settings(_env_file=None, data_dir=tmp_path, model_agent="gemini:x"))  # type: ignore[call-arg]
    assert not llm.available("agent")
    with pytest.raises(LLMNotConfigured):
        await llm.chat("agent", [{"role": "user", "content": "hi"}])


def test_gemini_gets_thought_signatures_and_others_do_not() -> None:
    from cairn.llm.client import _adapt_messages

    signed = {"google": {"thought_signature": "abc"}}
    msgs = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "why", "arguments": "{}"}, "extra_content": signed},
                {"id": "2", "type": "function", "function": {"name": "who_knows", "arguments": "{}"}},
            ],
        },
    ]
    gem = _adapt_messages("gemini", msgs)[1]["tool_calls"]
    assert gem[0]["extra_content"] == signed
    assert gem[1]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert all("extra_content" not in tc for tc in _adapt_messages("groq", msgs)[1]["tool_calls"])


async def test_chain_fails_over_on_overload(tmp_path: Path) -> None:
    import httpx2
    import openai

    llm = LLM(_settings(tmp_path, llm_cache=False, model_decide="groq:busy,groq:spare"))

    def respond(request: dict[str, Any]) -> SimpleNamespace:
        if request["model"] == "busy":
            response = httpx2.Response(503, request=httpx2.Request("POST", "https://example.invalid"))
            raise openai.InternalServerError("overloaded", response=response, body=None)
        return _completion('{"answer": "yes", "confidence": 1.0}')

    fake = _wire(llm, respond)
    out = await llm.structured("decide", [{"role": "user", "content": "q"}], Pick)
    assert out.answer == "yes"
    assert [r["model"] for r in fake.requests] == ["busy", "spare"]


async def test_cache_replays_without_api_key(tmp_path: Path) -> None:
    keyed = LLM(_settings(tmp_path))
    _wire(keyed, lambda _: _completion('{"answer": "yes", "confidence": 0.8}'))
    msgs = [{"role": "user", "content": "q"}]
    await keyed.structured("decide", msgs, Pick)
    keyless = LLM(Settings(_env_file=None, data_dir=tmp_path, model_decide="groq:small"))  # type: ignore[call-arg]
    assert (await keyless.structured("decide", msgs, Pick)).confidence == 0.8


async def test_daily_quota_trips_circuit_breaker(tmp_path: Path) -> None:
    import httpx2
    import openai

    from cairn.llm.client import LLMQuotaExhausted

    llm = LLM(_settings(tmp_path, llm_cache=False, model_decide="groq:capped,groq:spare"))
    calls: list[str] = []

    def respond(request: dict[str, Any]) -> SimpleNamespace:
        calls.append(request["model"])
        response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.invalid"))
        raise openai.RateLimitError("quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier", response=response, body=None)

    _wire(llm, respond)
    with pytest.raises(LLMQuotaExhausted):
        await llm.chat("decide", [{"role": "user", "content": "q"}])
    assert calls == ["capped", "spare"], "daily quota fails over immediately, without retries"
    assert llm.exhausted == {"groq:capped", "groq:spare"}
    with pytest.raises(LLMQuotaExhausted):
        await llm.chat("decide", [{"role": "user", "content": "again"}])
    assert len(calls) == 2, "benched models are not called again until their quota resets"


async def test_benched_model_comes_back_after_its_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx2
    import openai

    from cairn.llm import client as llm_client

    llm = LLM(_settings(tmp_path, llm_cache=False, model_decide="groq:capped"))
    state = {"capped": True}

    def respond(request: dict[str, Any]) -> SimpleNamespace:
        if state["capped"]:
            response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.invalid"))
            raise openai.RateLimitError(
                "Rate limit reached for model on tokens per day (TPD): Limit 200000. Please try again in 7m12.5s.", response=response, body=None
            )
        return _completion("ok")

    _wire(llm, respond)
    with pytest.raises(llm_client.LLMQuotaExhausted):
        await llm.chat("decide", [{"role": "user", "content": "q"}])
    assert llm.exhausted == {"groq:capped"}
    assert 430 < llm._benched["groq:capped"] - llm_client.time.monotonic() <= 438, "bench length follows Groq's reset time"

    clock = llm_client.time.monotonic() + 600
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: clock)
    state["capped"] = False
    assert llm.exhausted == set()
    assert (await llm.chat("decide", [{"role": "user", "content": "q2"}])).content == "ok"


async def test_per_minute_providers_are_paced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cairn.llm import client as llm_client

    llm = LLM(_settings(tmp_path, llm_cache=False))
    monkeypatch.setitem(llm_client.PROVIDER_RPM, "nvidia", 120)  # one request start every 0.5 s
    started: list[float] = []

    async def go() -> None:
        await llm._pace("nvidia")
        started.append(llm_client.time.monotonic())

    await asyncio.gather(*(go() for _ in range(3)))
    gaps = [b - a for a, b in zip(sorted(started), sorted(started)[1:], strict=False)]
    assert all(g >= 0.45 for g in gaps), gaps
    t0 = llm_client.time.monotonic()
    await llm._pace("groq")  # providers without a per-minute cap are never delayed
    assert llm_client.time.monotonic() - t0 < 0.05


async def test_excluded_model_never_answers(tmp_path: Path) -> None:
    from cairn.llm.client import LLMNotConfigured

    llm = LLM(_settings(tmp_path, llm_cache=False, model_decide="groq:judge,groq:checker"))
    calls: list[str] = []

    def respond(request: dict[str, Any]) -> SimpleNamespace:
        calls.append(request["model"])
        return _completion("ok")

    _wire(llm, respond)
    await llm.chat("decide", [{"role": "user", "content": "q"}], exclude={"groq:judge"})
    assert calls == ["checker"], "the excluded model is skipped even though it is first in the chain"
    solo = LLM(_settings(tmp_path, llm_cache=False, model_decide="groq:judge"))
    _wire(solo, respond)
    with pytest.raises(LLMNotConfigured):
        await solo.chat("decide", [{"role": "user", "content": "q"}], exclude={"groq:judge"})


def test_same_model_across_providers() -> None:
    from cairn.llm.client import same_model

    assert same_model("nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b", "openrouter:nvidia/nemotron-3.5-super-vl:free")
    assert same_model("groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-120b")
    assert not same_model("groq:openai/gpt-oss-20b", "groq:openai/gpt-oss-120b")
    assert not same_model("nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b", "groq:openai/gpt-oss-120b")


def test_cloudflare_endpoint_and_daily_allocation(tmp_path: Path) -> None:
    import httpx2
    import openai

    from cairn.llm.client import _is_daily_quota

    llm = LLM(_settings(tmp_path, cloudflare_api_key="t", cloudflare_account_id="acc123"))
    assert str(llm._client("cloudflare").base_url).rstrip("/").endswith("/accounts/acc123/ai/v1")
    response = httpx2.Response(429, request=httpx2.Request("POST", "https://example.invalid"))
    used_up = openai.RateLimitError("4006: you have used up your daily free allocation of 10,000 neurons", response=response, body=None)
    per_minute = openai.RateLimitError("Rate limit reached: 30 requests per minute", response=response, body=None)
    assert _is_daily_quota(used_up) and not _is_daily_quota(per_minute)


def test_cloudflare_requests_are_adapted() -> None:
    from cairn.llm.client import LLM as _LLM
    from cairn.llm.client import _adapt_messages

    msgs = [{"role": "system", "content": [{"type": "text", "text": "be brief"}]}, {"role": "assistant", "content": None, "tool_calls": []}]
    adapted = _adapt_messages("cloudflare", msgs)
    assert [m["content"] for m in adapted] == ["be brief", ""]
    req = _LLM._request("cloudflare", "@cf/openai/gpt-oss-120b", [], None, True, 0.0, 60)
    assert "max_tokens" not in req and req["extra_body"] == {"reasoning_effort": "low"}
