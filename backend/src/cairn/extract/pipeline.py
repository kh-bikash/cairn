"""Decision extraction: thread → (triage) → LLM extraction → quote verification → graph.

Threads are PRs, issues, discussions and docs together with their comments/reviews. Each thread
is processed once per EXTRACT_VERSION; bump the version to re-run after prompt changes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from cairn.config import Settings
from cairn.extract.grounding import verify_quote
from cairn.extract.merge import attach, find_existing
from cairn.extract.models import ExtractedDecision, ExtractionResult, QuotedClaim
from cairn.graph.client import GraphStore
from cairn.llm.client import LLM, LLMNotConfigured, LLMQuotaExhausted
from cairn.llm.decider import LLMDecider

log = logging.getLogger(__name__)

EXTRACT_VERSION = 2  # v2: decisions restated across sources are merged
_SEGMENT_CHARS = 2_500
_THREAD_CHARS = 14_000
_MAX_SEGMENTS = 14


@dataclass
class Segment:
    label: str
    uid: str
    author: str | None
    kind: str
    text: str


@dataclass
class Thread:
    uid: str
    repo: str
    kind: str
    title: str
    url: str
    ts: int
    author: str | None
    segments: list[Segment]
    participants: list[str]
    modules: list[str]  # module keys this thread touches (PRs) — the default AFFECTS set
    repo_modules: list[str]

    def render(self) -> str:
        parts = [f"Thread: {self.kind} {self.uid} — {self.title}"]
        budget = _THREAD_CHARS
        for seg in self.segments:
            block = f"\n[{seg.label}] ({seg.kind} by @{seg.author or 'unknown'})\n{seg.text[:_SEGMENT_CHARS]}"
            if len(block) > budget:
                break
            parts.append(block)
            budget -= len(block)
        return "\n".join(parts)

    def triage_view(self) -> str:
        head = self.segments[0].text[:1_200] if self.segments else ""
        rest = " | ".join(s.text[:200].replace("\n", " ") for s in self.segments[1:4])
        return f"{self.kind}: {self.title}\n{head}\nReplies: {rest}"


@dataclass
class Report:
    candidates: int = 0
    triaged_out: int = 0
    extracted_threads: int = 0
    decisions: int = 0
    premises_kept: int = 0
    premises_dropped: int = 0
    decisions_dropped: int = 0
    errors: int = 0
    deferred: int = 0
    pruned: int = 0
    quota_exhausted: bool = False
    mode: str = "live"
    seconds: float = 0.0
    error_samples: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- selection

_CANDIDATES = """
MATCH (s:Source)
// ADRs/RFCs are parsed deterministically on import; everything else with a conversation is a candidate.
// Repository docs only count when they are design records: READMEs and guides describe, they don't decide.
WHERE s.kind IN ['pr', 'issue', 'discussion', 'doc', 'thread', 'ticket', 'meeting', 'postmortem', 'runbook']
  AND (s.kind <> 'doc' OR coalesce(s.design, false))
  AND (s.extract_version IS NULL OR s.extract_version < $version)
  AND ($repo IS NULL OR s.repo = $repo)
  AND NOT (s.kind = 'pr' AND s.state = 'closed')
OPTIONAL MATCH (c:Source)-[:REPLY_TO]->(s)
WITH s, count(c) AS replies, size(coalesce(s.body, '')) AS body_len
WHERE s.kind = 'doc' OR body_len >= 300 OR replies >= 2
// Decisions live where people argue: rank by rationale language (scored in Python at load time, see
// ingest.text.rationale_signal) so scarce LLM calls go where they pay off.
WITH s, replies, body_len, coalesce(s.signal, 0) + CASE WHEN s.state = 'merged' THEN 1 ELSE 0 END AS signal
RETURN s.uid AS uid, replies, body_len, signal
ORDER BY signal DESC, replies * 400 + body_len DESC
LIMIT $limit
"""

_THREAD = """
MATCH (s:Source {uid: $uid})
OPTIONAL MATCH (c:Source)-[:REPLY_TO]->(s)
WITH s, c ORDER BY c.ts
WITH s, collect(c {.uid, .kind, .author, .body}) AS replies
OPTIONAL MATCH (s)-[:TOUCHES]->(m:System)
RETURN s {.uid, .repo, .kind, .title, .url, .ts, .author, .body} AS src, replies, collect(DISTINCT m.key) AS modules
"""

# Separate query on purpose: filtered OPTIONAL MATCH after an aggregating WITH is unreliable in FalkorDB.
_REPO_MODULES = "MATCH (m:System) WHERE m.repo = $repo AND m.kind = 'code' RETURN m.key AS key ORDER BY key"


def load_thread(store: GraphStore, uid: str) -> Thread | None:
    rows = store.records(_THREAD, {"uid": uid})
    if not rows:
        return None
    r = rows[0]
    src = r["src"]
    segments = [Segment("S1", src["uid"], src.get("author"), src["kind"], f"{src['title']}\n\n{src.get('body') or ''}")]
    for reply in r["replies"]:
        if reply.get("uid") and (reply.get("body") or "").strip() and len(segments) < _MAX_SEGMENTS:
            segments.append(Segment(f"S{len(segments) + 1}", reply["uid"], reply.get("author"), reply.get("kind") or "comment", reply["body"]))
    participants = sorted({s.author for s in segments if s.author})
    repo_modules = [m["key"] for m in store.records(_REPO_MODULES, {"repo": src["repo"]})]
    return Thread(
        uid=src["uid"],
        repo=src["repo"],
        kind=src["kind"],
        title=src.get("title") or "",
        url=src["url"],
        ts=src.get("ts") or 0,
        author=src.get("author"),
        segments=segments,
        participants=participants,
        modules=[m for m in r["modules"] if m],
        repo_modules=repo_modules,
    )


# --------------------------------------------------------------------------- prompts

_EXTRACT_SYSTEM = """You extract engineering DECISIONS and the REASONS behind them from GitHub threads.

A decision is a deliberate choice that shapes how the software works or how the team works: choosing a
library, an architecture, an API shape, a data model, a default, a process, or rejecting an approach.
Routine bug fixes, version bumps, typo fixes and "LGTM" are NOT decisions.

Rules:
- Only extract what the thread actually states. If there is no clear decision with a stated reason, return {"decisions": []}.
- Every quote must be copied VERBATIM from the segment you cite (same words, same order), max 300 characters.
- Premises are the reasons/assumptions the decision relies on, phrased as standalone factual claims that
  could later become false (e.g. "The hiredis parser is roughly 3x faster than the pure-Python parser").
- At most 3 decisions per thread, at most 4 premises each.
- `systems` must be chosen from the provided module list (exact strings) or left empty.
- `deciders` must be chosen from the provided participant handles.

Return JSON only, matching this shape:
{"decisions": [{"title": str, "summary": str, "category": one of [architecture, dependency, api, data, performance, security, process, product, testing, other],
  "statement": {"text": str, "quote": str, "segment": "S1"},
  "premises": [{"text": str, "quote": str, "segment": "S2"}],
  "rejected": [{"option": str, "text": str, "quote": str, "segment": "S3"}],
  "systems": [str], "deciders": [str]}]}"""


def _extract_messages(thread: Thread) -> list[dict[str, str]]:
    modules = ", ".join(sorted(set(thread.modules) | set(thread.repo_modules))[:60]) or "(none)"
    user = f"Repository: {thread.repo}\nParticipants: {', '.join('@' + p for p in thread.participants) or '(none)'}\nModules: {modules}\n\n{thread.render()}"
    return [{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": user}]


# --------------------------------------------------------------------------- grounding + write


@dataclass
class GroundedDecision:
    uid: str
    decision: ExtractedDecision
    statement_uid: str | None
    premises: list[tuple[QuotedClaim, str]]
    rejected: list[tuple[Any, str]]
    systems: list[str]
    deciders: list[str]
    confidence: float


def ground(thread: Thread, result: ExtractionResult, report: Report) -> list[GroundedDecision]:
    by_label = {s.label: s for s in thread.segments}
    allowed_systems = set(thread.modules) | set(thread.repo_modules)
    out: list[GroundedDecision] = []

    def check(claim: QuotedClaim) -> str | None:
        seg = by_label.get(claim.segment)
        if seg and verify_quote(claim.quote, seg.text):
            return seg.uid
        # Models occasionally mislabel the segment; accept the quote if it is verbatim elsewhere in the thread.
        for other in thread.segments:
            if verify_quote(claim.quote, other.text):
                claim.segment = other.label
                return other.uid
        return None

    for i, d in enumerate(result.decisions[:3]):
        statement_uid = check(d.statement)
        premises = []
        for p in d.premises[:4]:
            if uid := check(p):
                premises.append((p, uid))
                report.premises_kept += 1
            else:
                report.premises_dropped += 1
        rejected = [(r, uid) for r in d.rejected[:3] if (uid := check(r))]
        total_claims = 1 + len(d.premises[:4]) + len(d.rejected[:3])
        verified = int(statement_uid is not None) + len(premises) + len(rejected)
        if not premises and not rejected:
            # Cairn remembers *why*: a decision with no verified reason or alternative isn't worth keeping.
            report.decisions_dropped += 1
            continue
        systems = [s for s in d.systems if s in allowed_systems] or thread.modules[:5]
        deciders = [h.lstrip("@") for h in d.deciders if h.lstrip("@") in thread.participants]
        if not deciders and thread.author:
            deciders = [thread.author]
        out.append(
            GroundedDecision(
                uid=f"{thread.uid}::d{i + 1}",
                decision=d,
                statement_uid=statement_uid,
                premises=premises,
                rejected=rejected,
                systems=systems,
                deciders=deciders,
                confidence=round(verified / total_claims, 3),
            )
        )
    return out


def write_decisions(store: GraphStore, thread: Thread, decisions: list[GroundedDecision]) -> None:
    # Re-extraction replaces this thread's previous decisions and merges wholesale.
    store.query(
        "MATCH (d:Decision)-[:DECIDED_IN]->(:Source {uid: $uid}) OPTIONAL MATCH (d)-[:BECAUSE|REJECTED]->(p:Premise) DETACH DELETE d, p",
        {"uid": thread.uid},
    )
    store.query("MATCH (p:Premise) WHERE p.uid STARTS WITH $prefix DETACH DELETE p", {"prefix": f"{thread.uid}::m"})
    store.query("MATCH (:Decision)-[r:DISCUSSED_IN]->(:Source {uid: $uid}) DELETE r", {"uid": thread.uid})
    for i, g in enumerate(decisions):
        existing = find_existing(store, repo=thread.repo, ts=thread.ts, title=g.decision.title, systems=g.systems, exclude_thread=thread.uid)
        if existing:
            # Same decision seen in another source: link this thread and add only its new reasons.
            rows = [
                {
                    "uid": f"{thread.uid}::m{i + 1}::p{j + 1}",
                    "text": p.text[:500],
                    "quote": p.quote[:400],
                    "src": src,
                    "rel": "BECAUSE",
                    "option": None,
                    "ts": thread.ts,
                }
                for j, (p, src) in enumerate(g.premises)
            ] + [
                {
                    "uid": f"{thread.uid}::m{i + 1}::r{j + 1}",
                    "text": r.text[:500],
                    "quote": r.quote[:400],
                    "src": src,
                    "rel": "REJECTED",
                    "option": r.option[:200],
                    "ts": thread.ts,
                }
                for j, (r, src) in enumerate(g.rejected)
            ]
            attach(store, existing, thread.uid, rows, g.deciders)
            continue
        d = g.decision
        store.query(
            """
            MATCH (t:Source {uid: $thread})
            MERGE (d:Decision {uid: $uid})
            SET d.title = $title, d.summary = $summary, d.category = $category, d.ts = $ts, d.repo = $repo,
                d.status = 'active', d.confidence = $confidence, d.statement = $statement, d.url = $url
            MERGE (d)-[:DECIDED_IN]->(t)
            WITH d
            MATCH (repo:System {key: $repo}) MERGE (d)-[:AFFECTS]->(repo)
            """,
            {
                "thread": thread.uid,
                "uid": g.uid,
                "title": d.title[:200],
                "summary": d.summary[:1000],
                "category": d.category,
                "ts": thread.ts,
                "repo": thread.repo,
                "confidence": g.confidence,
                "statement": d.statement.quote[:400] if g.statement_uid else "",
                "url": thread.url,
            },
        )
        if g.systems:
            store.query(
                "MATCH (d:Decision {uid: $uid}) UNWIND $keys AS k MATCH (s:System {key: k}) MERGE (d)-[:AFFECTS]->(s)",
                {"uid": g.uid, "keys": g.systems},
            )
        if g.deciders:
            store.query(
                "MATCH (d:Decision {uid: $uid}) UNWIND $handles AS h MATCH (p:Person {handle: h}) MERGE (p)-[:DECIDED]->(d)",
                {"uid": g.uid, "handles": g.deciders},
            )
        rows = [
            {"uid": f"{g.uid}::p{j + 1}", "text": p.text[:500], "quote": p.quote[:400], "src": src, "rel": "BECAUSE", "option": None}
            for j, (p, src) in enumerate(g.premises)
        ] + [
            {
                "uid": f"{g.uid}::r{j + 1}",
                "text": r.text[:500],
                "quote": r.quote[:400],
                "src": src,
                "rel": "REJECTED",
                "option": r.option[:200],
            }
            for j, (r, src) in enumerate(g.rejected)
        ]
        for rel in ("BECAUSE", "REJECTED"):
            subset = [r for r in rows if r["rel"] == rel]
            if subset:
                store.query(
                    f"""
                    MATCH (d:Decision {{uid: $uid}})
                    UNWIND $rows AS r
                    MATCH (s:Source {{uid: r.src}})
                    MERGE (p:Premise {{uid: r.uid}})
                    SET p.text = r.text, p.ts = $ts, p.kind = toLower('{rel}'), p.option = r.option
                    MERGE (d)-[:{rel}]->(p)
                    MERGE (p)-[e:EVIDENCED_BY]->(s) SET e.quote = r.quote
                    """,
                    {"uid": g.uid, "rows": subset, "ts": thread.ts},
                )
    store.query(
        "MATCH (s:Source {uid: $uid}) SET s.extract_version = $v, s.decision_count = $n",
        {"uid": thread.uid, "v": EXTRACT_VERSION, "n": len(decisions)},
    )


# --------------------------------------------------------------------------- orchestration


async def process_thread(store: GraphStore, llm: LLM, decider: LLMDecider | None, uid: str, report: Report) -> None:
    thread = load_thread(store, uid)
    if thread is None:
        return
    # Docs are always worth reading; for chatty threads let the cheap model filter first.
    if decider and thread.kind != "doc":
        verdict = await decider.decide(
            "Does this thread record a deliberate engineering/product decision AND the reasoning behind it?",
            thread.triage_view(),
            ["yes", "no"],
        )
        if verdict.answer == "no":
            report.triaged_out += 1
            await asyncio.to_thread(write_decisions, store, thread, [])
            return
    result = await llm.structured("extract", _extract_messages(thread), ExtractionResult, max_tokens=2_000)
    grounded = ground(thread, result, report)
    await asyncio.to_thread(write_decisions, store, thread, grounded)
    report.extracted_threads += 1
    report.decisions += len(grounded)


async def run_extraction(store: GraphStore, llm: LLM, settings: Settings, *, limit: int, repo: str | None = None) -> dict[str, Any]:
    """Extract decisions from unprocessed threads.

    Without API keys this runs in *replay* mode: responses come from the on-disk LLM cache (e.g. a shipped demo
    snapshot) and threads without a cached response are left pending instead of failing. When every model hits its
    daily quota the batch stops cleanly; remaining threads stay pending for the next run.
    """
    replay = not llm.available("extract")
    if replay and not settings.llm_cache:
        raise LLMNotConfigured(f"extraction model {settings.model_extract} has no API key and the LLM cache is disabled")
    started = time.perf_counter()
    report = Report()
    report.mode = "replay" if replay else "live"
    decider = LLMDecider(llm, threshold=settings.decide_confidence_threshold)
    uids = [r["uid"] for r in store.records(_CANDIDATES, {"version": EXTRACT_VERSION, "repo": repo, "limit": limit})]
    report.candidates = len(uids)
    sem = asyncio.Semaphore(settings.llm_max_concurrency)
    stop = asyncio.Event()

    async def one(uid: str) -> None:
        async with sem:
            if stop.is_set():
                report.deferred += 1
                return
            try:
                await process_thread(store, llm, decider, uid, report)
            except LLMQuotaExhausted:
                stop.set()
                report.deferred += 1
                report.quota_exhausted = True
            except LLMNotConfigured:
                if not replay:
                    raise
                report.deferred += 1  # no cached response for this thread; a keyed run will pick it up
            except Exception as exc:  # noqa: BLE001 - one bad thread must not abort the batch
                report.errors += 1
                if len(report.error_samples) < 5:
                    report.error_samples.append(f"{uid}: {type(exc).__name__}: {str(exc)[:200]}")
                log.warning("extraction failed for %s: %s", uid, exc)

    await asyncio.gather(*(one(u) for u in uids))
    report.pruned = prune_unsupported(store)
    report.seconds = round(time.perf_counter() - started, 1)
    return vars(report)


def prune_unsupported(store: GraphStore) -> int:
    """Remove decisions that don't meet the bar, including ones extracted under older rules: no verified reason or
    alternative, or found only in a repository doc that describes rather than decides (README, guide, template)."""
    doomed = [
        r["uid"]
        for r in store.records(
            """
            MATCH (d:Decision)
            OPTIONAL MATCH (d)-[:BECAUSE|REJECTED]->(p:Premise)
            WITH d, count(p) AS reasons
            OPTIONAL MATCH (d)-[:DECIDED_IN]->(t:Source)
            WITH d, reasons, t
            WHERE reasons = 0 OR (t.kind = 'doc' AND NOT coalesce(t.design, false))
            RETURN DISTINCT d.uid AS uid
            """
        )
    ]
    if doomed:
        store.query(
            """
            UNWIND $uids AS u
            MATCH (d:Decision {uid: u})
            OPTIONAL MATCH (d)-[:BECAUSE|REJECTED]->(p:Premise)
            DETACH DELETE d, p
            """,
            {"uids": doomed},
        )
    return len(doomed)
