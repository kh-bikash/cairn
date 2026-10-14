"""End-to-end against a real FalkorDB (skipped when none is reachable).

Builds the synthetic company from tests/fixtures.py into a throwaway graph and checks that every
agent tool returns real data. These tests also guard against Cypher shapes FalkorDB evaluates
unreliably (filtered matches after aggregation), which fail silently by returning no rows.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cairn.agent.loop import Agent
from cairn.agent.tools import Tools, dispatch
from cairn.config import Settings
from cairn.derive.expertise import derive_collaboration, derive_expertise
from cairn.extract.drift import DriftReport, check_premise
from cairn.extract.models import ExtractedDecision, ExtractionResult, QuotedClaim, QuoteOnly, RejectedOption
from cairn.extract.pipeline import Report, ground, load_thread, write_decisions
from cairn.graph.client import GraphStore
from cairn.ingest.loader import build_rows, write_rows
from cairn.llm.client import LLM
from cairn.llm.decider import Verdict
from tests.fixtures import api_snapshot, client_snapshot


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        falkordb_host=os.environ.get("CAIRN_FALKORDB_HOST", "localhost"),
        falkordb_port=int(os.environ.get("CAIRN_FALKORDB_PORT", "6379")),
        graph_name=f"cairn_test_{uuid.uuid4().hex[:8]}",
        data_dir=tmp_path,
        repos=["acme/api", "acme/client"],
    )


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[GraphStore]:
    settings = _settings(tmp_path_factory.mktemp("data"))
    s = GraphStore(settings)
    if not s.ping():
        pytest.skip("FalkorDB not reachable; set CAIRN_FALKORDB_PORT")
    write_rows(s, build_rows([api_snapshot(), client_snapshot()]))
    derive_expertise(s)
    derive_collaboration(s, min_weight=0.5)
    _add_decision(s)
    yield s
    s.drop()


def _add_decision(s: GraphStore) -> None:
    thread = load_thread(s, "acme/api#10")
    assert thread is not None and thread.modules == ["acme/api:api/cache"]
    result = ExtractionResult(
        decisions=[
            ExtractedDecision(
                title="Use Redis for the session cache",
                summary="Redis replaces Memcached for sessions.",
                category="architecture",
                statement=QuotedClaim(text="t", quote="We decided to use Redis for the session cache instead of Memcached.", segment="S1"),
                premises=[
                    QuotedClaim(
                        text="Sessions must survive deploys",
                        quote="Redis supports persistence, which we need for warm restarts after deploys.",
                        segment="S1",
                    )
                ],
                rejected=[
                    RejectedOption(
                        option="Memcached",
                        text="No cross-zone replication",
                        quote="Memcached was rejected because it cannot replicate across zones.",
                        segment="S1",
                    )
                ],
                systems=["acme/api:api/cache"],
                deciders=["alice", "bob"],
            )
        ]
    )
    grounded = ground(thread, result, Report())
    assert len(grounded) == 1
    write_decisions(s, thread, grounded)


class _FakeDecider:
    async def decide(self, question: str, context: str, options: list[str]) -> Verdict:
        return Verdict("contradicts" if "stateless" in context else "unrelated", 0.9, "fake")


class _FakeLLM:
    async def structured(self, role: str, messages: list[dict[str, Any]], schema: type, **_: Any) -> QuoteOnly:
        return QuoteOnly(quote="the cache no longer needs persistence across deploys", explanation="Sessions became stateless.")


async def test_drift_detects_contradicting_later_pr(store: GraphStore) -> None:
    premise = store.records(
        "MATCH (d:Decision)-[:BECAUSE]->(p:Premise) RETURN p.uid AS uid, p.text AS text, d.uid AS decision, d.title AS title, d.ts AS ts, d.repo AS repo"
    )[0]
    report = DriftReport()
    await check_premise(store, _FakeLLM(), _FakeDecider(), premise, report)  # type: ignore[arg-type]
    assert report.challenges == 1, report
    assert store.ro_query("MATCH (:Source {uid: 'acme/api#14'})-[c:CHALLENGES]->(:Premise) RETURN c.verdict")[0][0] == "contradicts"


def test_graph_shape(store: GraphStore) -> None:
    counts = store.counts()
    assert counts["nodes"]["Person"] == 3
    assert counts["relationships"]["DEPENDS_ON"] == 1
    assert counts["relationships"]["KNOWS_ABOUT"] > 0
    assert counts["relationships"]["OWNS"] >= 2
    store.ensure_schema()  # idempotent on an existing graph


@pytest.mark.parametrize(
    ("name", "args", "check"),
    [
        ("list_systems", {}, lambda d: any(s["key"] == "acme/api:api/cache" and s["prs"] == 2 for s in d["systems"])),
        ("search", {"query": "session cache"}, lambda d: any(s["uid"] == "acme/api#10" for s in d["sources"])),
        (
            "why",
            {"topic": "session cache redis"},
            lambda d: (
                d["decisions"][0]["premises"][0]["source"] == "acme/api#10"
                and d["decisions"][0]["rejected_alternatives"][0]["option"] == "Memcached"
                and set(d["decisions"][0]["deciders"]) == {"alice", "bob"}
            ),
        ),
        ("who_knows", {"system": "api/cache"}, lambda d: d["experts"][0]["handle"] in {"alice", "carol"} and d["experts"][0]["recent_work"]),
        ("who_knows", {"system": "acme/api"}, lambda d: len(d["experts"]) == 3),
        ("impact", {"system": "acme/client"}, lambda d: d["depended_on_by"][0]["repo"] == "acme/api" and d["contacts"]),
        ("bus_factor", {}, lambda d: "at_risk_modules" in d),
        ("person_profile", {"handle": "bob"}, lambda d: any(e["system"].startswith("acme/") for e in d["expertise"]) and d["decisions"]),
        (
            "timeline",
            {"system": "api/cache", "since": "2026-01-01"},
            lambda d: [e["uid"] for e in d["events"]] == ["acme/api#14", "acme/api#10"],
        ),
        ("connect", {"a": "carol", "b": "acme/client"}, lambda d: d["path"] and d["path"][-1]["id"] == "acme/client"),
        ("teams", {}, lambda d: d["teams"] and d["teams"][0]["focus_systems"]),
        ("details", {"uid": "acme/api#10"}, lambda d: d["decisions"][0]["title"] == "Use Redis for the session cache" and d["links"]),
        ("details", {"uid": "acme/api#10::d1"}, lambda d: d["decision"]["premises"]),
        ("suggest_owners", {"text": "The session cache loses data after deploys"}, lambda d: d["systems"][0]["system"] == "acme/api:api/cache"),
    ],
)
def test_tools_return_real_data(store: GraphStore, name: str, args: dict[str, Any], check: Any) -> None:
    result = dispatch(Tools(store), name, args)
    assert check(result.data), result.data
    sg = result.subgraph.to_dict()
    if name not in {"bus_factor"}:
        assert sg["nodes"], f"{name} returned no subgraph for the UI"


def test_drift_report_after_detection(store: GraphStore) -> None:
    # Runs after test_drift_detects_contradicting_later_pr in file order.
    rows = dispatch(Tools(store), "drift_report", {}).data["drifted"]
    assert rows and rows[0]["challenged_by"] == "acme/api#14"
    why = dispatch(Tools(store), "why", {"topic": "session cache"}).data["decisions"][0]
    assert why["drift_detected"] and why["premises"][0]["challenged_by"]


async def test_agent_router_mode_answers_with_citations(store: GraphStore, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    agent = Agent(store, LLM(settings), settings)
    events = [e async for e in agent.run("Why did we choose Redis for the session cache?")]
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["mode"] == "router"
    assert "Use Redis for the session cache" in answer["text"]
    assert any(c["uid"] == "acme/api#10" for c in answer["citations"])
    assert store.ro_query("MATCH (a:Answer {uid: $u})-[:USED]->(n) RETURN count(n)", {"u": answer["answer_uid"]})[0][0] > 0


@pytest.mark.parametrize(
    ("question", "call"),
    [
        ("Who knows the cache best?", ("who_knows", {"system": "acme/api:api/cache"})),
        ("Who knows billing in api?", ("who_knows", {"system": "acme/api:api/billing"})),
        ("What breaks if acme/client goes down?", ("impact", {"system": "acme/client"})),
        ("What happens if bob leaves?", ("person_profile", {"handle": "bob"})),
        ("Why did we choose Redis?", ("why", {"topic": "Redis"})),
    ],
)
def test_router_resolves_entities(store: GraphStore, question: str, call: tuple[str, dict[str, Any]]) -> None:
    from cairn.agent.router import _CATALOG, route

    _CATALOG.clear()  # catalog is cached per store; tests share one store across graphs
    assert route(Tools(store), question).calls == [call]
