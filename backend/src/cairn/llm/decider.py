"""Bounded decisions: pick one answer from a fixed set, with a confidence.

This is the "System 1" layer used during ingestion (triage, entity resolution, drift checks).
The default implementation prompts a small, fast model for JSON; a TypeSafe JEV adapter can
replace it without touching callers, since JEV natively returns one of the listed options.
Low-confidence answers escalate to the larger extraction model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from cairn.llm.client import LLM, Role

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verdict:
    answer: str
    confidence: float
    decided_by: str  # model ref that produced the final answer
    escalated: bool = False


class Decider(Protocol):
    async def decide(self, question: str, context: str, options: list[str]) -> Verdict: ...


class _Choice(BaseModel):
    answer: str
    confidence: float = Field(ge=0.0, le=1.0)


_SYSTEM = (
    "You make one narrow classification decision. Read the context, then choose exactly one option. "
    'Respond with JSON only: {"answer": <one of the options, verbatim>, "confidence": <0.0-1.0>}. '
    "Confidence is your probability that the answer is correct; use < 0.7 when the context is ambiguous."
)


class LLMDecider:
    def __init__(self, llm: LLM, *, threshold: float) -> None:
        self.llm = llm
        self.threshold = threshold

    async def decide(self, question: str, context: str, options: list[str]) -> Verdict:
        verdict = await self._ask("decide", question, context, options)
        if verdict.confidence >= self.threshold or not self.llm.available("extract"):
            return verdict
        escalated = await self._ask("extract", question, context, options)
        return Verdict(escalated.answer, escalated.confidence, escalated.decided_by, escalated=True)

    async def _ask(self, role: Role, question: str, context: str, options: list[str]) -> Verdict:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Question: {question}\nOptions: {options}\n\nContext:\n{context}"},
        ]
        choice = await self.llm.structured(role, messages, _Choice, max_tokens=60)
        answer = _match_option(choice.answer, options)
        if answer is None:
            log.info("decider returned out-of-set answer %r; treating as lowest-confidence %r", choice.answer, options[-1])
            return Verdict(options[-1], 0.0, ":".join(self.llm.model_for(role)))
        return Verdict(answer, choice.confidence, ":".join(self.llm.model_for(role)))


def _match_option(answer: str, options: list[str]) -> str | None:
    norm = answer.strip().strip("\"'").lower()
    for opt in options:
        if opt.lower() == norm:
            return opt
    return None
