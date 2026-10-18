"""Multi-hop questions: chains of graph steps where each step feeds the next.

A single tool call answers "who knows X" or "what breaks". These answer questions that need several hops, and each
returns a `trace` (the steps it took) so the UI can show how the answer was built:

- departed_decisions: people who left, the decisions they made, what those decisions affect, what depends on them.
- stale_runbooks: runbooks, their authors, newer postmortems on the same systems, decisions that were later challenged.
- trace_topic: sources about a topic, grouped by repo and author, plus the earlier work each one refers to.
"""

from __future__ import annotations

import re
from typing import Any

from cairn.agent.tools import Evidence, ToolError, ToolResult, Tools, _stem, _words, fmt_date
from cairn.graph.client import SubGraph
from cairn.workflows import _n, _stems

_TOPIC_NOISE = frozenset(
    [
        "which", "what", "who", "whom", "our", "their", "projects", "project", "repos", "repo", "repositories",
        "adopted", "adopt", "uses", "used", "use", "drove", "drive", "drives", "earlier", "refer", "refers",
        "referred", "release", "does", "did", "and", "the", "for", "with", "from", "that", "this", "about",
        "into", "have", "has", "had", "when", "where", "how",
        "fix",
        "fixes",
        "fixed",
        "broke",
        "broken",
        "break",
    ]
)


def _step(trace: list[dict[str, Any]], title: str, detail: str, count: int | None = None) -> None:
    trace.append({"n": len(trace) + 1, "title": title, "detail": detail, "count": count})


def _short(items: list[str], limit: int = 4) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


# --------------------------------------------------------------------------- decisions by people who left


def departed_decisions(tools: Tools) -> ToolResult:
    """Decisions made by people who have since left, what each one affects, and what still depends on it."""
    trace: list[dict[str, Any]] = []
    sg = SubGraph()
    evidence: list[Evidence] = []

    gone = tools.store.records("MATCH (p:Person) WHERE p.left IS NOT NULL RETURN p.handle AS handle, p.name AS name, p.left AS left ORDER BY p.left")
    names = [f"{g['handle']} (left {fmt_date(g['left'])})" for g in gone]
    _step(trace, "Find people who have left", _short(names) or "nobody has left", len(gone))
    if not gone:
        return ToolResult({"departed": [], "trace": trace}, "nobody has left", sg, evidence)

    rows = tools.store.records(
        "MATCH (p:Person)-[:DECIDED]->(d:Decision) WHERE p.left IS NOT NULL "
        "RETURN p.handle AS handle, d.uid AS uid, d.title AS title, d.ts AS ts, d.status AS status, coalesce(d.drift, false) AS drift ORDER BY d.ts"
    )
    _step(trace, "Find the decisions they made", _short([r["title"] for r in rows]) or "none recorded", len(rows))

    by_person: dict[str, dict[str, Any]] = {g["handle"]: {"handle": g["handle"], "left": fmt_date(g["left"]), "decisions": []} for g in gone}
    affected = 0
    dependents_total: set[str] = set()
    for r in rows:
        systems = [s["key"] for s in tools.store.records("MATCH (:Decision {uid: $u})-[:AFFECTS]->(s:System) RETURN s.key AS key", {"u": r["uid"]})]
        affected += len(systems)
        per_system = []
        for key in systems:
            deps = [d["key"] for d in tools.store.records("MATCH (d:System)-[:DEPENDS_ON*1..3]->(:System {key: $k}) RETURN DISTINCT d.key AS key", {"k": key})]
            dependents_total.update(deps)
            per_system.append({"system": key, "dependents": deps})
            sg.node("System", key, name=key)
            for dep in deps:
                sg.edge(sg.node("System", dep, name=dep), "DEPENDS_ON", f"System:{key}")
        src = tools.store.records(
            "MATCH (:Decision {uid: $u})-[:DECIDED_IN]->(s:Source) RETURN s.uid AS uid, s.title AS title, s.url AS url, s.kind AS kind, s.ts AS ts LIMIT 1",
            {"u": r["uid"]},
        )
        source_uid = src[0]["uid"] if src else None
        if src:
            evidence.append(Evidence(src[0]["uid"], src[0]["title"], src[0]["url"], src[0]["kind"], src[0]["ts"]))
            sg.node("Source", src[0]["uid"], kind=src[0]["kind"], title=src[0]["title"], ts=src[0]["ts"], url=src[0]["url"])
        did = sg.node("Decision", r["uid"], title=r["title"], ts=r["ts"], drift=r["drift"])
        sg.edge(sg.node("Person", r["handle"], name=r["handle"]), "DECIDED", did)
        for ps in per_system:
            sg.edge(did, "AFFECTS", f"System:{ps['system']}")
        by_person[r["handle"]]["decisions"].append(
            {
                "uid": r["uid"],
                "title": r["title"],
                "date": fmt_date(r["ts"]),
                "status": r["status"],
                "drifted": bool(r["drift"]),
                "source_uid": source_uid,
                "systems": per_system,
            }
        )
    _step(trace, "See what each decision affects", f"{_n(affected, 'system link')}", affected)
    _step(trace, "Follow dependencies to what still relies on them", _short(sorted(dependents_total)) or "nothing depends on them", len(dependents_total))

    departed = [p for p in by_person.values() if p["decisions"]]
    data = {"departed": departed, "trace": trace}
    summary = f"{_n(len(rows), 'decision')} by {_n(len(departed), 'person')} who left; {len(dependents_total)} systems depend on them"
    return ToolResult(data, summary, sg, evidence)


# --------------------------------------------------------------------------- stale runbooks


def stale_runbooks(tools: Tools) -> ToolResult:
    """Runbooks that may be out of date: author left, an incident postmortem came after, or a decision they rely on was challenged."""
    trace: list[dict[str, Any]] = []
    sg = SubGraph()
    evidence: list[Evidence] = []

    books = tools.store.records("MATCH (r:Source) WHERE r.kind = 'runbook' RETURN r.uid AS uid, r.title AS title, r.url AS url, r.ts AS ts ORDER BY r.ts")
    _step(trace, "Find the runbooks", _short([b["title"] for b in books]) or "none found", len(books))
    if not books:
        return ToolResult({"runbooks": [], "trace": trace}, "no runbooks in the graph", sg, evidence)

    out: list[dict[str, Any]] = []
    checked_authors: list[str] = []
    postmortems: set[str] = set()
    challenged: set[str] = set()
    for b in books:
        keys = [s["key"] for s in tools.store.records("MATCH (:Source {uid: $u})-[:TOUCHES|MENTIONS]->(s:System) RETURN DISTINCT s.key AS key", {"u": b["uid"]})]
        author = tools.store.records("MATCH (a:Person)-[:AUTHORED]->(:Source {uid: $u}) RETURN a.handle AS handle, a.left AS left LIMIT 1", {"u": b["uid"]})
        flags: list[str] = []
        if author:
            checked_authors.append(author[0]["handle"])
            if author[0]["left"]:
                flags.append(f"written by {author[0]['handle']}, who left on {fmt_date(author[0]['left'])}")
        newer = tools.store.records(
            "MATCH (pm:Source)-[:TOUCHES|MENTIONS]->(s:System) WHERE pm.kind = 'postmortem' AND pm.ts > $ts AND s.key IN $keys "
            "RETURN DISTINCT pm.uid AS uid, pm.title AS title, pm.url AS url, pm.ts AS ts ORDER BY pm.ts",
            {"ts": b["ts"] or 0, "keys": keys},
        )
        for pm in newer:
            postmortems.add(pm["title"])
            flags.append(f"older than the postmortem '{pm['title']}' ({fmt_date(pm['ts'])})")
            evidence.append(Evidence(pm["uid"], pm["title"], pm["url"], "postmortem", pm["ts"]))
            pid = sg.node("Source", pm["uid"], kind="postmortem", title=pm["title"], ts=pm["ts"], url=pm["url"])
            for key in keys:
                sg.edge(pid, "TOUCHES", sg.node("System", key, name=key))
        drifted = tools.store.records(
            "MATCH (d:Decision {drift: true})-[:AFFECTS]->(s:System) WHERE s.key IN $keys AND d.ts >= $ts RETURN DISTINCT d.title AS title LIMIT 2",
            {"keys": keys, "ts": b["ts"] or 0},
        )
        for d in drifted:
            challenged.add(d["title"])
            flags.append(f"a decision it relies on was later challenged ('{d['title']}')")
        rid = sg.node("Source", b["uid"], kind="runbook", title=b["title"], ts=b["ts"], url=b["url"])
        evidence.append(Evidence(b["uid"], b["title"], b["url"], "runbook", b["ts"]))
        for key in keys:
            sg.edge(rid, "TOUCHES", sg.node("System", key, name=key))
        if author:
            sg.edge(sg.node("Person", author[0]["handle"], name=author[0]["handle"]), "AUTHORED", rid)
        out.append(
            {
                "uid": b["uid"],
                "title": b["title"],
                "date": fmt_date(b["ts"]),
                "systems": keys,
                "author": author[0]["handle"] if author else None,
                "outdated": bool(flags),
                "why": flags,
            }
        )

    _step(trace, "Check who wrote each one and whether they left", _short(sorted(set(checked_authors))) or "no authors recorded", len(set(checked_authors)))
    _step(trace, "Look for newer postmortems on the same systems", _short(sorted(postmortems)) or "none", len(postmortems))
    _step(trace, "Look for decisions they rely on that were later challenged", _short(sorted(challenged)) or "none", len(challenged))
    out.sort(key=lambda r: (-len(r["why"]), r["title"]))
    stale = [r for r in out if r["outdated"]]
    return ToolResult({"runbooks": out, "trace": trace}, f"{len(stale)} of {_n(len(out), 'runbook')} may be out of date", sg, evidence)


# --------------------------------------------------------------------------- follow a topic across repos


def _named_repo(tools: Tools, text: str) -> str | None:
    """The tracked repo the text names, if any (longest name wins, so 'falkordb-py' beats 'falkordb')."""
    lowered = text.lower()
    best: str | None = None
    for row in tools.store.records("MATCH (s:System {kind: 'repo'}) RETURN s.key AS key"):
        short = row["key"].split("/")[-1].lower()
        if len(short) > 3 and re.search(rf"(?<![\w-]){re.escape(short)}(?![\w-])", lowered) and (best is None or len(short) > len(best.split("/")[-1])):
            best = row["key"]
    return best


def trace_topic(tools: Tools, topic: str, limit: int = 6) -> ToolResult:
    """Find the work about a topic, group it by repo and person, and follow each item to the earlier work it refers to."""
    trace: list[dict[str, Any]] = []
    sg = SubGraph()
    evidence: list[Evidence] = []

    # A repo named in the question ("falkordb-py's release") narrows the search to that repo.
    repo = _named_repo(tools, topic)
    repo_words = set(_words(repo.split("/")[-1])) if repo else set()
    words = [w for w in _words(topic) if w not in _TOPIC_NOISE and w not in repo_words and len(w) > 2]
    wanted = {_stem(w) for w in words}
    if not wanted:
        raise ToolError("name the topic to trace, for example 'the hiredis extra'")
    # Stems with a trailing * so 'bounding' also finds 'bound' and 'dependency' finds 'dependencies'.
    query = "|".join(f"{s}*" if len(s) > 3 else s for s in sorted(wanted))
    hits = tools.store.records(
        "CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score WHERE node.kind IN ['pr', 'issue', 'discussion'] "
        "AND ($repo IS NULL OR node.uid STARTS WITH $prefix) "
        "RETURN node.uid AS uid, node.kind AS kind, node.title AS title, node.url AS url, node.ts AS ts, node.state AS state, "
        "substring(coalesce(node.body, ''), 0, 600) AS snippet, score ORDER BY score DESC LIMIT 40",
        {"q": query, "repo": repo, "prefix": f"{repo}#" if repo else ""},
    )
    # Keep items that cover most of the topic's distinctive words, not ones that share a single common word.
    scored = []
    for h in hits:
        covered = _stems(f"{h['title']} {h['snippet']}") & wanted
        scored.append((len(covered) / len(wanted), h))
    best = max((s for s, _ in scored), default=0)
    keep = [h for s, h in scored if s >= max(0.5, best - 0.2)]
    keep.sort(key=lambda h: h["ts"] or 0)
    keep = keep[:limit]
    searched = " ".join(words)
    _step(trace, f"Search for '{searched}'", _short([h["title"] for h in keep]) or "nothing matched", len(keep))
    if not keep:
        return ToolResult({"topic": " ".join(words), "items": [], "trace": trace}, "nothing in the graph matches", sg, evidence)

    items: list[dict[str, Any]] = []
    for h in keep:
        repo = h["uid"].split("#")[0]
        people = tools.store.records("MATCH (p:Person)-[:AUTHORED]->(:Source {uid: $u}) RETURN p.handle AS handle", {"u": h["uid"]})
        links = tools.store.records(
            "MATCH (:Source {uid: $u})-[r:CLOSES|REFERENCES]-(o:Source) WHERE o.kind IN ['pr', 'issue', 'discussion'] "
            "RETURN type(r) AS rel, o.uid AS uid, o.title AS title, o.kind AS kind, o.url AS url, o.ts AS ts LIMIT 6",
            {"u": h["uid"]},
        )
        sid = sg.node("Source", h["uid"], kind=h["kind"], title=h["title"], ts=h["ts"], url=h["url"])
        evidence.append(Evidence(h["uid"], h["title"], h["url"], h["kind"], h["ts"]))
        for p in people:
            sg.edge(sg.node("Person", p["handle"], name=p["handle"]), "AUTHORED", sid)
        for link in links:
            sg.edge(sid, link["rel"], sg.node("Source", link["uid"], kind=link["kind"], title=link["title"], ts=link["ts"], url=link["url"]))
            evidence.append(Evidence(link["uid"], link["title"], link["url"], link["kind"], link["ts"]))
        items.append(
            {
                "uid": h["uid"],
                "repo": repo,
                "kind": h["kind"],
                "title": h["title"],
                "date": fmt_date(h["ts"]),
                "authors": [p["handle"] for p in people],
                "links": [{"rel": link["rel"].lower(), "uid": link["uid"], "title": link["title"], "date": fmt_date(link["ts"])} for link in links],
            }
        )
    repos = sorted({i["repo"] for i in items})
    who = sorted({a for i in items for a in i["authors"]})
    linked = sorted({link["uid"] for i in items for link in i["links"]})
    _step(trace, "Group by repository", _short(repos), len(repos))
    _step(trace, "Find who authored them", _short([f"@{w}" for w in who]) or "no authors recorded", len(who))
    _step(trace, "Follow references to earlier work", _short(linked) or "no references", len(linked))
    data = {"topic": " ".join(words), "items": items, "repos": repos, "authors": who, "trace": trace}
    return ToolResult(data, f"{_n(len(items), 'item')} across {_n(len(repos), 'repo')}", sg, evidence)


# --------------------------------------------------------------------------- answers (graph-only mode)


def _cite(ledger: Any, uid: str | None) -> str:
    if uid and uid in ledger.entries:
        return f" [{ledger.entries[uid]['ref']}]"
    return ""


def render_departed(r: ToolResult, ledger: Any) -> str:
    people = r.data.get("departed") or []
    if not people:
        return "Nobody who has left made a recorded decision."
    lines = ["Decisions made by people who have since left, and what still depends on them:"]
    for p in people:
        lines.append(f"\n**@{p['handle']}** (left {p['left']})")
        for d in p["decisions"]:
            note = " (challenged since)" if d["drifted"] else ""
            lines.append(f"- {d['title']} ({d['date']}){note}{_cite(ledger, d['source_uid'])}")
            for s in d["systems"]:
                deps = ", ".join(s["dependents"]) or "nothing else"
                lines.append(f"  - affects {s['system']}; depended on by {deps}")
    return "\n".join(lines)


def render_stale(r: ToolResult, ledger: Any) -> str:
    stale = [b for b in r.data.get("runbooks") or [] if b["outdated"]]
    if not stale:
        return "No runbook looks out of date."
    lines = ["Runbooks that may be out of date:"]
    for b in stale:
        lines.append(f"\n**{b['title']}** ({b['date']}){_cite(ledger, b['uid'])}")
        lines += [f"- {w}" for w in b["why"]]
    return "\n".join(lines)


def render_trace(r: ToolResult, ledger: Any) -> str:
    items = r.data.get("items") or []
    if not items:
        return "The graph has nothing on that topic."
    lines = [f"Found {_n(len(items), 'item')} across {_n(len(r.data['repos']), 'repo')}:"]
    for i in items:
        who = ", ".join(f"@{a}" for a in i["authors"]) or "unknown author"
        lines.append(f"- **{i['repo']}**: {i['title']} ({i['kind']}, {i['date']}) by {who}{_cite(ledger, i['uid'])}")
        for link in i["links"]:
            lines.append(f"  - {link['rel']} {link['uid']}: {link['title']}{_cite(ledger, link['uid'])}")
    return "\n".join(lines)
