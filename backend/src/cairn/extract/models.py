"""Schemas for LLM extraction output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["architecture", "dependency", "api", "data", "performance", "security", "process", "product", "testing", "other"]


class QuotedClaim(BaseModel):
    text: str = Field(description="The claim restated as one standalone sentence.")
    quote: str = Field(description="Verbatim excerpt (<= 300 chars) from the cited segment that supports the claim.")
    segment: str = Field(description="Segment label, e.g. 'S2'.")

    @field_validator("segment")
    @classmethod
    def _norm_segment(cls, v: str) -> str:
        return v.strip().strip("[]").upper()


class RejectedOption(QuotedClaim):
    option: str = Field(description="The alternative that was considered and not chosen.")


class ExtractedDecision(BaseModel):
    title: str = Field(description="Imperative, <= 12 words, e.g. 'Use hiredis parser for server replies'.")
    summary: str = Field(description="2-3 sentences: what was decided and the context.")
    category: Category = "other"
    statement: QuotedClaim = Field(description="Where the decision itself is stated.")
    premises: list[QuotedClaim] = Field(default_factory=list, description="The reasons the decision was made.")
    rejected: list[RejectedOption] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list, description="Affected module keys, chosen from the provided list.")
    deciders: list[str] = Field(default_factory=list, description="GitHub handles of people who made or approved it.")

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, v: object) -> object:
        allowed = set(Category.__args__)  # type: ignore[attr-defined]
        return v if isinstance(v, str) and v in allowed else "other"


class ExtractionResult(BaseModel):
    decisions: list[ExtractedDecision] = Field(default_factory=list)


class QuoteOnly(BaseModel):
    quote: str
    explanation: str = ""


class ChallengeCheck(BaseModel):
    """Second-opinion check for drift: the model must commit to a verdict, not just produce a quote."""

    challenges: bool = Field(description="True only if the later text shows the premise is false or no longer holds.")
    quote: str = ""
    explanation: str = ""
