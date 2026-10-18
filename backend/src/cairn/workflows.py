"""Three moments of need, each a multi-hop walk over the graph that ends in a draft a human can approve.

- change_impact:   "I'm about to change X": what breaks, who must know, which decisions constrain the change.
- incident_memory: "X is broken": similar past incidents, what fixed them, who handled them, which runbooks are stale.
- handoff_plan:    "Y is leaving": what only Y knows, open work, decisions to re-own, and suggested successors.
"""

from __future__ import annotations

import re
from typing import Any

from cairn.agent.tools import Evidence, ToolError, ToolResult, Tools, _stem, _words, fmt_date
from cairn.graph.client import SubGraph

_INCIDENT_KINDS = ("postmortem", "ticket", "thread", "issue")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "it",
        "this",
        "that",
        "with",
        "from",
        "at",
        "by",
        "be",
        "has",
        "have",
        "had",
        "not",
        "no",
        "our",
        "we",
        "they",
    ]
)


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if w not in _STOP}


def _name(key: str) -> str:
    return key.split(":")[-1].split("/")[-1] if ":" in key else key.split("/")[-1]


# --------------------------------------------------------------------------- change impact


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def change_impact(tools: Tools, system: str, change: str = "") -> ToolResult:
    impact = tools.impact(system)
    target = impact.data["system"]
    sg = SubGraph()
    sg.merge(impact.subgraph)
    evidence = list(impact.evidence)
    dependents = [d["repo"] for d in impact.data["depended_on_by"]]

    # Decisions that constrain the system: changing it may undo a deliberate choice.
    constraints = []
    for d in tools.store.records(
        "MATCH (d:Decision)-[:AFFECTS]->(:System {key: $k}) WHERE coalesce(d.status, 'active') <> 'superseded' RETURN d.uid AS uid ORDER BY d.ts DESC LIMIT 4",
        {"k": target["key"]},
    ):
        detail = tools._decision_detail(d["uid"], sg, evidence)
        constraints.append(
            {
                "uid": detail["uid"],
                "title": detail["title"],
                "date": detail["date"],
                "reasons": [p["claim"] for p in detail["premises"][:3]],
                "drift_detected": detail["drift_detected"],
            }
        )

    # Who to tell: owning team and current experts for the system and everything downstream.
    notify: list[dict[str, Any]] = []
    for key in [target["key"], *dependents]:
        experts = tools.who_knows(key, limit=2).data.get("experts", []) if key else []
        team = impact.data.get("owning_teams", {}).get(key)
        notify.append({"system": key, "team": team, "people": [e["handle"] for e in experts], "why": "owner" if key == target["key"] else "depends on it"})

    lines = [f"**Heads-up: planned change to `{target['key']}`**", ""]
    if change:
        lines += [f"Change: {change}", ""]
    if dependents:
        lines += ["Systems that depend on it (directly or transitively):", *[f"- `{d}`" for d in dependents], ""]
    if constraints:
        lines += ["Decisions this change should respect:"]
        for c in constraints:
            lines.append(f"- {c['title']} ({c['date']})" + (" — reasons since challenged" if c["drift_detected"] else ""))
            lines += [f"  - because: {r}" for r in c["reasons"]]
        lines.append("")
    if impact.data["open_items"]:
        lines += ["Open work that may collide:", *[f"- {o['title']} ({o['kind']})" for o in impact.data["open_items"][:6]], ""]
    lines += ["Please review before the change ships:"]
    for n in notify:
        who = ", ".join(f"`{p}`" for p in n["people"]) or "no recorded experts"
        lines.append(f"- `{n['system']}` ({n['why']}){' · team ' + n['team'] if n['team'] else ''}: {who}")
    data = {
        "system": target,
        "change": change,
        "dependents": impact.data["depended_on_by"],
        "constraints": constraints,
        "open_items": impact.data["open_items"],
        "notify": notify,
        "draft": "\n".join(lines),
    }
    summary = f"{_n(len(dependents), 'dependent')}, {_n(len(constraints), 'constraining decision')}, {_n(len(notify), 'group')} to notify"
    return ToolResult(data, summary, sg, evidence)


# --------------------------------------------------------------------------- decision guard

# Words that signal a change is undoing or replacing something, compared by stem.
_REVERSAL_STEMS = frozenset(_stem(w) for w in ("revert", "replace", "remove", "drop", "delete", "deprecate", "migrate", "rewrite", "switch", "disable", "rollback"))


def decision_guard(tools: Tools, title: str, body: str = "", files: list[str] | None = None) -> ToolResult:
    """A new PR or ticket arrives: which active decisions does it touch, and does it look like it undoes one?"""
    from cairn.agent.router import find_systems  # local import: router imports this module

    change_text = f"{title}\n{body}"
    if not change_text.strip():
        raise ToolError("give the change a title or description")
    systems = find_systems(tools, f"{change_text}\n" + "\n".join(files or []))[:4]
    change_stems = _stems(change_text)
    reverses = bool({_stem(w) for w in _words(change_text)} & _REVERSAL_STEMS)

    sg = SubGraph()
    evidence: list[Evidence] = []
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in systems:
        # Words that only name the system ("driver", "app") say nothing about whether a decision is involved.
        system_words = _stems(re.sub(r"[:/_-]", " ", key))
        rows = tools.store.records(
            "MATCH (d:Decision)-[:AFFECTS]->(:System {key: $k}) WHERE coalesce(d.status, 'active') <> 'superseded' RETURN d.uid AS uid ORDER BY d.ts DESC LIMIT 6",
            {"k": key},
        )
        for row in rows:
            if row["uid"] in seen:
                continue
            seen.add(row["uid"])
            detail = tools._decision_detail(row["uid"], sg, evidence)
            if detail.get("missing"):
                continue
            reasons = [p["claim"] for p in detail["premises"][:3]]
            topic = _stems(" ".join([detail["title"], detail.get("summary") or "", *reasons]))
            overlap = sorted((change_stems & topic) - system_words)
            drifted = bool(detail["drift_detected"])
            # Relevant only when the change really talks about the same thing; this keeps the guard quiet otherwise.
            if len(overlap) < 2 and not (drifted and overlap):
                continue
            level = "conflict" if reverses and len(overlap) >= 2 else "challenged" if drifted else "respect"
            findings.append(
                {
                    "uid": detail["uid"],
                    "title": detail["title"],
                    "date": detail["date"],
                    "level": level,
                    "system": key,
                    "deciders": detail["deciders"],
                    "reasons": reasons,
                    "drift_detected": drifted,
                    "matched_terms": overlap[:6],
                }
            )
    order = {"conflict": 0, "challenged": 1, "respect": 2}
    findings.sort(key=lambda f: order[f["level"]])
    verdict = "review" if any(f["level"] == "conflict" for f in findings) else "heads_up" if findings else "clear"

    lines = [f"**Decision check for: {title}**", ""]
    if not findings:
        lines.append("No active decisions in the graph conflict with or constrain this change." if systems else "No known system matched this change.")
    for f in findings:
        label = {"conflict": "May reverse", "challenged": "Reasons challenged", "respect": "Constrains this change"}[f["level"]]
        who = ", ".join(f"`{h}`" for h in f["deciders"]) or "unknown"
        lines.append(f"- **{label}:** {f['title']} ({f['date']}), decided by {who}")
        lines += [f"  - because: {r}" for r in f["reasons"]]
    data = {"title": title, "systems": systems, "verdict": verdict, "reverses": reverses, "findings": findings, "draft": "\n".join(lines)}
    summary = f"{verdict.replace('_', ' ')}: {_n(len(findings), 'decision')} flagged across {_n(len(systems), 'system')}"
    return ToolResult(data, summary, sg, evidence)


# --------------------------------------------------------------------------- incident memory


def incident_memory(tools: Tools, description: str, limit: int = 5) -> ToolResult:
    from cairn.extract.drift import search_terms

    terms = search_terms(description, limit=10)
    if not terms:
        raise ToolError("describe the incident in a few words (symptoms, system)")
    # Wide pool, then rank by topic overlap: generic words ("events") otherwise crowd out the real incident.
    # Replies count toward their thread, since on-call details usually live in the replies.
    raw = tools.store.records(
        """
        CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score
        OPTIONAL MATCH (node)-[:REPLY_TO]->(parent:Source)
        WITH coalesce(parent, node) AS root, score
        WHERE root.kind IN $kinds
        RETURN root.uid AS uid, root.kind AS kind, root.title AS title, root.url AS url, root.ts AS ts, root.labels AS labels,
               root.author AS author, substring(coalesce(root.body, ''), 0, 3000) AS body, max(score) AS score
        ORDER BY score DESC LIMIT 250
        """,
        {"q": terms, "kinds": list(_INCIDENT_KINDS)},
    )
    replies: dict[str, str] = {}
    for r in tools.store.records(
        "MATCH (c:Source)-[:REPLY_TO]->(t:Source) WHERE t.uid IN $u RETURN t.uid AS uid, collect(c.body) AS bodies",
        {"u": [h["uid"] for h in raw]},
    ):
        replies[r["uid"]] = "\n".join(b for b in r["bodies"] if b)[:4000]
    hits = [{**h, "body": f"{h['body']}\n{replies.get(h['uid'], '')}"} for h in raw]
    want = _stems(description)

    def incidentness(h: dict[str, Any]) -> float:
        labels = {str(x).lower() for x in (h["labels"] or [])}
        text = f"{h['title']} {h['body']}".lower()
        signal = 2.0 if h["kind"] == "postmortem" else 0.0
        signal += 1.5 if labels & {"incident", "sev1", "sev2", "#incidents", "outage", "bug"} else 0.0
        signal += 1.0 if any(w in text for w in ("outage", "incident", "root cause", "recovered", "rollback", "sev1", "postmortem")) else 0.0
        overlap = len(want & _stems(f"{h['title']} {h['body']}"))
        return overlap + signal

    ranked = sorted(hits, key=incidentness, reverse=True)
    similar = [h for h in ranked if incidentness(h) >= 3][:limit]
    sg = SubGraph()
    evidence: list[Evidence] = []
    out: list[dict[str, Any]] = []
    for h in similar:
        sid = sg.node("Source", h["uid"], kind=h["kind"], title=h["title"], ts=h["ts"], url=h["url"])
        evidence.append(Evidence(h["uid"], h["title"], h["url"], h["kind"], h["ts"]))
        responders = tools.store.records(
            """
            MATCH (p:Person)-[:AUTHORED|ASSIGNED]->(x:Source) WHERE x.uid = $u OR (x)-[:REPLY_TO]->(:Source {uid: $u})
            RETURN p.handle AS handle, p.left AS left, count(x) AS n ORDER BY n DESC LIMIT 4
            """,
            {"u": h["uid"]},
        )
        for r in responders:
            sg.edge(sg.node("Person", r["handle"], name=r["handle"], left=r["left"]), "AUTHORED", sid)
        systems = [r["key"] for r in tools.store.records("MATCH (:Source {uid: $u})-[:TOUCHES|MENTIONS]->(s:System) RETURN s.key AS key", {"u": h["uid"]})]
        for key in systems:
            sg.edge(sid, "TOUCHES", sg.node("System", key, name=key))
        resolution = _resolution(h["body"])
        out.append(
            {
                "uid": h["uid"],
                "kind": h["kind"],
                "title": h["title"],
                "date": fmt_date(h["ts"]),
                "systems": systems,
                "handled_by": [{"handle": r["handle"], "left_company": fmt_date(r["left"])} for r in responders],
                "what_fixed_it": resolution,
            }
        )

    # Systems ordered by how many of the similar incidents involve them: the most implicated system comes first.
    counts: dict[str, int] = {}
    for o in out:
        for k in o["systems"]:
            counts[k] = counts.get(k, 0) + 1
    systems = sorted(counts, key=lambda k: (-counts[k], k))
    runbooks = []
    for rb in tools.store.records(
        """
        MATCH (r:Source)-[:TOUCHES|MENTIONS]->(s:System) WHERE r.kind = 'runbook' AND s.key IN $keys
        OPTIONAL MATCH (author:Person)-[:AUTHORED]->(r)
        RETURN DISTINCT r.uid AS uid, r.title AS title, r.url AS url, r.ts AS ts, author.handle AS author, author.left AS author_left
        """,
        {"keys": systems},
    ):
        flags = []
        if rb["author_left"]:
            flags.append(f"written by {rb['author']}, who left on {fmt_date(rb['author_left'])}")
        newer = [o for o in out if o["kind"] == "postmortem" and (o["date"] or "") > (fmt_date(rb["ts"]) or "")]
        if newer:
            flags.append(f"older than the postmortem '{newer[0]['title']}'")
        drifted = tools.store.records(
            "MATCH (d:Decision {drift: true})-[:AFFECTS]->(s:System) WHERE s.key IN $keys AND d.ts >= $ts RETURN d.title AS t LIMIT 1",
            {"keys": systems, "ts": rb["ts"] or 0},
        )
        if drifted:
            flags.append(f"a decision it relies on was later challenged ('{drifted[0]['t']}')")
        rid = sg.node("Source", rb["uid"], kind="runbook", title=rb["title"], ts=rb["ts"], url=rb["url"])
        evidence.append(Evidence(rb["uid"], rb["title"], rb["url"], "runbook", rb["ts"]))
        for key in systems:
            sg.edge(rid, "TOUCHES", f"System:{key}")
        runbooks.append({"uid": rb["uid"], "title": rb["title"], "date": fmt_date(rb["ts"]), "possibly_outdated": bool(flags), "why": flags})

    available: list[dict[str, Any]] = []
    for key in systems[:3]:
        for e in tools.who_knows(key, limit=2).data.get("experts", []):
            if e["handle"] not in {a["handle"] for a in available}:
                available.append({"handle": e["handle"], "system": key, "last_active": e["last_active"]})
    data = {
        "similar_incidents": out,
        "systems": systems,
        "runbooks": runbooks,
        "who_to_page": available[:4],
        "note": None if out else "No similar past incident found in the graph.",
    }
    return ToolResult(data, f"{_n(len(out), 'similar incident')}, {_n(len(runbooks), 'runbook')}", sg, evidence)


def _resolution(body: str) -> str | None:
    """Pull the sentence that says how it was fixed, if the source states it."""
    for line in body.replace("\r", "").split("\n"):
        low = line.lower()
        if any(k in low for k in ("fixed", "resolved", "recovered", "rolled back", "rollback", "re-elected", "mitigat", "root cause", "workaround")):
            text = line.strip(" -*#")
            if len(text) > 12:
                return text[:300]
    return None


# --------------------------------------------------------------------------- handoff


def handoff_plan(tools: Tools, handle: str) -> ToolResult:
    profile = tools.person_profile(handle)
    person = profile.data["person"]
    sg = SubGraph()
    sg.merge(profile.subgraph)
    evidence = list(profile.evidence)
    areas = []
    for e in profile.data["expertise"]:
        if e["share_of_knowledge"] < 0.15:
            continue
        others = [x for x in tools.who_knows(e["system"], limit=4).data.get("experts", []) if x["handle"] != person["handle"]]
        docs = tools.store.records(
            """
            MATCH (:Person {handle: $h})-[:AUTHORED]->(d:Source)-[:TOUCHES|MENTIONS]->(:System {key: $k})
            WHERE d.kind IN ['doc', 'adr', 'rfc', 'runbook', 'postmortem', 'pr']
            RETURN d.uid AS uid, d.title AS title, d.kind AS kind, d.url AS url, d.ts AS ts ORDER BY ts DESC LIMIT 4
            """,
            {"h": person["handle"], "k": e["system"]},
        )
        for d in docs:
            evidence.append(Evidence(d["uid"], d["title"], d["url"], d["kind"], d["ts"]))
            sg.edge(f"Person:{person['handle']}", "AUTHORED", sg.node("Source", d["uid"], kind=d["kind"], title=d["title"], ts=d["ts"], url=d["url"]))
        areas.append(
            {
                "system": e["system"],
                "share_of_knowledge": e["share_of_knowledge"],
                "successor": others[0]["handle"] if others else None,
                "backup": others[1]["handle"] if len(others) > 1 else None,
                "read_first": [{"uid": d["uid"], "title": d["title"], "kind": d["kind"]} for d in docs],
            }
        )
    areas.sort(key=lambda a: -a["share_of_knowledge"])
    who = f"{person['handle']}" + (f" ({person['name']})" if person.get("name") else "")
    lines = [f"# Handoff plan: {who}", ""]
    if person.get("left_company"):
        lines += [f"Left the company on {person['left_company']}. This plan lists what they knew and who holds it now.", ""]
    lines += ["## Areas to hand over", ""]
    for a in areas:
        lines.append(f"### `{a['system']}`: {int(a['share_of_knowledge'] * 100)}% of recent activity")
        successor = f"- Successor: `{a['successor'] or 'nobody yet'}`"
        lines.append(successor + (f", backup `{a['backup']}`" if a["backup"] else ""))
        if a["read_first"]:
            lines += ["- Read first:", *[f"  - {d['title']} ({d['kind']})" for d in a["read_first"]]]
        lines.append("")
    if profile.data["decisions"]:
        lines += ["## Decisions to re-own", *[f"- {d['title']} ({d['date']})" for d in profile.data["decisions"]], ""]
    if profile.data["open_work"]:
        lines += ["## Open work to reassign", *[f"- {w['title']} ({w['kind']})" for w in profile.data["open_work"]], ""]
    lines += ["Generated by Cairn from the knowledge graph. People are named without @-mentions."]
    data = {
        "person": person,
        "areas": areas,
        "decisions_to_reown": profile.data["decisions"],
        "open_work": profile.data["open_work"],
        "draft": "\n".join(lines),
    }
    return ToolResult(data, f"handoff for {person['handle']}: {_n(len(areas), 'area')}", sg, evidence)
