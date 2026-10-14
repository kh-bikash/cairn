"""Read models for the browsable parts of the app: decisions, people, systems, suggestions.

Plain functions over the graph so they are easy to test and reuse. Queries avoid filtered matches after an
aggregating WITH (unreliable in FalkorDB) by splitting into simple queries and joining in Python.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from cairn.agent.tools import Tools, fmt_date
from cairn.graph.client import GraphStore, SubGraph

# --------------------------------------------------------------------------- decisions


def list_decisions(
    store: GraphStore, *, q: str | None = None, repo: str | None = None, drift_only: bool = False, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    uids: list[str] | None = None
    if q and q.strip():
        tools = Tools(store)
        hits = store.records(
            """
            CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node RETURN node.uid AS uid
            UNION
            CALL db.idx.fulltext.queryNodes('Premise', $q) YIELD node MATCH (d:Decision)-[:BECAUSE|REJECTED]->(node) RETURN d.uid AS uid
            """,
            {"q": _ft(q)},
        )
        uids = tools._relevant_decisions(q, list(dict.fromkeys(h["uid"] for h in hits))) or [h["uid"] for h in hits]
    rows = store.records(
        """
        MATCH (d:Decision)
        WHERE ($uids IS NULL OR d.uid IN $uids) AND ($repo IS NULL OR d.repo = $repo) AND (NOT $drift OR d.drift = true)
        RETURN d.uid AS uid, d.title AS title, d.summary AS summary, d.category AS category, d.ts AS ts, d.repo AS repo,
               coalesce(d.drift, false) AS drift, d.confidence AS confidence, d.url AS url
        ORDER BY ts DESC, uid
        """,
        {"uids": uids, "repo": repo, "drift": drift_only},
    )
    total = len(rows)
    page = rows[offset : offset + limit]
    ids = [r["uid"] for r in page]
    deciders: dict[str, list[str]] = defaultdict(list)
    for r in store.records("MATCH (p:Person)-[:DECIDED]->(d:Decision) WHERE d.uid IN $ids RETURN d.uid AS uid, p.handle AS h", {"ids": ids}):
        deciders[r["uid"]].append(r["h"])
    reasons = {
        r["uid"]: r["n"] for r in store.records("MATCH (d:Decision)-[:BECAUSE]->(p:Premise) WHERE d.uid IN $ids RETURN d.uid AS uid, count(p) AS n", {"ids": ids})
    }
    items = [
        {
            **{k: r[k] for k in ("uid", "title", "summary", "category", "repo", "drift", "confidence", "url")},
            "date": fmt_date(r["ts"]),
            "deciders": sorted(deciders.get(r["uid"], [])),
            "reasons": reasons.get(r["uid"], 0),
        }
        for r in page
    ]
    if uids is not None:  # keep relevance order for searches
        order = {u: i for i, u in enumerate(uids)}
        items.sort(key=lambda d: order.get(d["uid"], 1 << 30))
    return {"items": items, "total": total}


def decision_detail(store: GraphStore, uid: str) -> dict[str, Any] | None:
    tools = Tools(store)
    sg = SubGraph()
    evidence: list[Any] = []
    detail = tools._decision_detail(uid, sg, evidence)
    if detail.get("missing"):
        return None
    thread = store.records("MATCH (s:Source {uid: $u}) RETURN s.url AS url, s.title AS title", {"u": detail["decided_in"]})
    detail["thread"] = thread[0] if thread else None
    detail["sources"] = _sources(store, [e.uid for e in evidence])
    related = store.records(
        """
        MATCH (d:Decision {uid: $u})-[:AFFECTS]->(s:System)<-[:AFFECTS]-(o:Decision) WHERE o.uid <> $u AND s.kind <> 'repo'
        RETURN DISTINCT o.uid AS uid, o.title AS title, o.ts AS ts ORDER BY ts DESC LIMIT 5
        """,
        {"u": uid},
    )
    detail["related"] = [{"uid": r["uid"], "title": r["title"], "date": fmt_date(r["ts"])} for r in related]
    return {"decision": detail, "graph": sg.to_dict()}


# --------------------------------------------------------------------------- people


def list_people(store: GraphStore, *, q: str | None = None, limit: int = 60) -> dict[str, Any]:
    totals = store.records(
        "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE s.kind = 'repo' "
        "RETURN p.handle AS handle, p.name AS name, sum(k.score) AS score, max(k.last_ts) AS last_ts ORDER BY score DESC",
    )
    if q and q.strip():
        needle = q.strip().lower().lstrip("@")
        totals = [t for t in totals if needle in t["handle"].lower() or needle in (t["name"] or "").lower()]
    totals = totals[:limit]
    handles = [t["handle"] for t in totals]
    areas: dict[str, list[str]] = defaultdict(list)
    for r in store.records(
        "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE p.handle IN $h AND s.kind = 'code' "
        "RETURN p.handle AS h, s.key AS key, k.score AS score ORDER BY score DESC",
        {"h": handles},
    ):
        if len(areas[r["h"]]) < 3:
            areas[r["h"]].append(r["key"])
    decided = {
        r["h"]: r["n"] for r in store.records("MATCH (p:Person)-[:DECIDED]->(d:Decision) WHERE p.handle IN $h RETURN p.handle AS h, count(d) AS n", {"h": handles})
    }
    top = totals[0]["score"] if totals else 1.0
    return {
        "items": [
            {
                "handle": t["handle"],
                "name": t["name"],
                "activity": round(t["score"] / (top or 1.0), 3),
                "last_active": fmt_date(t["last_ts"]),
                "areas": areas.get(t["handle"], []),
                "decisions": decided.get(t["handle"], 0),
            }
            for t in totals
        ]
    }


def person_detail(store: GraphStore, handle: str) -> dict[str, Any]:
    result = Tools(store).person_profile(handle)
    return {"person": result.data, "sources": _sources(store, [e.uid for e in result.evidence]), "graph": result.subgraph.to_dict()}


# --------------------------------------------------------------------------- systems


def list_systems(store: GraphStore) -> dict[str, Any]:
    repos = store.records(
        "MATCH (r:System {kind: 'repo'}) RETURN r.key AS key, r.name AS name, r.description AS description, r.language AS language, r.url AS url ORDER BY key"
    )
    modules = store.records(
        """
        MATCH (m:System)-[:PART_OF]->(r:System) WHERE m.kind = 'code'
        OPTIONAL MATCH (x:Source)-[:TOUCHES]->(m)
        RETURN m.key AS key, m.path AS path, r.key AS repo, count(x) AS prs ORDER BY prs DESC
        """
    )
    modules = [m for m in modules if m["prs"] >= 3]
    experts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in store.records(
        "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE s.key IN $keys RETURN s.key AS key, p.handle AS handle, k.score AS score ORDER BY score DESC",
        {"keys": [m["key"] for m in modules] + [r["key"] for r in repos]},
    ):
        experts[r["key"]].append({"handle": r["handle"], "score": r["score"]})
    deps = store.records("MATCH (a:System)-[e:DEPENDS_ON]->(b:System) RETURN a.key AS src, b.key AS dst, e.via AS via")
    decisions = {r["key"]: r["n"] for r in store.records("MATCH (d:Decision)-[:AFFECTS]->(s:System) RETURN s.key AS key, count(DISTINCT d) AS n")}

    def owners(key: str) -> dict[str, Any]:
        people = experts.get(key, [])
        total = sum(p["score"] for p in people) or 1.0
        top_share = people[0]["score"] / total if people else 0.0
        significant = [p for p in people if p["score"] / total >= 0.2]
        return {
            "owners": [{"handle": p["handle"], "share": round(p["score"] / total, 2)} for p in people[:3]],
            "bus_factor": max(1, len(significant)) if people else 0,
            "concentration": round(top_share, 2),
        }

    out = []
    for r in repos:
        mods = [{**m, **owners(m["key"]), "decisions": decisions.get(m["key"], 0)} for m in modules if m["repo"] == r["key"]]
        out.append(
            {
                **r,
                **owners(r["key"]),
                "decisions": decisions.get(r["key"], 0),
                "depends_on": [d["dst"] for d in deps if d["src"] == r["key"]],
                "depended_on_by": [d["src"] for d in deps if d["dst"] == r["key"]],
                "modules": mods,
            }
        )
    return {"items": out}


def system_detail(store: GraphStore, key: str) -> dict[str, Any]:
    tools = Tools(store)
    who = tools.who_knows(key)
    impact = tools.impact(key)
    timeline = tools.timeline(key, limit=10)
    sg = SubGraph()
    for part in (who, impact):
        sg.merge(part.subgraph)
    decisions = store.records(
        "MATCH (d:Decision)-[:AFFECTS]->(:System {key: $k}) "
        "RETURN d.uid AS uid, d.title AS title, d.ts AS ts, coalesce(d.drift, false) AS drift ORDER BY ts DESC LIMIT 20",
        {"k": who.data["system"]["key"]},
    )
    return {
        "system": who.data["system"],
        "experts": who.data.get("experts", []),
        "impact": impact.data,
        "timeline": timeline.data.get("events", []),
        "decisions": [{"uid": d["uid"], "title": d["title"], "date": fmt_date(d["ts"]), "drift": d["drift"]} for d in decisions],
        "graph": sg.to_dict(),
    }


# --------------------------------------------------------------------------- suggestions & overview


def suggestions(store: GraphStore, limit: int = 4) -> list[str]:
    """Starter questions generated from this workspace's own graph, so they're always answerable."""
    out: list[str] = []
    recent = store.records("MATCH (d:Decision) WHERE d.confidence >= 0.75 RETURN d.title AS title ORDER BY d.ts DESC LIMIT 1")
    if recent:
        out.append(f"Why did we decide to {_lower_first(recent[0]['title'])}?")
    busiest = store.records(
        "MATCH (x:Source)-[:TOUCHES]->(m:System) WHERE m.kind = 'code' RETURN m.path AS path, m.repo AS repo, count(x) AS n ORDER BY n DESC LIMIT 1"
    )
    if busiest:
        repo = busiest[0]["repo"].split("/")[-1]
        out.append(f"Who knows {repo} {busiest[0]['path']} best?")
    depended = store.records("MATCH (a:System)-[:DEPENDS_ON]->(b:System) RETURN b.name AS name, count(a) AS n ORDER BY n DESC LIMIT 1")
    if depended:
        out.append(f"What breaks if {depended[0]['name']} changes?")
    out.append(
        "Which decisions may no longer hold?"
        if store.ro_query("MATCH (d:Decision {drift: true}) RETURN count(d)")[0][0]
        else "Where is knowledge concentrated in one person?"
    )
    return out[:limit]


def workspace(store: GraphStore) -> dict[str, Any]:
    counts = store.counts()["nodes"]
    span = store.records("MATCH (s:Source) WHERE s.kind IN ['pr', 'issue'] RETURN min(s.ts) AS first, max(s.ts) AS last")
    drift = store.ro_query("MATCH (d:Decision {drift: true}) RETURN count(d)")[0][0]
    pending = store.ro_query("MATCH (a:Action {status: 'pending'}) RETURN count(a)")[0][0]
    return {
        "people": counts.get("Person", 0),
        "systems": counts.get("System", 0),
        "sources": counts.get("Source", 0),
        "decisions": counts.get("Decision", 0),
        "reasons": counts.get("Premise", 0),
        "drifted": drift,
        "pending_actions": pending,
        "from": fmt_date(span[0]["first"]) if span else None,
        "to": fmt_date(span[0]["last"]) if span else None,
    }


# --------------------------------------------------------------------------- helpers


def _sources(store: GraphStore, uids: list[str]) -> list[dict[str, Any]]:
    uids = list(dict.fromkeys(uids))
    rows = store.records("MATCH (s:Source) WHERE s.uid IN $u RETURN s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts", {"u": uids})
    by = {r["uid"]: r for r in rows}
    return [{**by[u], "date": fmt_date(by[u]["ts"])} for u in uids if u in by]


def _ft(text: str) -> str:
    from cairn.agent.tools import ft_query

    return ft_query(text)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text
