"""Decision drift: find later evidence that contradicts the premises a decision relied on.

For each premise of an active decision we search *later* sources in the same repo (full-text on the
premise's key terms), let the cheap decider classify each pair, and record a `CHALLENGES` edge only
when a verbatim quote from the later source backs the verdict.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from cairn.agent.tools import _stem, _words
from cairn.config import Settings
from cairn.extract.grounding import verify_quote
from cairn.extract.models import ChallengeCheck
from cairn.graph.client import GraphStore
from cairn.llm.client import LLM, LLMNotConfigured, LLMQuotaExhausted
from cairn.llm.decider import LLMDecider

log = logging.getLogger(__name__)

DRIFT_VERSION = 2  # v2: relevance gate + confirmed verdicts
_CANDIDATES_PER_PREMISE = 4
_STOP = frozenset(
    [
        "about",
        "above",
        "after",
        "again",
        "against",
        "because",
        "before",
        "being",
        "below",
        "between",
        "both",
        "could",
        "does",
        "doing",
        "during",
        "each",
        "from",
        "further",
        "have",
        "having",
        "here",
        "into",
        "itself",
        "more",
        "most",
        "other",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
        "your",
        "will",
        "just",
        "also",
        "only",
        "using",
        "used",
        "make",
        "makes",
        "made",
        "need",
        "needs",
        "like",
        "than",
        "into",
        "onto",
        "upon",
    ]
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
# RediSearch query syntax characters that must not reach the full-text engine unescaped.
_FT_SPECIAL = re.compile(r"[^A-Za-z0-9_ ]")

_PREMISES = """
MATCH (d:Decision {status: 'active'})-[:BECAUSE]->(p:Premise)
WHERE p.drift_version IS NULL OR p.drift_version < $version
RETURN p.uid AS uid, p.text AS text, d.uid AS decision, d.title AS title, d.ts AS ts, d.repo AS repo
ORDER BY d.ts
LIMIT $limit
"""

_LATER = """
CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score
// Repository docs carry the date they were fetched, not written, so they are never "later" evidence.
WHERE node.repo = $repo AND node.ts > $ts AND node.kind <> 'doc' AND NOT (node)<-[:EVIDENCED_BY]-(:Premise {uid: $premise})
RETURN node.uid AS uid, node.title AS title, node.body AS body, node.ts AS ts, node.url AS url, score
ORDER BY score DESC
LIMIT $k
"""


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in _STOP}


def search_terms(text: str, limit: int = 6) -> str:
    """Build an OR query from the premise's most distinctive words."""
    words = []
    for w in _WORD.findall(text):
        lw = _FT_SPECIAL.sub("", w.lower())
        if lw and lw not in _STOP and lw not in words:
            words.append(lw)
    words.sort(key=len, reverse=True)
    return "|".join(words[:limit])


@dataclass
class DriftReport:
    premises: int = 0
    pairs_checked: int = 0
    challenges: int = 0
    unverified: int = 0
    filtered: int = 0
    rejected: int = 0
    errors: int = 0
    deferred: int = 0
    seconds: float = 0.0
    error_samples: list[str] = field(default_factory=list)


async def _second_opinion(llm: LLM, premise: dict[str, Any], later: str, decided_by: str) -> ChallengeCheck:
    """A *different* model must agree there is a contradiction and quote the evidence. The model that gave the verdict
    is excluded (including the same model behind another provider), so one model never confirms its own call."""
    return await llm.structured(
        "extract",
        [
            {
                "role": "system",
                "content": "Decide whether the LATER TEXT shows that the PREMISE is false or no longer holds. Be strict: "
                "mentioning the same topic, praising, or adding detail is NOT a challenge, and neither is evidence that the decision "
                "was carried out when the premise describes the earlier situation. If it is a challenge, copy the single "
                'sentence that shows it, verbatim. Return JSON: {"challenges": bool, "quote": str, "explanation": str (one sentence)}.',
            },
            {"role": "user", "content": f"PREMISE: {premise['text']}\n\nLATER TEXT:\n{later}"},
        ],
        ChallengeCheck,
        max_tokens=300,
        exclude={decided_by},
    )


async def check_premise(store: GraphStore, llm: LLM, decider: LLMDecider, premise: dict[str, Any], report: DriftReport) -> None:
    query = search_terms(premise["text"])
    candidates = []
    if query:
        candidates = store.records(_LATER, {"q": query, "repo": premise["repo"], "ts": premise["ts"] or 0, "premise": premise["uid"], "k": _CANDIDATES_PER_PREMISE})
    premise_terms = _stems(premise["text"])
    decision_terms = _stems(premise["title"])
    for cand in candidates:
        later = f"{cand['title']}\n{(cand['body'] or '')[:2_500]}"
        # Cheap relevance gate before any LLM call: short chatter or text sharing <2 topic words can't challenge it.
        if len((cand["body"] or "").strip()) < 40 or len(premise_terms & _stems(later)) < 2:
            report.filtered += 1
            continue
        # A later source whose title restates the decision ("stop caching description vectors") is the decision being
        # carried out, which always "contradicts" a premise describing the old state. It is not new evidence.
        title_terms = _stems(cand["title"] or "")
        if decision_terms and len(decision_terms & title_terms) / len(decision_terms | title_terms) >= 0.5:
            report.filtered += 1
            continue
        report.pairs_checked += 1
        verdict = await decider.decide(
            "Compared with the PREMISE, what does the LATER text do? 'contradicts' = shows the premise is no longer true; "
            "'supersedes' = replaces the decision that relied on it; 'supports' = confirms it; 'unrelated' otherwise. "
            "A premise that describes the situation before the decision ('X was not tracked', 'we used to') is not "
            "contradicted by later text showing the decision was carried out: that is 'supports'.",
            f"PREMISE (behind decision '{premise['title']}'): {premise['text']}\n\nLATER TEXT:\n{later}",
            ["contradicts", "supersedes", "supports", "unrelated"],
        )
        if verdict.answer not in {"contradicts", "supersedes"}:
            continue
        # Second opinion from a *different* model: it must agree there is a contradiction and quote the evidence.
        # The model that gave the verdict is excluded, so one model can never confirm its own call.
        try:
            check = await _second_opinion(llm, premise, later, verdict.decided_by)
        except (LLMQuotaExhausted, LLMNotConfigured):
            # No second model available right now: leave the premise unchecked so a later run confirms it,
            # and keep checking the rest (most verdicts are 'unrelated' and need no confirmation).
            report.deferred += 1
            return
        if not check.challenges:
            report.rejected += 1
            continue
        if not verify_quote(check.quote, later):
            report.unverified += 1
            continue
        quote = check
        store.query(
            """
            MATCH (s:Source {uid: $src}), (p:Premise {uid: $premise})
            MERGE (s)-[c:CHALLENGES]->(p)
            SET c.quote = $quote, c.verdict = $verdict, c.confidence = $confidence, c.explanation = $explanation, c.decided_by = $by
            WITH p MATCH (d:Decision)-[:BECAUSE]->(p) SET d.drift = true
            """,
            {
                "src": cand["uid"],
                "premise": premise["uid"],
                "quote": quote.quote[:400],
                "verdict": verdict.answer,
                "confidence": verdict.confidence,
                "explanation": quote.explanation[:400],
                "by": verdict.decided_by,
            },
        )
        report.challenges += 1
    store.query("MATCH (p:Premise {uid: $uid}) SET p.drift_version = $v", {"uid": premise["uid"], "v": DRIFT_VERSION})


async def run_drift(store: GraphStore, llm: LLM, settings: Settings, *, limit: int) -> dict[str, Any]:
    replay = not (llm.available("decide") and llm.available("extract"))
    if replay and not settings.llm_cache:
        raise LLMNotConfigured("drift needs the decide and extract models (or a populated LLM cache to replay)")
    started = time.perf_counter()
    report = DriftReport()
    decider = LLMDecider(llm, threshold=settings.decide_confidence_threshold)
    premises = store.records(_PREMISES, {"version": DRIFT_VERSION, "limit": limit})
    report.premises = len(premises)
    sem = asyncio.Semaphore(settings.llm_max_concurrency)
    stop = asyncio.Event()

    async def one(p: dict[str, Any]) -> None:
        async with sem:
            if stop.is_set():
                report.deferred += 1
                return
            try:
                await check_premise(store, llm, decider, p, report)
            except LLMQuotaExhausted:
                stop.set()
                report.deferred += 1
            except LLMNotConfigured:
                if not replay:
                    raise
                report.deferred += 1
            except Exception as exc:  # noqa: BLE001 - keep going; report the failure
                report.errors += 1
                if len(report.error_samples) < 5:
                    report.error_samples.append(f"{p['uid']}: {type(exc).__name__}: {str(exc)[:200]}")
                log.warning("drift check failed for %s: %s", p["uid"], exc)

    await asyncio.gather(*(one(p) for p in premises))
    report.seconds = round(time.perf_counter() - started, 1)
    return vars(report)
