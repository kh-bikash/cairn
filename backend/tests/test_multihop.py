"""Multi-hop chains: routing is pure, the chains run against a real FalkorDB (skipped when none is reachable)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from cairn.agent.router import intent_of
from cairn.agent.tools import Tools, dispatch
from cairn.config import Settings
from cairn.graph.client import GraphStore
from cairn.ingest.loader import build_rows, write_rows
from tests.fixtures import api_snapshot, client_snapshot


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Which runbook is out of date, and why?", "stale_runbook"),
        ("Which of our runbooks are stale?", "stale_runbook"),
        ("Which decisions did people who have since left make, and what still depends on them?", "departed"),
        ("Which of our projects adopted the hiredis extra for faster reply parsing, and who drove it?", "trace"),
        ("Which earlier fix does the 1.7.0 release refer to, and what broke?", "trace"),
        # Everyday questions keep their old routing.
        ("Why did we move the events pipeline to MSK?", "why"),
        ("Who knows the payments service best?", "who"),
        ("What depends on the events pipeline?", "impact"),
        ("What knowledge did we lose when Priya left?", "risk"),
    ],
)
def test_multi_hop_questions_get_their_own_route(question: str, intent: str) -> None:
    assert intent_of(question) == intent


@pytest.fixture(scope="module")
def tools(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Tools]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        falkordb_host=os.environ.get("CAIRN_FALKORDB_HOST", "localhost"),
        falkordb_port=int(os.environ.get("CAIRN_FALKORDB_PORT", "6379")),
        graph_name=f"cairn_test_{uuid.uuid4().hex[:8]}",
        data_dir=Path(tmp_path_factory.mktemp("data")),
        repos=["acme/api", "acme/client"],
    )
    store = GraphStore(settings)
    if not store.ping():
        pytest.skip("FalkorDB not reachable; set CAIRN_FALKORDB_PORT")
    write_rows(store, build_rows([api_snapshot(), client_snapshot()]))
    yield Tools(store)
    store.drop()


@pytest.mark.parametrize("name", ["departed_decisions", "stale_runbooks"])
def test_chains_always_report_their_steps(tools: Tools, name: str) -> None:
    result = dispatch(tools, name, {})
    assert isinstance(result.data["trace"], list) and result.data["trace"]
    assert [s["n"] for s in result.data["trace"]] == list(range(1, len(result.data["trace"]) + 1))


def test_trace_topic_groups_by_repo_and_records_steps(tools: Tools) -> None:
    result = dispatch(tools, "trace_topic", {"topic": "cache"})
    assert result.data["trace"][0]["title"].startswith("Search for")
    assert all("repo" in item for item in result.data["items"])
