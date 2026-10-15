"""One decision, many sources.

The same decision usually shows up several times: argued in Slack, written up as an ADR, executed in a ticket.
When extraction finds a decision that already exists, the new thread is linked to it (`DISCUSSED_IN`) and its new
reasons are added as extra evidence, instead of creating a duplicate. This is what lets an answer trace a decision
from the chat where it started to the document that made it official.
"""

from __future__ import annotations

from typing import Any

from cairn.agent.tools import _stem, _words
from cairn.graph.client import GraphStore

_WINDOW_MS = 120 * 86_400_000  # decisions more than ~4 months apart are treated as distinct
_TITLE_STOP = frozenset(
    [
        "use",
        "using",
        "adopt",
        "choose",
        "move",
        "switch",
        "build",
        "make",
        "keep",
        "add",
        "the",
        "a",
        "an",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "and",
        "as",
        "from",
        "into",
    ]
)


def stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in _TITLE_STOP}


def find_existing(store: GraphStore, *, repo: str, ts: int | None, title: str, systems: list[str], exclude_thread: str) -> str | None:
    """Return the uid of an existing decision this one restates, or None."""
    if ts is None:
        return None
    rows = store.records(
        """
        MATCH (d:Decision)-[:DECIDED_IN]->(t:Source)
        WHERE d.repo = $repo AND t.uid <> $thread AND d.ts >= $lo AND d.ts <= $hi
        OPTIONAL MATCH (d)-[:AFFECTS]->(s:System)
        RETURN d.uid AS uid, d.title AS title, d.confidence AS confidence, collect(s.key) AS systems
        """,
        {"repo": repo, "thread": exclude_thread, "lo": ts - _WINDOW_MS, "hi": ts + _WINDOW_MS},
    )
    mine = stems(title)
    best: tuple[float, str] | None = None
    for r in rows:
        overlap = mine & stems(r["title"])
        shared_system = bool(set(systems) & set(r["systems"]))
        if len(overlap) < 2 or not (shared_system or len(overlap) >= 3):
            continue
        score = len(overlap) / max(1, len(mine | stems(r["title"]))) + (0.5 if shared_system else 0.0) + (r["confidence"] or 0) * 0.1
        if best is None or score > best[0]:
            best = (score, r["uid"])
    return best[1] if best else None


def attach(store: GraphStore, decision_uid: str, thread_uid: str, premises: list[dict[str, Any]], deciders: list[str]) -> int:
    """Link a thread to an existing decision and add its genuinely new reasons. Returns premises added."""
    store.query(
        "MATCH (d:Decision {uid: $d}), (t:Source {uid: $t}) MERGE (d)-[:DISCUSSED_IN]->(t)",
        {"d": decision_uid, "t": thread_uid},
    )
    existing = [stems(r["text"]) for r in store.records("MATCH (:Decision {uid: $d})-[:BECAUSE|REJECTED]->(p:Premise) RETURN p.text AS text", {"d": decision_uid})]
    added = 0
    for p in premises:
        mine = stems(p["text"])
        if any(len(mine & e) / max(1, len(mine | e)) >= 0.5 for e in existing):
            continue  # same reason already recorded from another source
        store.query(
            f"""
            MATCH (d:Decision {{uid: $d}}), (s:Source {{uid: $src}})
            MERGE (p:Premise {{uid: $uid}})
            SET p.text = $text, p.ts = $ts, p.kind = toLower('{p["rel"]}'), p.option = $option
            MERGE (d)-[:{p["rel"]}]->(p)
            MERGE (p)-[e:EVIDENCED_BY]->(s) SET e.quote = $quote
            """,
            {"d": decision_uid, "src": p["src"], "uid": p["uid"], "text": p["text"], "ts": p["ts"], "option": p["option"], "quote": p["quote"]},
        )
        existing.append(mine)
        added += 1
    if deciders:
        store.query(
            "MATCH (d:Decision {uid: $d}) UNWIND $h AS h MATCH (p:Person {handle: h}) MERGE (p)-[:DECIDED]->(d)",
            {"d": decision_uid, "h": deciders},
        )
    return added
