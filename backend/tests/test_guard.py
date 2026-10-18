"""Decision Guard: pure-logic tests with a fake graph (no FalkorDB needed)."""

from __future__ import annotations

from typing import Any

import pytest

from cairn.agent.tools import ToolError, Tools
from cairn.workflows import decision_guard


class FakeStore:
    def records(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [{"uid": "d1"}]


def make_tools(monkeypatch: pytest.MonkeyPatch, *, drift: bool = False) -> Tools:
    detail = {
        "uid": "d1",
        "title": "Use Kafka for the events pipeline",
        "summary": "Events move over Kafka with ordered partitions",
        "date": "2025-03-02",
        "deciders": ["priya"],
        "premises": [{"claim": "Kafka keeps ordering guarantees for events"}],
        "drift_detected": drift,
    }
    monkeypatch.setattr(Tools, "_decision_detail", lambda self, uid, sg, ev: detail)
    monkeypatch.setattr("cairn.agent.router.find_systems", lambda tools, text: ["acme/events"])
    return Tools(FakeStore())  # type: ignore[arg-type]


def test_reversal_is_flagged_for_review(monkeypatch: pytest.MonkeyPatch) -> None:
    result = decision_guard(make_tools(monkeypatch), "Replace Kafka with a cron job for events pipeline")
    assert result.data["verdict"] == "review"
    assert result.data["findings"][0]["level"] == "conflict"
    assert "@" not in result.data["draft"]  # never pings anyone


def test_related_change_is_only_a_heads_up(monkeypatch: pytest.MonkeyPatch) -> None:
    result = decision_guard(make_tools(monkeypatch), "Add metrics to the Kafka events pipeline")
    assert result.data["verdict"] == "heads_up"
    assert result.data["findings"][0]["level"] == "respect"


def test_unrelated_change_stays_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    result = decision_guard(make_tools(monkeypatch), "Fix typo in billing page")
    assert result.data["verdict"] == "clear"
    assert result.data["findings"] == []


def test_empty_change_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ToolError):
        decision_guard(make_tools(monkeypatch), "   ")


def test_words_that_only_name_the_system_do_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    # "events" is the system's own name, so it alone doesn't tie this change to the Kafka decision.
    result = decision_guard(make_tools(monkeypatch), "Bump the font size on the events dashboard")
    assert result.data["verdict"] == "clear"
