"""Derive `KNOWS_ABOUT` edges: who knows which system, and why.

Score = sum over activities of weight(kind) * size_factor * recency_decay.
Each edge keeps its component counts so the agent can explain the ranking
("authored 7 PRs, reviewed 12, last active 9 days ago") instead of quoting a bare number.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from cairn.graph.client import GraphStore

HALF_LIFE_DAYS = 180.0
# Making a design decision or writing the doc about a system is stronger evidence of knowing it than routine tickets.
WEIGHTS = {"authored": 3.0, "reviewed": 1.5, "merged": 1.0, "commented": 0.5, "worked": 1.5, "wrote": 3.0, "chatted": 0.5, "decided": 4.0}
KINDS = (*WEIGHTS, "discussed")
_DAY_MS = 86_400_000

_ACTIVITY_QUERIES: dict[str, str] = {
    "authored": """
        MATCH (p:Person)-[:AUTHORED]->(s:Source {kind: 'pr'})-[t:TOUCHES]->(m:System)
        RETURN p.handle, m.key, s.ts, t.additions + t.deletions
    """,
    "reviewed": """
        MATCH (p:Person)-[r:REVIEWED]->(s:Source {kind: 'pr'})-[t:TOUCHES]->(m:System)
        WHERE NOT (p)-[:AUTHORED]->(s)
        RETURN p.handle, m.key, coalesce(r.ts, s.ts), t.additions + t.deletions
    """,
    "merged": """
        MATCH (p:Person)-[r:MERGED]->(s:Source {kind: 'pr'})-[t:TOUCHES]->(m:System)
        WHERE NOT (p)-[:AUTHORED]->(s)
        RETURN p.handle, m.key, coalesce(r.ts, s.ts), t.additions + t.deletions
    """,
    "commented": """
        MATCH (p:Person)-[:AUTHORED]->(c:Source)-[:REPLY_TO]->(s:Source {kind: 'pr'})-[t:TOUCHES]->(m:System)
        WHERE NOT (p)-[:AUTHORED]->(s)
        RETURN p.handle, m.key, c.ts, t.additions + t.deletions
    """,
    # Tickets someone filed or was assigned, on the components they touch.
    "worked": """
        MATCH (p:Person)-[:AUTHORED|ASSIGNED]->(s:Source {kind: 'ticket'})-[:TOUCHES]->(m:System)
        RETURN p.handle, m.key, s.ts, 0
    """,
    # Design docs, ADRs, postmortems and runbooks written about a system.
    "wrote": """
        MATCH (p:Person)-[:AUTHORED]->(s:Source)-[:TOUCHES|MENTIONS]->(m:System)
        WHERE s.kind IN ['doc', 'adr', 'rfc', 'postmortem', 'runbook', 'meeting']
        RETURN p.handle, m.key, s.ts, 0
    """,
    # Making (or approving) a decision about a system.
    "decided": """
        MATCH (p:Person)-[:DECIDED]->(d:Decision)-[:AFFECTS]->(m:System)
        RETURN p.handle, m.key, d.ts, 0
    """,
    # Chat: starting or replying to a thread that is about a system.
    "chatted": """
        MATCH (p:Person)-[:AUTHORED]->(t:Source {kind: 'thread'})-[:MENTIONS]->(m:System)
        RETURN p.handle AS h, m.key AS k, t.ts AS ts, 0 AS churn
        UNION ALL
        MATCH (p:Person)-[:AUTHORED]->(c:Source)-[:REPLY_TO]->(t:Source {kind: 'thread'})-[:MENTIONS]->(m:System)
        RETURN p.handle AS h, m.key AS k, c.ts AS ts, 0 AS churn
    """,
}

# Issue/discussion activity has no file footprint; it counts toward the repo as a whole.
_REPO_ACTIVITY = """
    MATCH (p:Person)-[:AUTHORED]->(s:Source)-[:IN_REPO]->(r:System)
    WHERE s.kind IN ['issue', 'discussion']
    RETURN p.handle, r.key, s.ts
"""
_MODULE_REPO = "MATCH (m:System)-[:PART_OF]->(r:System) RETURN m.key, r.key"


@dataclass
class Tally:
    score: float = 0.0  # recency-weighted: who to ask now
    lifetime: float = 0.0  # undecayed: who has ever known it (what leaves when they do)
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_ts: int = 0

    def add(self, kind: str, weight: float, ts: int, lifetime: float | None = None) -> None:
        self.score += weight
        self.lifetime += weight if lifetime is None else lifetime
        self.counts[kind] += 1
        self.last_ts = max(self.last_ts, ts)


def decay(ts: int, now: int, half_life_days: float = HALF_LIFE_DAYS) -> float:
    age_days = max(0.0, (now - ts) / _DAY_MS)
    return math.pow(0.5, age_days / half_life_days)


def size_factor(churn: int | None) -> float:
    """Diminishing returns on PR size: a 2,000-line PR isn't 100x a 20-line one."""
    return 0.5 + 0.5 * min(1.0, math.log10(1 + max(0, churn or 0)) / 3)


def score_activities(
    activities: dict[str, list[tuple[str, str, int | None, int | None]]],
    repo_activity: list[tuple[str, str, int | None]],
    module_repo: dict[str, str],
    now: int,
) -> dict[tuple[str, str], Tally]:
    tallies: dict[tuple[str, str], Tally] = defaultdict(Tally)
    for kind, rows in activities.items():
        for handle, key, ts, churn in rows:
            if ts is None:
                continue
            base = WEIGHTS[kind] * size_factor(churn)
            w = base * decay(ts, now)
            tallies[(handle, key)].add(kind, w, ts, base)
            # Roll module expertise up to the repo with a discount: knowing one module ≠ knowing the repo.
            if repo := module_repo.get(key):
                tallies[(handle, repo)].add(kind, 0.5 * w, ts, 0.5 * base)
    for handle, repo, ts in repo_activity:
        if ts is not None:
            tallies[(handle, repo)].add("discussed", 0.75 * decay(ts, now), ts, 0.75)
    return tallies


def derive_expertise(store: GraphStore, *, min_score: float = 0.05) -> int:
    now_rows = store.ro_query("MATCH (s:Source) RETURN max(s.ts)")
    # Anchor "now" to the newest event in the graph so a frozen snapshot scores the same forever.
    now = int(now_rows[0][0] or 0) if now_rows else 0
    activities = {k: [tuple(r) for r in store.ro_query(q, timeout_ms=60_000)] for k, q in _ACTIVITY_QUERIES.items()}
    repo_activity = [tuple(r) for r in store.ro_query(_REPO_ACTIVITY, timeout_ms=60_000)]
    module_repo = dict(tuple(r) for r in store.ro_query(_MODULE_REPO))
    tallies = score_activities(activities, repo_activity, module_repo, now)

    rows: list[dict[str, Any]] = [
        {
            "handle": h,
            "key": k,
            "score": round(t.score, 4),
            "lifetime": round(t.lifetime, 4),
            "last_ts": t.last_ts,
            **{kind: t.counts.get(kind, 0) for kind in KINDS},
        }
        for (h, k), t in tallies.items()
        if t.score >= min_score
    ]
    store.query("MATCH ()-[e:KNOWS_ABOUT]->() DELETE e")
    return store.write_batches(
        """
        UNWIND $rows AS r
        MATCH (p:Person {handle: r.handle}), (s:System {key: r.key})
        MERGE (p)-[e:KNOWS_ABOUT]->(s)
        SET e.score = r.score, e.lifetime = r.lifetime, e.last_ts = r.last_ts, e.authored = r.authored, e.reviewed = r.reviewed,
            e.merged = r.merged, e.commented = r.commented, e.worked = r.worked, e.wrote = r.wrote, e.chatted = r.chatted,
            e.decided = r.decided, e.discussed = r.discussed
        """,
        rows,
    )


def derive_collaboration(store: GraphStore, *, min_weight: float = 2.0) -> int:
    """Project a people↔people `WORKS_WITH` graph from reviews and replies.

    Label propagation oscillates on bipartite graphs (people↔systems), so community detection
    runs on this one-mode projection instead.
    """
    rows = store.ro_query(
        """
        MATCH (a:Person)-[:REVIEWED]->(pr:Source {kind: 'pr'})<-[:AUTHORED]-(b:Person) WHERE a <> b
        RETURN a.handle AS a, b.handle AS b, 1.0 AS w
        UNION ALL
        MATCH (a:Person)-[:AUTHORED]->(:Source)-[:REPLY_TO]->(t:Source)<-[:AUTHORED]-(b:Person) WHERE a <> b
        RETURN a.handle AS a, b.handle AS b, 0.5 AS w
        """,
        timeout_ms=60_000,
    )
    weights: dict[tuple[str, str], float] = defaultdict(float)
    for a, b, w in rows:
        weights[(min(a, b), max(a, b))] += w  # undirected: one edge per pair
    store.query("MATCH ()-[e:WORKS_WITH]->() DELETE e")
    return store.write_batches(
        "UNWIND $rows AS r MATCH (a:Person {handle: r.a}), (b:Person {handle: r.b}) MERGE (a)-[e:WORKS_WITH]->(b) SET e.weight = r.w",
        [{"a": a, "b": b, "w": w} for (a, b), w in weights.items() if w >= min_weight],
    )
