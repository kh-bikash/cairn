"""Graph tools the agent can call. Each is deterministic Cypher (plus FalkorDB algorithms) and returns:

- `data`: compact JSON for the model,
- `subgraph`: the nodes/edges it touched, so the UI can light up the reasoning path,
- `evidence`: citable sources (uid, title, url, optional quote).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cairn.graph.client import GraphStore, SubGraph

_DAY_MS = 86_400_000
_FT_STRIP = re.compile(r"[^\w\s/-]", re.UNICODE)


@dataclass
class Evidence:
    uid: str
    title: str
    url: str
    kind: str
    ts: int | None = None
    quote: str | None = None


@dataclass
class ToolResult:
    data: dict[str, Any]
    summary: str
    subgraph: SubGraph = field(default_factory=SubGraph)
    evidence: list[Evidence] = field(default_factory=list)


class ToolError(ValueError):
    """Bad tool arguments; the message is shown to the model so it can retry."""


def fmt_date(ts: int | None) -> str | None:
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d") if ts else None


def ft_query(text: str, *, prefix: bool = True) -> str:
    """Turn free text into a RediSearch OR-query; prefix-match words so 'ingest' finds 'ingestion'."""
    words = [w for w in _FT_STRIP.sub(" ", text).replace("/", " ").replace("-", " ").split() if len(w) > 1]
    if not words:
        raise ToolError("query has no searchable words")
    return "|".join(f"{w}*" if prefix and len(w) > 3 else w for w in words[:8])


def parse_date(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)
    except ValueError as exc:
        raise ToolError(f"dates must be YYYY-MM-DD, got {value!r}") from exc


class Tools:
    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ================================================================ resolution
    def resolve_system(self, name: str) -> dict[str, Any]:
        rows = self.store.records(
            "MATCH (s:System) WHERE toLower(s.key) = toLower($n) OR toLower(s.name) = toLower($n) "
            "RETURN s.key AS key, s.name AS name, s.kind AS kind, s.repo AS repo LIMIT 1",
            {"n": name.strip()},
        )
        if not rows:
            rows = self.store.records(
                "CALL db.idx.fulltext.queryNodes('System', $q) YIELD node, score "
                "RETURN node.key AS key, node.name AS name, node.kind AS kind, node.repo AS repo, score "
                "ORDER BY CASE node.kind WHEN 'repo' THEN 0 WHEN 'code' THEN 1 ELSE 2 END, score DESC LIMIT 1",
                {"q": ft_query(name)},
            )
        if not rows:
            raise ToolError(f"no system matches {name!r}; call list_systems to see valid names")
        return rows[0]

    def resolve_person(self, handle: str) -> dict[str, Any]:
        rows = self.store.records(
            "MATCH (p:Person) WHERE toLower(p.handle) = toLower($h) RETURN p.handle AS handle, p.name AS name LIMIT 1",
            {"h": handle.strip().lstrip("@")},
        )
        if not rows:
            rows = self.store.records(
                "CALL db.idx.fulltext.queryNodes('Person', $q) YIELD node, score RETURN node.handle AS handle, node.name AS name ORDER BY score DESC LIMIT 1",
                {"q": ft_query(handle)},
            )
        if not rows:
            raise ToolError(f"no person matches {handle!r}")
        return rows[0]

    # ================================================================ tools
    def list_systems(self, repo: str | None = None) -> ToolResult:
        rows = self.store.records(
            """
            MATCH (s:System) WHERE s.kind IN ['repo', 'code'] AND ($repo IS NULL OR s.repo = $repo)
            OPTIONAL MATCH (p:Person)-[k:KNOWS_ABOUT]->(s)
            WITH s, count(p) AS experts
            // Filter inside count(): OPTIONAL MATCH ... WHERE after an aggregating WITH returns no rows in FalkorDB.
            OPTIONAL MATCH (x:Source)-[:TOUCHES|IN_REPO]->(s)
            RETURN s.key AS key, s.kind AS kind, s.repo AS repo, experts,
                   count(DISTINCT CASE WHEN x.kind = 'pr' THEN x END) AS prs
            ORDER BY s.kind DESC, prs DESC LIMIT 80
            """,
            {"repo": repo},
        )
        sg = SubGraph()
        for r in rows:
            sg.node("System", r["key"], name=r["key"], kind=r["kind"])
        return ToolResult({"systems": rows}, f"{len(rows)} systems", sg)

    def search(self, query: str, limit: int = 8) -> ToolResult:
        q = ft_query(query)
        sg = SubGraph()
        evidence: list[Evidence] = []
        out: dict[str, list[dict[str, Any]]] = {}

        decisions = self.store.records(
            "CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node, score "
            "RETURN node.uid AS uid, node.title AS title, node.repo AS repo, node.ts AS ts, node.url AS url, score "
            "ORDER BY score DESC LIMIT $k",
            {"q": q, "k": limit},
        )
        for d in decisions:
            sg.node("Decision", d["uid"], title=d["title"], ts=d["ts"])
        out["decisions"] = [{**d, "date": fmt_date(d.pop("ts"))} for d in decisions]

        sources = self.store.records(
            "CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score "
            "WHERE node.kind IN ['pr', 'issue', 'discussion', 'doc'] "
            "RETURN node.uid AS uid, node.kind AS kind, node.title AS title, node.url AS url, node.ts AS ts, "
            "node.state AS state, node.author AS author, substring(coalesce(node.body, ''), 0, 280) AS snippet, score "
            "ORDER BY score DESC LIMIT $k",
            {"q": q, "k": limit},
        )
        for s in sources:
            sg.node("Source", s["uid"], kind=s["kind"], title=s["title"], ts=s["ts"], url=s["url"])
            evidence.append(Evidence(s["uid"], s["title"], s["url"], s["kind"], s["ts"]))
        out["sources"] = [{**s, "date": fmt_date(s.pop("ts"))} for s in sources]

        systems = self.store.records(
            "CALL db.idx.fulltext.queryNodes('System', $q) YIELD node, score RETURN node.key AS key, node.kind AS kind, score ORDER BY score DESC LIMIT 5",
            {"q": q},
        )
        for s in systems:
            sg.node("System", s["key"], name=s["key"], kind=s["kind"])
        out["systems"] = systems
        n = sum(len(v) for v in out.values())
        return ToolResult(out, f"{n} matches for '{query}'", sg, evidence)

    def why(self, topic: str, limit: int = 3) -> ToolResult:
        """Decisions about a topic, with their premises, evidence quotes, rejected options and drift."""
        q = ft_query(topic)
        sg = SubGraph()
        evidence: list[Evidence] = []
        decision_uids = [
            r["uid"]
            for r in self.store.records(
                """
                CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node, score
                RETURN node.uid AS uid, score
                UNION
                CALL db.idx.fulltext.queryNodes('Premise', $q) YIELD node, score
                MATCH (d:Decision)-[:BECAUSE|REJECTED]->(node)
                RETURN d.uid AS uid, score
                """,
                {"q": q},
            )
        ]
        # Keep order (best first), drop duplicates.
        decision_uids = list(dict.fromkeys(decision_uids))[:limit]
        decisions = [self._decision_detail(uid, sg, evidence) for uid in decision_uids]
        if decisions:
            return ToolResult({"decisions": decisions}, f"{len(decisions)} decisions on '{topic}'", sg, evidence)

        # No extracted decision: fall back to the raw threads so the agent can still reason from sources.
        fallback = self.search(topic, limit=5)
        fallback.data = {
            "decisions": [],
            "note": "No extracted decision matches; these raw threads may contain the rationale.",
            "sources": fallback.data.get("sources", []),
        }
        fallback.summary = f"no decisions on '{topic}', {len(fallback.data['sources'])} raw threads"
        return fallback

    def _decision_detail(self, uid: str, sg: SubGraph, evidence: list[Evidence]) -> dict[str, Any]:
        rows = self.store.records(
            """
            MATCH (d:Decision {uid: $uid})-[:DECIDED_IN]->(t:Source)
            OPTIONAL MATCH (p:Person)-[:DECIDED]->(d)
            WITH d, t, collect(DISTINCT p.handle) AS deciders
            OPTIONAL MATCH (d)-[:AFFECTS]->(s:System)
            WITH d, t, deciders, collect(DISTINCT s.key) AS systems
            OPTIONAL MATCH (newer:Decision)-[:SUPERSEDES]->(d)
            RETURN d {.uid, .title, .summary, .category, .ts, .status, .confidence, .statement, .drift} AS d,
                   t {.uid, .title, .url, .kind, .ts} AS thread, deciders, systems,
                   collect(DISTINCT newer {.uid, .title}) AS superseded_by
            """,
            {"uid": uid},
        )
        if not rows:
            return {"uid": uid, "missing": True}
        r = rows[0]
        d, thread = r["d"], r["thread"]
        did = sg.node("Decision", d["uid"], title=d["title"], ts=d["ts"], drift=d.get("drift"))
        tid = sg.node("Source", thread["uid"], kind=thread["kind"], title=thread["title"], ts=thread["ts"], url=thread["url"])
        sg.edge(did, "DECIDED_IN", tid)
        evidence.append(Evidence(thread["uid"], thread["title"], thread["url"], thread["kind"], thread["ts"], d.get("statement") or None))
        for h in r["deciders"]:
            sg.edge(sg.node("Person", h, name=h), "DECIDED", did)
        for key in r["systems"]:
            sg.edge(did, "AFFECTS", sg.node("System", key, name=key))

        claims = self.store.records(
            """
            MATCH (d:Decision {uid: $uid})-[rel:BECAUSE|REJECTED]->(p:Premise)-[e:EVIDENCED_BY]->(s:Source)
            OPTIONAL MATCH (c:Source)-[ch:CHALLENGES]->(p)
            RETURN type(rel) AS rel, p.uid AS puid, p.text AS text, p.option AS option, e.quote AS quote,
                   s {.uid, .title, .url, .kind, .ts, .author} AS src,
                   collect(c {.uid, .title, .url, .kind, .ts, quote: ch.quote, verdict: ch.verdict, explanation: ch.explanation}) AS challenges
            """,
            {"uid": uid},
        )
        premises, rejected = [], []
        for c in claims:
            pid = sg.node("Premise", c["puid"], text=c["text"])
            sg.edge(did, c["rel"], pid)
            sid = sg.node("Source", c["src"]["uid"], kind=c["src"]["kind"], title=c["src"]["title"], ts=c["src"]["ts"], url=c["src"]["url"])
            sg.edge(pid, "EVIDENCED_BY", sid, quote=c["quote"])
            evidence.append(Evidence(c["src"]["uid"], c["src"]["title"], c["src"]["url"], c["src"]["kind"], c["src"]["ts"], c["quote"]))
            challenges = []
            for ch in c["challenges"]:
                if not ch or not ch.get("uid"):
                    continue
                cid = sg.node("Source", ch["uid"], kind=ch["kind"], title=ch["title"], ts=ch["ts"], url=ch["url"])
                sg.edge(cid, "CHALLENGES", pid, verdict=ch["verdict"])
                evidence.append(Evidence(ch["uid"], ch["title"], ch["url"], ch["kind"], ch["ts"], ch["quote"]))
                challenges.append(
                    {
                        "source": ch["uid"],
                        "date": fmt_date(ch["ts"]),
                        "verdict": ch["verdict"],
                        "quote": ch["quote"],
                        "why": ch["explanation"],
                    }
                )
            item = {"claim": c["text"], "quote": c["quote"], "source": c["src"]["uid"], "by": c["src"]["author"]}
            if c["rel"] == "BECAUSE":
                premises.append({**item, "challenged_by": challenges})
            else:
                rejected.append({**item, "option": c["option"]})
        return {
            "uid": d["uid"],
            "title": d["title"],
            "summary": d["summary"],
            "category": d["category"],
            "date": fmt_date(d["ts"]),
            "status": d["status"],
            "decided_in": thread["uid"],
            "deciders": r["deciders"],
            "systems": r["systems"],
            "premises": premises,
            "rejected_alternatives": rejected,
            "drift_detected": bool(d.get("drift")),
            "superseded_by": [s for s in r["superseded_by"] if s and s.get("uid")],
            "grounding": d["confidence"],
        }

    def who_knows(self, system: str, limit: int = 5) -> ToolResult:
        target = self.resolve_system(system)
        sg = SubGraph()
        sid = sg.node("System", target["key"], name=target["key"], kind=target["kind"])
        people = self.store.records(
            """
            MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System {key: $key})
            OPTIONAL MATCH (p)-[o:OWNS]->(s)
            RETURN p.handle AS handle, p.name AS name, k.score AS score, k.authored AS authored, k.reviewed AS reviewed,
                   k.merged AS merged, k.commented AS commented, k.discussed AS discussed, k.last_ts AS last_ts, o.via AS owns
            ORDER BY k.score DESC LIMIT $k
            """,
            {"key": target["key"], "k": limit},
        )
        if not people:
            return ToolResult({"system": target, "experts": [], "note": "no recorded activity on this system"}, "no experts found", sg)
        total = sum(p["score"] for p in people) or 1.0
        evidence: list[Evidence] = []
        experts = []
        # Module-level systems: evidence is PRs touching it. Repo-level: PRs in the repo.
        touch = "(s)-[:TOUCHES]->(:System {key: $key})" if target["kind"] != "repo" else "(s)-[:IN_REPO]->(:System {key: $key})"
        for p in people:
            pid = sg.node("Person", p["handle"], name=p["name"] or p["handle"])
            sg.edge(pid, "KNOWS_ABOUT", sid, score=p["score"])
            prs = self.store.records(
                f"""
                MATCH (:Person {{handle: $h}})-[r:AUTHORED|REVIEWED]->(s:Source {{kind: 'pr'}}) WHERE {touch}
                WITH s, collect(type(r)) AS roles
                RETURN s.uid AS uid, s.title AS title, s.url AS url, s.ts AS ts,
                       CASE WHEN 'AUTHORED' IN roles THEN 'AUTHORED' ELSE 'REVIEWED' END AS role
                ORDER BY ts DESC LIMIT 3
                """,
                {"h": p["handle"], "key": target["key"]},
            )
            for pr in prs:
                prid = sg.node("Source", pr["uid"], kind="pr", title=pr["title"], ts=pr["ts"], url=pr["url"])
                sg.edge(pid, pr["role"], prid)
                sg.edge(prid, "TOUCHES" if target["kind"] != "repo" else "IN_REPO", sid)
                evidence.append(Evidence(pr["uid"], pr["title"], pr["url"], "pr", pr["ts"]))
            experts.append(
                {
                    "handle": p["handle"],
                    "name": p["name"],
                    "share": round(p["score"] / total, 2),
                    "authored_prs": p["authored"],
                    "reviewed_prs": p["reviewed"],
                    "merged_prs": p["merged"],
                    "comments": p["commented"],
                    "last_active": fmt_date(p["last_ts"]),
                    "codeowner": p["owns"],
                    "recent_prs": [{"uid": x["uid"], "title": x["title"], "role": x["role"].lower(), "date": fmt_date(x["ts"])} for x in prs],
                }
            )
        return ToolResult({"system": target, "experts": experts}, f"{len(experts)} experts on {target['key']}", sg, evidence)

    def impact(self, system: str) -> ToolResult:
        """What depends on this system, who to call, and what is in flight."""
        target = self.resolve_system(system)
        repo = target["repo"] or target["key"]
        sg = SubGraph()
        rid = sg.node("System", repo, name=repo, kind="repo")
        if target["key"] != repo:
            sg.edge(sg.node("System", target["key"], name=target["key"], kind=target["kind"]), "PART_OF", rid)
        dependents = self.store.records(
            """
            MATCH path = (d:System)-[:DEPENDS_ON*1..4]->(:System {key: $repo})
            RETURN d.key AS key, length(path) AS hops, [r IN relationships(path) | r.via] AS via
            ORDER BY hops
            """,
            {"repo": repo},
        )
        dependencies = self.store.records("MATCH (:System {key: $repo})-[r:DEPENDS_ON]->(d:System) RETURN d.key AS key, r.via AS via", {"repo": repo})
        affected = [repo] + [d["key"] for d in dependents]
        for d in dependents:
            sg.edge(sg.node("System", d["key"], name=d["key"], kind="repo"), "DEPENDS_ON", rid, via=d["via"][-1] if d["via"] else None)
        for d in dependencies:
            sg.edge(rid, "DEPENDS_ON", sg.node("System", d["key"], name=d["key"], kind="repo"), via=d["via"])

        contacts = self.store.records(
            """
            UNWIND $repos AS rk
            MATCH (p:Person)-[k:KNOWS_ABOUT]->(:System {key: rk})
            WITH rk, p, k ORDER BY k.score DESC
            WITH rk, collect({handle: p.handle, score: k.score})[0..2] AS top
            RETURN rk AS repo, top
            """,
            {"repos": affected},
        )
        for c in contacts:
            for t in c["top"]:
                sg.edge(sg.node("Person", t["handle"], name=t["handle"]), "KNOWS_ABOUT", f"System:{c['repo']}")
        open_items = self.store.records(
            """
            MATCH (s:Source)-[:IN_REPO]->(r:System) WHERE r.key IN $repos AND s.kind IN ['issue', 'pr'] AND s.state = 'open'
            RETURN r.key AS repo, s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts
            ORDER BY s.ts DESC LIMIT 12
            """,
            {"repos": affected},
        )
        evidence = [Evidence(o["uid"], o["title"], o["url"], o["kind"], o["ts"]) for o in open_items]
        for o in open_items:
            sg.edge(sg.node("Source", o["uid"], kind=o["kind"], title=o["title"], ts=o["ts"], url=o["url"]), "IN_REPO", f"System:{o['repo']}")
        decisions = self.store.records(
            """
            MATCH (d:Decision)-[:AFFECTS]->(s:System) WHERE s.key IN $keys
            RETURN DISTINCT d.uid AS uid, d.title AS title, d.ts AS ts, d.drift AS drift ORDER BY d.ts DESC LIMIT 8
            """,
            {"keys": [target["key"], *affected]},
        )
        for d in decisions:
            sg.node("Decision", d["uid"], title=d["title"], ts=d["ts"], drift=d["drift"])
        data = {
            "system": target,
            "repo": repo,
            "depended_on_by": [{"repo": d["key"], "hops": d["hops"], "via_package": d["via"]} for d in dependents],
            "depends_on": dependencies,
            "contacts": {c["repo"]: [t["handle"] for t in c["top"]] for c in contacts},
            "open_items": [{"uid": o["uid"], "kind": o["kind"], "title": o["title"], "date": fmt_date(o["ts"])} for o in open_items],
            "relevant_decisions": [{"uid": d["uid"], "title": d["title"], "date": fmt_date(d["ts"]), "drift": bool(d["drift"])} for d in decisions],
        }
        return ToolResult(data, f"{len(dependents)} dependents, {len(open_items)} open items", sg, evidence)

    def bus_factor(self, repo: str | None = None, min_share: float = 0.2) -> ToolResult:
        """Modules where knowledge is concentrated in one or two people, plus who would leave the biggest gap."""
        rows = self.store.records(
            """
            MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System {kind: 'code'})
            WHERE $repo IS NULL OR s.repo = $repo
            WITH s, p, k ORDER BY k.score DESC
            WITH s, collect({handle: p.handle, score: k.score}) AS people, sum(k.score) AS total
            MATCH (:Source)-[t:TOUCHES]->(s)
            WITH s, people, total, count(t) AS prs WHERE prs >= 3
            RETURN s.key AS key, s.repo AS repo, people, total, prs
            """,
            {"repo": repo},
            timeout_ms=30_000,
        )
        sg = SubGraph()
        modules = []
        sole: dict[str, list[str]] = {}
        for r in rows:
            significant = [p for p in r["people"] if p["score"] / (r["total"] or 1) >= min_share]
            top = r["people"][0]
            top_share = top["score"] / (r["total"] or 1)
            factor = len(significant)
            if factor <= 1 or top_share >= 0.6:
                modules.append(
                    {
                        "system": r["key"],
                        "bus_factor": max(1, factor),
                        "top_expert": top["handle"],
                        "top_share": round(top_share, 2),
                        "prs": r["prs"],
                    }
                )
                sole.setdefault(top["handle"], []).append(r["key"])
                sid = sg.node("System", r["key"], name=r["key"], kind="code", risk=round(top_share, 2))
                sg.edge(sg.node("Person", top["handle"], name=top["handle"]), "KNOWS_ABOUT", sid, score=top["score"])
        modules.sort(key=lambda m: (-m["top_share"], -m["prs"]))
        # Betweenness over the expertise graph: people who bridge otherwise separate parts of the codebase.
        bridges = self.store.records(
            """
            CALL algo.betweenness({nodeLabels: ['Person', 'System'], relationshipTypes: ['KNOWS_ABOUT'], samplingSize: 64, samplingSeed: 7})
            YIELD node, score
            WITH node, score WHERE 'Person' IN labels(node) AND score > 0
            RETURN node.handle AS handle, score ORDER BY score DESC LIMIT 5
            """,
            timeout_ms=30_000,
        )
        concentration = sorted(({"handle": h, "sole_expert_on": ks} for h, ks in sole.items()), key=lambda x: -len(x["sole_expert_on"]))
        data = {
            "at_risk_modules": modules[:15],
            "knowledge_concentration": concentration[:8],
            "bridge_people": [{"handle": b["handle"], "betweenness": round(b["score"], 2)} for b in bridges],
            "method": "bus factor = people holding >= 20% of the module's recency-weighted activity",
        }
        return ToolResult(data, f"{len(modules)} at-risk modules", sg)

    def person_profile(self, handle: str) -> ToolResult:
        """What someone knows, decided and is working on — also the basis for a handoff pack."""
        person = self.resolve_person(handle)
        sg = SubGraph()
        pid = sg.node("Person", person["handle"], name=person["name"] or person["handle"])
        knows = self.store.records(
            """
            MATCH (:Person {handle: $h})-[k:KNOWS_ABOUT]->(s:System)
            OPTIONAL MATCH (o:Person)-[k2:KNOWS_ABOUT]->(s) WHERE o.handle <> $h
            WITH s, k, sum(coalesce(k2.score, 0)) AS others
            RETURN s.key AS key, s.kind AS kind, k.score AS score, k.last_ts AS last_ts,
                   k.score / (k.score + others) AS share
            ORDER BY k.score DESC LIMIT 10
            """,
            {"h": person["handle"]},
        )
        for k in knows:
            sg.edge(pid, "KNOWS_ABOUT", sg.node("System", k["key"], name=k["key"], kind=k["kind"]), score=k["score"])
        decided = self.store.records(
            "MATCH (:Person {handle: $h})-[:DECIDED]->(d:Decision) RETURN d.uid AS uid, d.title AS title, d.ts AS ts ORDER BY d.ts DESC LIMIT 8",
            {"h": person["handle"]},
        )
        for d in decided:
            sg.edge(pid, "DECIDED", sg.node("Decision", d["uid"], title=d["title"], ts=d["ts"]))
        active = self.store.records(
            """
            MATCH (:Person {handle: $h})-[:AUTHORED|ASSIGNED]->(s:Source) WHERE s.kind IN ['pr', 'issue'] AND s.state = 'open'
            RETURN s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts ORDER BY s.ts DESC LIMIT 8
            """,
            {"h": person["handle"]},
        )
        evidence = [Evidence(a["uid"], a["title"], a["url"], a["kind"], a["ts"]) for a in active]
        for a in active:
            sg.edge(pid, "AUTHORED", sg.node("Source", a["uid"], kind=a["kind"], title=a["title"], ts=a["ts"], url=a["url"]))
        data = {
            "person": person,
            "expertise": [{"system": k["key"], "share_of_knowledge": round(k["share"] or 0, 2), "last_active": fmt_date(k["last_ts"])} for k in knows],
            "decisions": [{"uid": d["uid"], "title": d["title"], "date": fmt_date(d["ts"])} for d in decided],
            "open_work": [{"uid": a["uid"], "kind": a["kind"], "title": a["title"]} for a in active],
            "would_leave_gap_in": [k["key"] for k in knows if (k["share"] or 0) >= 0.5],
        }
        return ToolResult(data, f"profile of {person['handle']}", sg, evidence)

    def timeline(self, system: str, since: str | None = None, until: str | None = None, limit: int = 15) -> ToolResult:
        """What happened to a system in a time window (merged PRs, issues, decisions) — the 'as of' view."""
        target = self.resolve_system(system)
        lo, hi = parse_date(since) or 0, parse_date(until) or 2**62
        rel = "(s)-[:TOUCHES]->(:System {key: $key})" if target["kind"] != "repo" else "(s)-[:IN_REPO]->(:System {key: $key})"
        events = self.store.records(
            f"""
            MATCH (s:Source) WHERE s.kind IN ['pr', 'issue', 'discussion'] AND s.ts >= $lo AND s.ts <= $hi AND {rel}
            RETURN s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts, s.state AS state, s.author AS author
            ORDER BY s.ts DESC LIMIT $k
            """,
            {"key": target["key"], "lo": lo, "hi": hi, "k": limit},
        )
        decisions = self.store.records(
            """
            MATCH (d:Decision)-[:AFFECTS]->(:System {key: $key}) WHERE d.ts >= $lo AND d.ts <= $hi
            RETURN d.uid AS uid, d.title AS title, d.ts AS ts ORDER BY d.ts DESC LIMIT 10
            """,
            {"key": target["key"], "lo": lo, "hi": hi},
        )
        sg = SubGraph()
        sid = sg.node("System", target["key"], name=target["key"], kind=target["kind"])
        for e in events:
            sg.edge(sg.node("Source", e["uid"], kind=e["kind"], title=e["title"], ts=e["ts"], url=e["url"]), "TOUCHES", sid)
        for d in decisions:
            sg.edge(sg.node("Decision", d["uid"], title=d["title"], ts=d["ts"]), "AFFECTS", sid)
        evidence = [Evidence(e["uid"], e["title"], e["url"], e["kind"], e["ts"]) for e in events]
        data = {
            "system": target["key"],
            "window": {"since": since, "until": until},
            "events": [
                {
                    "uid": e["uid"],
                    "kind": e["kind"],
                    "title": e["title"],
                    "date": fmt_date(e["ts"]),
                    "state": e["state"],
                    "author": e["author"],
                }
                for e in events
            ],
            "decisions": [{"uid": d["uid"], "title": d["title"], "date": fmt_date(d["ts"])} for d in decisions],
        }
        return ToolResult(data, f"{len(events)} events, {len(decisions)} decisions", sg, evidence)

    def connect(self, a: str, b: str) -> ToolResult:
        """Shortest path between two entities (people, systems, PR/issue uids)."""
        ea, eb = self._entity(a), self._entity(b)
        rows = self.store.ro_query(
            f"MATCH (a:{ea[0]} {{{ea[1]}: $a}}), (b:{eb[0]} {{{eb[1]}: $b}}) "
            "RETURN shortestPath((a)-[:AUTHORED|REVIEWED|MERGED|TOUCHES|IN_REPO|PART_OF|DEPENDS_ON|REPLY_TO|CLOSES|REFERENCES|DECIDED|DECIDED_IN|AFFECTS*..8]-(b))",
            {"a": ea[2], "b": eb[2]},
        )
        sg = SubGraph()
        if not rows or rows[0][0] is None:
            return ToolResult({"path": None, "note": "no connection within 8 hops"}, "no path", sg)
        path = rows[0][0]
        sg.add(path)
        steps = []
        evidence = []
        for n in path.nodes():
            label = n.labels[0]
            p = n.properties
            steps.append({"type": label, "id": p.get("uid") or p.get("key") or p.get("handle"), "title": p.get("title") or p.get("name")})
            if label == "Source":
                evidence.append(Evidence(p["uid"], p.get("title", ""), p.get("url", ""), p.get("kind", ""), p.get("ts")))
        rels = [e.relation for e in path.edges()]
        return ToolResult({"path": steps, "relationships": rels, "hops": len(rels)}, f"{len(rels)}-hop path", sg, evidence)

    def _entity(self, ref: str) -> tuple[str, str, str]:
        ref = ref.strip()
        looks_like_source = "#" in ref or (":" in ref and ref.split(":", 1)[1].endswith(".md"))
        if looks_like_source and self.store.records("MATCH (s:Source {uid: $u}) RETURN s.uid AS u", {"u": ref}):
            return ("Source", "uid", ref)
        if ref.startswith("@") or self.store.records("MATCH (p:Person {handle: $h}) RETURN p.handle AS h", {"h": ref}):
            return ("Person", "handle", self.resolve_person(ref)["handle"])
        return ("System", "key", self.resolve_system(ref)["key"])

    def details(self, uid: str) -> ToolResult:
        """Open one PR/issue/discussion/doc (or a decision) by uid: content, decisions made there, and links."""
        uid = uid.strip().strip("\"'`")
        if "::d" in uid:
            sg = SubGraph()
            evidence: list[Evidence] = []
            detail = self._decision_detail(uid, sg, evidence)
            if detail.get("missing"):
                raise ToolError(f"no decision {uid!r}")
            return ToolResult({"decision": detail}, f"decision {uid}", sg, evidence)
        rows = self.store.records(
            "MATCH (s:Source {uid: $u}) RETURN s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts, "
            "s.state AS state, s.author AS author, substring(coalesce(s.body, ''), 0, 2500) AS body",
            {"u": uid},
        )
        if not rows:
            raise ToolError(f"no PR/issue/doc with uid {uid!r}; uids look like 'owner/repo#123'")
        src = rows[0]
        sg = SubGraph()
        sid = sg.node("Source", src["uid"], kind=src["kind"], title=src["title"], ts=src["ts"], url=src["url"])
        evidence = [Evidence(src["uid"], src["title"], src["url"], src["kind"], src["ts"])]
        decisions = [r["uid"] for r in self.store.records("MATCH (d:Decision)-[:DECIDED_IN]->(:Source {uid: $u}) RETURN d.uid AS uid ORDER BY uid", {"u": uid})]
        decision_details = [self._decision_detail(d, sg, evidence) for d in decisions]
        links = self.store.records(
            """
            MATCH (:Source {uid: $u})-[r:CLOSES|REFERENCES]-(o:Source) WHERE o.kind IN ['pr', 'issue', 'discussion']
            RETURN type(r) AS rel, o.uid AS uid, o.title AS title, o.kind AS kind, o.url AS url, o.ts AS ts LIMIT 10
            """,
            {"u": uid},
        )
        for link in links:
            sg.edge(sid, link["rel"], sg.node("Source", link["uid"], kind=link["kind"], title=link["title"], ts=link["ts"], url=link["url"]))
            evidence.append(Evidence(link["uid"], link["title"], link["url"], link["kind"], link["ts"]))
        people = self.store.records("MATCH (p:Person)-[r:AUTHORED|REVIEWED|MERGED]->(:Source {uid: $u}) RETURN p.handle AS handle, type(r) AS role", {"u": uid})
        for p in people:
            sg.edge(sg.node("Person", p["handle"], name=p["handle"]), p["role"], sid)
        data = {
            **{k: src[k] for k in ("uid", "kind", "title", "state", "author", "body")},
            "date": fmt_date(src["ts"]),
            "people": [{"handle": p["handle"], "role": p["role"].lower()} for p in people],
            "decisions": decision_details,
            "links": [{"rel": link["rel"].lower(), "uid": link["uid"], "title": link["title"]} for link in links],
        }
        return ToolResult(data, f"{src['kind']} {uid} ({len(decisions)} decisions)", sg, evidence)

    def suggest_owners(self, text: str, limit: int = 3) -> ToolResult:
        """Who should handle a new issue? Similar past PRs -> the modules they touched -> experts on those modules."""
        from cairn.agent.router import find_systems  # local import: router imports this module
        from cairn.extract.drift import search_terms

        weights: dict[str, float] = {}
        mentioned = find_systems(self, text)
        explicit = {k for k in mentioned if ":" in k}
        # Any mention (repo or one of its modules) scopes the similarity search to that repo.
        repos = sorted({k.split(":", 1)[0] for k in mentioned})
        terms = search_terms(text, limit=10)
        similar = (
            self.store.records(
                """
                CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score
                WHERE node.kind IN ['pr', 'issue'] AND ($repos = [] OR node.repo IN $repos)
                RETURN node.uid AS uid, node.kind AS kind, node.title AS title, node.url AS url, node.ts AS ts, score
                ORDER BY score DESC LIMIT 15
                """,
                {"q": terms, "repos": repos},
            )
            if terms
            else []
        )
        if similar:
            touched = self.store.records(
                "MATCH (s:Source)-[:TOUCHES]->(m:System) WHERE s.uid IN $uids AND m.kind = 'code' RETURN s.uid AS uid, m.key AS key",
                {"uids": [s["uid"] for s in similar]},
            )
            score_of = {s["uid"]: s["score"] for s in similar}
            for t in touched:
                weights[t["key"]] = weights.get(t["key"], 0.0) + score_of[t["uid"]]
        for key in explicit:
            weights.setdefault(key, 0.0)
        if not weights and repos:
            weights = {r: 1.0 for r in repos}
        if not weights:
            return ToolResult({"systems": [], "note": "no similar past work or system mention found"}, "no match", SubGraph())
        # Modules the issue names outright come first; similar past work orders the rest.
        ranked = sorted(weights.items(), key=lambda kv: (kv[0] not in explicit, -kv[1]))[:2]
        sg = SubGraph()
        evidence = [Evidence(s["uid"], s["title"], s["url"], s["kind"], s["ts"]) for s in similar[:3]]
        out = []
        for key, weight in ranked:
            res = self.who_knows(key, limit=limit)
            sg.merge(res.subgraph)
            evidence += res.evidence
            out.append({"system": key, "evidence_weight": round(weight, 2), "experts": res.data.get("experts", [])})
        for s in similar[:3]:
            sg.node("Source", s["uid"], kind=s["kind"], title=s["title"], ts=s["ts"], url=s["url"])
        data = {
            "systems": out,
            "similar_past_work": [{"uid": s["uid"], "title": s["title"], "kind": s["kind"], "date": fmt_date(s["ts"])} for s in similar[:5]],
            "method": "explicit mentions + modules touched by similar past PRs/issues, then expertise ranking",
        }
        return ToolResult(data, f"{len(out)} likely systems", sg, evidence)

    def drift_report(self, repo: str | None = None) -> ToolResult:
        rows = self.store.records(
            """
            MATCH (c:Source)-[ch:CHALLENGES]->(p:Premise)<-[:BECAUSE]-(d:Decision)
            WHERE $repo IS NULL OR d.repo = $repo
            RETURN d.uid AS decision, d.title AS title, d.ts AS dts, p.uid AS puid, p.text AS premise,
                   c {.uid, .title, .url, .kind, .ts} AS by, ch.quote AS quote, ch.verdict AS verdict, ch.explanation AS why
            ORDER BY c.ts DESC LIMIT 20
            """,
            {"repo": repo},
        )
        sg = SubGraph()
        evidence = []
        for r in rows:
            did = sg.node("Decision", r["decision"], title=r["title"], ts=r["dts"], drift=True)
            pid = sg.node("Premise", r["puid"], text=r["premise"])
            sg.edge(did, "BECAUSE", pid)
            cid = sg.node("Source", r["by"]["uid"], kind=r["by"]["kind"], title=r["by"]["title"], ts=r["by"]["ts"], url=r["by"]["url"])
            sg.edge(cid, "CHALLENGES", pid, verdict=r["verdict"])
            evidence.append(Evidence(r["by"]["uid"], r["by"]["title"], r["by"]["url"], r["by"]["kind"], r["by"]["ts"], r["quote"]))
        data = {
            "drifted": [
                {
                    "decision": r["decision"],
                    "title": r["title"],
                    "decided": fmt_date(r["dts"]),
                    "premise": r["premise"],
                    "challenged_by": r["by"]["uid"],
                    "on": fmt_date(r["by"]["ts"]),
                    "verdict": r["verdict"],
                    "quote": r["quote"],
                    "why": r["why"],
                }
                for r in rows
            ]
        }
        return ToolResult(data, f"{len(rows)} challenged premises", sg, evidence)

    def teams(self) -> ToolResult:
        """Infer de-facto teams: label propagation over the people↔people WORKS_WITH projection."""
        communities = self.store.records(
            """
            CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['WORKS_WITH'], maxIterations: 30})
            YIELD node, communityId
            WITH communityId, collect(node.handle) AS people WHERE size(people) >= 2
            RETURN communityId AS team, people ORDER BY size(people) DESC LIMIT 8
            """,
            timeout_ms=30_000,
        )
        rows = []
        for c in communities:
            # Separate query per team (rather than UNWIND + filtered MATCH after aggregation; see list_systems).
            focus = self.store.records(
                "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE p.handle IN $people AND s.kind = 'code' "
                "RETURN s.key AS key, sum(k.score) AS weight ORDER BY weight DESC LIMIT 8",
                {"people": c["people"]},
            )
            rows.append({"team": c["team"], "people": c["people"], "systems": [f["key"] for f in focus]})
        sg = SubGraph()
        teams = []
        for r in rows[:8]:
            teams.append({"people": r["people"], "focus_systems": r["systems"]})
            for p in r["people"][:8]:
                pid = sg.node("Person", p, name=p, team=r["team"])
                for s_key in r["systems"][:3]:
                    sg.edge(pid, "KNOWS_ABOUT", sg.node("System", s_key, name=s_key))
        return ToolResult(
            {"teams": teams, "method": "label propagation over WORKS_WITH (who reviews/replies to whom)"},
            f"{len(teams)} inferred teams",
            sg,
        )


# ---------------------------------------------------------------------- schema for the LLM

ToolFn = Callable[..., ToolResult]


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_S = {"type": "string"}

TOOL_SPECS: list[dict[str, Any]] = [
    _fn("search", "Full-text search over decisions, PRs, issues, docs and systems. Use to orient yourself.", {"query": _S}, ["query"]),
    _fn(
        "why",
        "Explain WHY something is the way it is: decisions on a topic with their premises, verbatim evidence quotes, "
        "rejected alternatives, who decided, and whether later evidence challenged them (drift).",
        {"topic": _S},
        ["topic"],
    ),
    _fn(
        "who_knows",
        "Rank people by expertise on a system (repo or module), with the PRs that justify the ranking.",
        {"system": {**_S, "description": "repo like 'FalkorDB/QueryWeaver' or module key/name like 'api/routes'"}},
        ["system"],
    ),
    _fn(
        "impact",
        "Blast radius of a system: dependent repos (transitive), who to contact, open issues/PRs, relevant decisions.",
        {"system": _S},
        ["system"],
    ),
    _fn(
        "bus_factor",
        "Find modules whose knowledge is concentrated in one person, and bridge people.",
        {"repo": {**_S, "description": "optional owner/name"}},
        [],
    ),
    _fn("person_profile", "What a person knows, decided and is working on; what would be lost if they left.", {"handle": _S}, ["handle"]),
    _fn(
        "timeline",
        "Events and decisions on a system within a date window (YYYY-MM-DD). Use for 'what changed' or 'as of' questions.",
        {"system": _S, "since": _S, "until": _S},
        ["system"],
    ),
    _fn(
        "connect",
        "Shortest path between two entities (people, systems, or uids like 'FalkorDB/QueryWeaver#123').",
        {"a": _S, "b": _S},
        ["a", "b"],
    ),
    _fn("drift_report", "Decisions whose premises were contradicted by later evidence.", {"repo": _S}, []),
    _fn("teams", "Inferred de-facto teams (community detection over who works on what).", {}, []),
    _fn(
        "details",
        "Open one item by uid: a PR/issue/doc ('owner/repo#123') with its text, people, links and the decisions made there, or a decision ('owner/repo#123::d1').",
        {"uid": _S},
        ["uid"],
    ),
    _fn(
        "suggest_owners",
        "Given an issue/bug/feature description, find the systems it most likely touches (via similar past work) and who should handle it.",
        {"text": _S},
        ["text"],
    ),
    _fn("list_systems", "List known systems (repos and modules) with activity counts.", {"repo": _S}, []),
]


def dispatch(tools: Tools, name: str, args: dict[str, Any]) -> ToolResult:
    fn: ToolFn | None = getattr(tools, name, None) if name in {t["function"]["name"] for t in TOOL_SPECS} else None
    if fn is None:
        raise ToolError(f"unknown tool {name!r}")
    try:
        return fn(**args)
    except TypeError as exc:
        raise ToolError(f"bad arguments for {name}: {exc}") from exc
