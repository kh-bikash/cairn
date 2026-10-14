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

    async def _create(self, **request: Any) -> SimpleNamespace:
        self.requests.append(request)
        return self._respond(request)


def _wire(llm: LLM, respond: Callable[[dict[str, Any]], SimpleNamespace]) -> FakeProvider:
    fake = FakeProvider(respond)
    llm._clients["groq"] = fake  # type: ignore[assignment]
    llm._limits["groq"] = asyncio.Semaphore(4)
    return fake


def _settings(tmp_path: Path, **kw: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        GROQ_API_KEY="test",  # type: ignore[arg-type]
        data_dir=tmp_path,
        model_decide="groq:small",
        model_extract="groq:big",
        model_agent="groq:big",
        **kw,
    )


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
