from __future__ import annotations

import pytest

from cairn.agent.loop import Ledger, clean_citations, tool_payload
from cairn.agent.router import intent_of
from cairn.agent.tools import Evidence, ToolError, ToolResult, ft_query
from cairn.derive.expertise import decay, score_activities, size_factor
from cairn.extract.drift import search_terms
from cairn.extract.grounding import verify_quote
from cairn.extract.models import ExtractedDecision, ExtractionResult, QuotedClaim
from cairn.extract.pipeline import Segment, Thread, ground

DAY = 86_400_000


# ------------------------------------------------------------------ grounding


def test_verify_quote_accepts_exact_and_markdown_drift() -> None:
    src = "We **decided** to use `Redis` for the session cache.\n\nIt supports persistence."
    assert verify_quote("We decided to use Redis for the session cache.", src)
    assert verify_quote("we decided to use redis for the session cache", src)


def test_verify_quote_rejects_paraphrase_and_tiny_quotes() -> None:
    src = "We decided to use Redis for the session cache."
    assert not verify_quote("The team picked Redis as its caching layer.", src)
    assert not verify_quote("Redis", src)


def _thread() -> Thread:
    return Thread(
        uid="acme/api#10",
        repo="acme/api",
        kind="pr",
        title="Use Redis",
        url="u",
        ts=1,
        author="alice",
        segments=[
            Segment(
                "S1",
                "acme/api#10",
                "alice",
                "pr",
                "We decided to use Redis for the session cache. Redis supports persistence across deploys.",
            ),
            Segment("S2", "acme/api#10/issuecomment-1", "carol", "comment", "Latency in staging dropped from 40ms to 9ms with Redis."),
        ],
        participants=["alice", "carol"],
        modules=["acme/api:api/cache"],
        repo_modules=["acme/api:api/cache", "acme/api:api/billing"],
    )


def test_ground_keeps_verified_claims_and_drops_fabrications() -> None:
    from cairn.extract.pipeline import Report

    result = ExtractionResult(
        decisions=[
            ExtractedDecision(
                title="Use Redis for session cache",
                summary="s",
                statement=QuotedClaim(text="t", quote="We decided to use Redis for the session cache.", segment="S1"),
                premises=[
                    QuotedClaim(text="Redis persists", quote="Redis supports persistence across deploys.", segment="S1"),
                    # Mislabelled segment: verbatim in S2, cited as S1 -> kept and relabelled.
                    QuotedClaim(text="Faster", quote="Latency in staging dropped from 40ms to 9ms", segment="S1"),
                    QuotedClaim(text="Cheaper", quote="Redis is cheaper than every alternative we priced.", segment="S2"),
                ],
                systems=["acme/api:api/cache", "not/a:system"],
                deciders=["@alice", "mallory"],
            ),
            ExtractedDecision(
                title="Invented",
                summary="s",
                statement=QuotedClaim(text="t", quote="Nobody ever wrote this sentence anywhere.", segment="S1"),
            ),
        ]
    )
    report = Report()
    out = ground(_thread(), result, report)
    assert len(out) == 1, "a decision with no verifiable quote is dropped"
    g = out[0]
    assert [src for _, src in g.premises] == ["acme/api#10", "acme/api#10/issuecomment-1"]
    assert report.premises_kept == 2 and report.premises_dropped == 1 and report.decisions_dropped == 1
    assert g.systems == ["acme/api:api/cache"]
    assert g.deciders == ["alice"]
    assert g.confidence == pytest.approx(3 / 4)


# ------------------------------------------------------------------ expertise


def test_decay_halves_at_half_life() -> None:
    assert decay(0, 180 * DAY) == pytest.approx(0.5)
    assert decay(10, 5) == 1.0  # future timestamps don't inflate scores


def test_size_factor_is_bounded() -> None:
    assert 0.5 <= size_factor(0) < size_factor(50) < size_factor(5000) <= 1.0


def test_score_rolls_module_expertise_up_to_repo() -> None:
    now = 1000 * DAY
    tallies = score_activities(
        {"authored": [("alice", "r:m", now, 100)], "reviewed": [("bob", "r:m", now - 360 * DAY, 100)], "merged": [], "commented": []},
        [("carol", "r", now)],
        {"r:m": "r"},
        now,
    )
    assert tallies[("alice", "r:m")].score > tallies[("bob", "r:m")].score
    assert tallies[("alice", "r")].score == pytest.approx(0.5 * tallies[("alice", "r:m")].score)
    assert tallies[("carol", "r")].counts["discussed"] == 1


# ------------------------------------------------------------------ agent plumbing


def test_clean_citations_strips_unknown_refs() -> None:
    ledger = Ledger()
    ledger.add(Evidence("acme/api#10", "Use Redis", "https://x/10", "pr", 0, "quote"))
    text, cites = clean_citations("Redis was chosen [1] for persistence [7].", ledger)
    assert text == "Redis was chosen [1] for persistence."
    assert [c["uid"] for c in cites] == ["acme/api#10"]


def test_ledger_numbers_are_stable_and_deduplicated() -> None:
    ledger = Ledger()
    a = ledger.add(Evidence("u1", "t", "url", "pr"))
    b = ledger.add(Evidence("u2", "t", "url", "pr"))
    assert (a, b, ledger.add(Evidence("u1", "t", "url", "pr", quote="q"))) == (1, 2, 1)
    assert ledger.entries["u1"]["quote"] == "q"


def test_tool_payload_stays_valid_json_under_cap() -> None:
    import json

    big = ToolResult(data={"rows": [{"text": "x" * 2000} for _ in range(100)]}, summary="s")
    payload = tool_payload(big, Ledger())
    assert len(payload) <= 9_000
    json.loads(payload)


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("Why did we pick Redis?", "why"),
        ("Who knows the billing module best?", "who"),
        ("What breaks if the client goes down?", "impact"),
        ("What happens if alice leaves?", "risk"),
        ("Which decisions are outdated?", "drift"),
        ("What changed in billing since June?", "timeline"),
        ("redis", "search"),
        ("Why did we bound our dependencies to tested versions?", "why"),
        ("How come the release depends on hiredis?", "why"),
    ],
)
def test_intent_router(question: str, intent: str) -> None:
    assert intent_of(question) == intent


def test_ft_query_escapes_and_prefixes() -> None:
    assert ft_query("api/routes (v2)!") == "api|routes*|v2"
    with pytest.raises(ToolError):
        ft_query("?! ")


def test_drift_search_terms_drop_stopwords() -> None:
    q = search_terms("Redis supports persistence, which we need for warm restarts after deploys")
    assert "which" not in q.split("|") and "persistence" in q.split("|")


def test_malformed_and_grouped_citations_are_normalised() -> None:
    ledger = Ledger()
    ledger.add(Evidence("a", "A", "u", "pr"))
    ledger.add(Evidence("b", "B", "u", "pr"))
    text, cites = clean_citations("Chosen for speed [10.666666666666668]. Also [1, 2] and [ 2 ] and [9].", ledger)
    assert text == "Chosen for speed. Also [1][2] and [2] and."
    assert [c["ref"] for c in cites] == [1, 2]


def test_refs_are_annotated_inline() -> None:
    from cairn.agent.loop import annotate_refs

    ledger = Ledger()
    ledger.add(Evidence("x#1", "t", "u", "pr"))
    data = {"premises": [{"claim": "c", "source": "x#1"}, {"claim": "d", "source": "unknown"}]}
    out = annotate_refs(data, ledger)
    assert out["premises"][0]["ref"] == 1 and "ref" not in out["premises"][1]


def test_clean_params_replaces_lone_surrogates() -> None:
    from cairn.graph.client import clean_params

    broken = "half an emoji " + chr(0xD83D) + " here"
    assert clean_params({"rows": [{"body": broken}]}) == {"rows": [{"body": "half an emoji � here"}]}
    assert clean_params("fine \U0001f389") == "fine \U0001f389"


def test_decisions_with_different_numbers_are_not_merged() -> None:
    from cairn.extract.merge import numbers_conflict

    assert numbers_conflict("Use Redis with 24h TTL for webhook idempotency", "Extend idempotency key TTL to 7 days")
    assert not numbers_conflict("Move the events pipeline to Amazon MSK", "Adopt AWS MSK for Kafka")
    assert not numbers_conflict("Adopt RFC-0007 OR-Tools", "Replace the greedy heuristic with OR-Tools"), "one side without numbers"


def test_citation_ranges_with_any_dash_are_expanded() -> None:
    ledger = Ledger()
    for i in range(1, 5):
        ledger.add(Evidence(f"s{i}", f"t{i}", None, "pr", 0))
    text, cites = clean_citations("Gal leads ingestion [1‑3] and Naseem helps [4].", ledger)
    assert text == "Gal leads ingestion [1][2][3] and Naseem helps [4]."
    assert [c["ref"] for c in cites] == [1, 2, 3, 4]


def test_lenticular_bracket_citations_are_recognised() -> None:
    ledger = Ledger()
    ledger.add(Evidence("s1", "t1", None, "pr", 0))
    text, cites = clean_citations("Gal knows ingestion【1】.", ledger)
    assert text == "Gal knows ingestion[1]." and [c["ref"] for c in cites] == [1]


def test_eval_scores_replies_as_their_thread_and_reports_judged() -> None:
    from cairn.evaluation import Question, report_markdown, score

    q = Question("q1", "incident", "Has this happened before?", {"any_uids": ["ticket:PAY-341"], "all_terms": ["TTL"]}, "PAY-341", "cairn_orbiton")
    s = score(q, "cairn", "Yes: the TTL had expired.", ["ticket:PAY-341/c1"], 1.0)
    assert s.passed and s.workspace == "cairn_orbiton"
    s.judged = True
    other = score(q, "rag", "I don't know.", [], 1.0)
    other.judged = False
    md = report_markdown([s, other], [q], model="nvidia:x", judge_model="cloudflare:y")
    assert "| **cairn** | **1/1** | **1/1** |" in md and "| **rag** | **0/1** | **0/1** |" in md
    assert "✅ / ✅" in md and "❌ / ❌" in md


def test_annotated_and_tool_name_citations_are_cleaned() -> None:
    ledger = Ledger()
    ledger.add(Evidence("s1", "t1", None, "pr", 0))
    text, cites = clean_citations("Nothing recorded【1†source】. Four repos depend on it [impact]. See [the docs](x).", ledger)
    assert text == "Nothing recorded[1]. Four repos depend on it. See [the docs](x)."
    assert [c["ref"] for c in cites] == [1]
