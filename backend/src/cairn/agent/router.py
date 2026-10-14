"""Deterministic question router used when no agent LLM is available (no key, quota exhausted, provider down).

It recognises the intent with patterns, resolves entities by matching known system names and handles
in the question, runs the matching tool, and renders a templated answer with ledger citations. It is
deliberately simple; its job is to keep the product useful, not to compete with the LLM agent.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cairn.agent.tools import ToolResult, Tools

if TYPE_CHECKING:
    from cairn.agent.loop import Ledger

_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("act_wiki", re.compile(r"\b(update|refresh|publish|sync|regenerate)\b.*\b(wiki|knowledge ?base|docs?)\b", re.I)),
    ("act_route", re.compile(r"\b(route|triage|assign)\b.*#\s*\d+", re.I)),
    ("drift", re.compile(r"\b(drift|stale|outdated|still (true|valid|hold)|no longer|out of date)\b", re.I)),
    ("risk", re.compile(r"\b(bus factor|leaves?|left|quits?|single point|knowledge risk|concentrat\w*|handoff|hand off)\b", re.I)),
    ("teams", re.compile(r"\b(teams?|who works with|groups?)\b", re.I)),
    ("connect", re.compile(r"\b(connected|related|relationship|path)\b.*\b(between|to|and)\b", re.I)),
    ("who", re.compile(r"\bwho\b.*\b(knows?|expert|owns?|review|ask|maintains?|best|should|worked)\b|\bexperts?\b", re.I)),
    ("impact", re.compile(r"\b(break|breaks|impact|depends?|dependen\w*|blast radius|goes down|downstream|affects?)\b", re.I)),
    ("timeline", re.compile(r"\b(changed|timeline|history|since|recently|last (week|month|year)|as of|what happened)\b", re.I)),
    ("why", re.compile(r"\b(why|reason|rationale|decid\w*|decision|chose|choose|how come)\b", re.I)),
]
_STOPWORDS = re.compile(
    r"\b(why|did|do|does|we|they|the|a|an|is|are|was|were|use|using|what|who|how|come|of|in|on|for|to|it|this|that|"
    r"about|knows?|best|should|i|ask|decide|decided|decision|reason|choose|chose)\b",
    re.I,
)
_HANDLE = re.compile(r"@([A-Za-z0-9-]+)")


@dataclass
class _Catalog:
    systems: list[tuple[str, str]]  # (match text lowercased, key), longest first
    handles: dict[str, str]  # lowercased -> canonical
    loaded_at: float


_CATALOG: dict[int, _Catalog] = {}
_CATALOG_TTL = 300.0


def _catalog(tools: Tools) -> _Catalog:
    cached = _CATALOG.get(id(tools.store))
    if cached and time.monotonic() - cached.loaded_at < _CATALOG_TTL:
        return cached
    systems: list[tuple[str, str]] = []
    activity: dict[str, int] = {}
    rows = tools.store.records(
        "MATCH (s:System) WHERE s.kind IN ['repo', 'code'] OPTIONAL MATCH (x:Source)-[:TOUCHES|IN_REPO]->(s) "
        "RETURN s.key AS key, s.name AS name, s.path AS path, count(x) AS act"
    )
    for r in rows:
        activity[r["key"]] = r["act"]
    for r in rows:
        texts = {r["key"], r["name"], r["path"]}
        # "ingestion" should find "graphrag_sdk/ingestion": index a module's last segment too.
        if r["path"] and "/" in r["path"]:
            texts.add(r["path"].rsplit("/", 1)[1])
        for text in texts:
            if text and len(text) >= 3:
                systems.append((text.lower(), r["key"]))
    # Longest (most specific) text first; among equal texts, the most active system first.
    systems.sort(key=lambda t: (-len(t[0]), -activity.get(t[1], 0)))
    handles = {r["h"].lower(): r["h"] for r in tools.store.records("MATCH (p:Person) RETURN p.handle AS h")}
    cat = _Catalog(systems, handles, time.monotonic())
    _CATALOG[id(tools.store)] = cat
    return cat


def find_systems(tools: Tools, question: str) -> list[str]:
    q = question.lower()
    found: list[str] = []
    by_text: dict[str, list[str]] = {}
    for text, key in _catalog(tools).systems:  # longest text first
        by_text.setdefault(text, []).append(key)
    for text, keys in by_text.items():
        if re.search(rf"(?<![\w/-]){re.escape(text)}(?![\w/-])", q):
            # A repo's own name ("QueryWeaver") is a repo mention, even if a module shares the name.
            repo_keys = [k for k in keys if ":" not in k]
            # Otherwise keep every system sharing this text (e.g. 'api' in two repos); repo scoping below picks one.
            found.extend(k for k in (repo_keys or keys) if k not in found)
            q = q.replace(text, " ")  # don't let "api" re-match inside an already matched "api/routes"
    repos = [k for k in found if ":" not in k]
    modules = [k for k in found if ":" in k]
    # A bare word like "api" can match modules in several repos; if the question names a repo, keep its modules.
    if repos and (scoped := [m for m in modules if m.split(":", 1)[0] in repos]):
        modules = scoped
    # "QueryWeaver api/routes": a module mentioned alongside its repo is the more specific target.
    repos_with_module = {k.split(":", 1)[0] for k in modules}
    return modules + [k for k in repos if k not in repos_with_module]


def find_people(tools: Tools, question: str) -> list[str]:
    handles = _catalog(tools).handles
    out = [handles[h.lower()] for h in _HANDLE.findall(question) if h.lower() in handles]
    for word in re.findall(r"[A-Za-z0-9-]{3,}", question):
        if word.lower() in handles and handles[word.lower()] not in out:
            out.append(handles[word.lower()])
    return out


def intent_of(question: str) -> str:
    for name, pattern in _INTENTS:
        if pattern.search(question):
            return name
    return "search"


@dataclass
class Plan:
    intent: str
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def render(self, results: list[tuple[str, ToolResult]], ledger: Ledger) -> str:
        parts = [RENDERERS.get(name, _render_generic)(r, ledger) for name, r in results]
        return "\n\n".join(p for p in parts if p).strip() or "The graph returned no results for that question."


def route(tools: Tools, question: str, action_repos: list[str] | None = None) -> Plan:
    intent = intent_of(question)
    if intent == "act_wiki":
        return Plan(intent, [("propose_wiki_update", {"reason": question})])
    if intent == "act_route" and action_repos:
        number = int(re.search(r"#\s*(\d+)", question).group(1))  # type: ignore[union-attr]
        named = [r for r in action_repos if r.lower() in question.lower()]
        return Plan(intent, [("propose_issue_routing", {"repo": (named or action_repos)[0], "number": number})])
    systems = find_systems(tools, question)
    people = find_people(tools, question)
    topic = _STOPWORDS.sub(" ", question).strip(" ?.!") or question
    plan = Plan(intent)
    if intent == "drift":
        plan.calls.append(("drift_report", {}))
    elif intent == "risk":
        plan.calls.append(("person_profile", {"handle": people[0]}) if people else ("bus_factor", {}))
    elif intent == "teams":
        plan.calls.append(("teams", {}))
    elif intent == "connect" and len(systems) + len(people) >= 2:
        a, b = (people + systems)[:2]
        plan.calls.append(("connect", {"a": a, "b": b}))
    elif intent == "who" and systems:
        plan.calls.append(("who_knows", {"system": systems[0]}))
    elif intent == "impact" and systems:
        plan.calls.append(("impact", {"system": systems[0]}))
    elif intent == "timeline" and systems:
        plan.calls.append(("timeline", {"system": systems[0]}))
    elif intent == "why":
        plan.calls.append(("why", {"topic": topic}))
    elif people:
        plan.calls.append(("person_profile", {"handle": people[0]}))
    else:
        plan.calls.append(("search", {"query": topic}))
    return plan


# --------------------------------------------------------------------------- rendering


def _cite(ledger: Ledger, uid: str | None) -> str:
    if uid and uid in ledger.entries:
        return f" [{ledger.entries[uid]['ref']}]"
    return ""


def _render_why(r: ToolResult, ledger: Ledger) -> str:
    decisions = r.data.get("decisions") or []
    if not decisions:
        srcs = r.data.get("sources") or []
        if not srcs:
            return "I found no recorded decision or discussion on that topic."
        lines = ["No extracted decision matches, but these threads discuss it:"]
        lines += [f"- {s['title']} ({s['kind']}, {s.get('date')}){_cite(ledger, s['uid'])}" for s in srcs[:5]]
        return "\n".join(lines)
    out = []
    for d in decisions:
        lines = [f"**{d['title']}** ({d.get('date')}){_cite(ledger, d.get('decided_in'))}", d.get("summary") or ""]
        for p in d.get("premises", []):
            lines.append(f"- Because: {p['claim']}{_cite(ledger, p['source'])}")
            for c in p.get("challenged_by", []):
                lines.append(f"  - ⚠ Challenged on {c['date']}: “{c['quote']}”{_cite(ledger, c['source'])}")
        for rj in d.get("rejected_alternatives", []):
            lines.append(f"- Rejected {rj.get('option')}: {rj['claim']}{_cite(ledger, rj['source'])}")
        if d.get("deciders"):
            lines.append(f"- Decided by {', '.join('@' + h for h in d['deciders'])}")
        out.append("\n".join(x for x in lines if x))
    return "\n\n".join(out)


def _render_who(r: ToolResult, ledger: Ledger) -> str:
    experts = r.data.get("experts") or []
    if not experts:
        return f"No recorded activity on {r.data.get('system', {}).get('key', 'that system')}."
    lines = [f"Best people to ask about **{r.data['system']['key']}**:"]
    for e in experts:
        prs = "".join(_cite(ledger, p["uid"]) for p in e.get("recent_prs", [])[:2])
        lines.append(
            f"- @{e['handle']}: {int(e['share'] * 100)}% of recent activity; authored {e['authored_prs']} / reviewed {e['reviewed_prs']} PRs, "
            f"last active {e['last_active']}{prs}"
        )
    return "\n".join(lines)


def _render_impact(r: ToolResult, ledger: Ledger) -> str:
    d = r.data
    lines = [f"**{d['repo']}**", ""]
    if d["depended_on_by"]:
        lines.append("Depended on by: " + ", ".join(f"{x['repo']} (via {', '.join(x['via_package'])})" for x in d["depended_on_by"]))
    else:
        lines.append("No ingested repository depends on it.")
    lines.append("")
    for repo, people in d["contacts"].items():
        lines.append(f"- Contacts for {repo}: {', '.join('@' + p for p in people)}")
    if d["open_items"]:
        lines += ["", "Open work that may be affected:"]
        lines += [f"- {o['title']} ({o['kind']}, {o['date']}){_cite(ledger, o['uid'])}" for o in d["open_items"][:6]]
    return "\n".join(lines)


def _render_bus(r: ToolResult, ledger: Ledger) -> str:
    mods = r.data.get("at_risk_modules") or []
    if not mods:
        return "No module has knowledge concentrated in a single person."
    lines = ["Modules where knowledge is concentrated:"]
    lines += [f"- {m['system']}: @{m['top_expert']} holds {int(m['top_share'] * 100)}% (bus factor {m['bus_factor']}, {m['prs']} PRs)" for m in mods[:8]]
    if r.data.get("bridge_people"):
        lines += ["", "Bridge people (betweenness): " + ", ".join(f"@{b['handle']}" for b in r.data["bridge_people"])]
    return "\n".join(lines)


def _render_profile(r: ToolResult, ledger: Ledger) -> str:
    d = r.data
    p = d["person"]
    lines = [f"**@{p['handle']}**" + (f" ({p['name']})" if p.get("name") else "")]
    if d["expertise"]:
        lines.append("Knows: " + ", ".join(f"{e['system']} ({int(e['share_of_knowledge'] * 100)}%)" for e in d["expertise"][:6]))
    if d["would_leave_gap_in"]:
        lines.append("Would leave a gap in: " + ", ".join(d["would_leave_gap_in"]))
    if d["open_work"]:
        lines += ["", "Open work:"]
        lines += [f"- {w['title']}{_cite(ledger, w['uid'])}" for w in d["open_work"][:5]]
    return "\n".join(lines)


def _render_timeline(r: ToolResult, ledger: Ledger) -> str:
    events = r.data.get("events") or []
    if not events:
        return f"Nothing recorded on {r.data.get('system')} in that window."
    lines = [f"Recent activity on **{r.data['system']}**:"]
    lines += [f"- {e['date']}: {e['title']} ({e['kind']}, {e['state']}, @{e['author']}){_cite(ledger, e['uid'])}" for e in events[:10]]
    return "\n".join(lines)


def _render_drift(r: ToolResult, ledger: Ledger) -> str:
    rows = r.data.get("drifted") or []
    if not rows:
        return "No decision premises have been contradicted by later evidence."
    lines = ["Decisions whose reasons may no longer hold:"]
    for x in rows[:8]:
        lines.append(f"- **{x['title']}** ({x['decided']}) assumed “{x['premise']}”; on {x['on']}: “{x['quote']}”{_cite(ledger, x['challenged_by'])}")
    return "\n".join(lines)


def _render_teams(r: ToolResult, ledger: Ledger) -> str:
    teams = r.data.get("teams") or []
    lines = ["Inferred teams (who reviews and replies to whom):"]
    for i, t in enumerate(teams[:5], 1):
        lines.append(f"{i}. {', '.join('@' + p for p in t['people'][:8])}: focus on {', '.join(t['focus_systems'][:3])}")
    return "\n".join(lines)


def _render_connect(r: ToolResult, ledger: Ledger) -> str:
    path = r.data.get("path")
    if not path:
        return "I found no connection within 8 hops."
    hops = []
    for i, step in enumerate(path):
        hops.append(f"{step['type']} {step['title'] or step['id']}")
        if i < len(r.data["relationships"]):
            hops.append(f"—{r.data['relationships'][i]}→")
    cites = "".join(_cite(ledger, s["id"]) for s in path if s["type"] == "Source")
    return " ".join(hops) + cites


def _render_generic(r: ToolResult, ledger: Ledger) -> str:
    srcs = r.data.get("sources") or []
    decisions = r.data.get("decisions") or []
    lines = []
    if decisions:
        lines.append("Related decisions: " + "; ".join(d["title"] for d in decisions[:5]))
    lines += [f"- {s['title']} ({s['kind']}, {s.get('date')}){_cite(ledger, s['uid'])}" for s in srcs[:6]]
    return "\n".join(lines) or r.summary


def _render_proposal(r: ToolResult, ledger: Ledger) -> str:
    a = r.data["action"]
    return f"Proposed: **{a['title']}** on {a['repo']}. It is waiting for your approval in the Actions panel and has not run yet."


RENDERERS: dict[str, Callable[[ToolResult, Ledger], str]] = {
    "propose_wiki_update": _render_proposal,
    "propose_issue_routing": _render_proposal,
    "why": _render_why,
    "who_knows": _render_who,
    "impact": _render_impact,
    "bus_factor": _render_bus,
    "person_profile": _render_profile,
    "timeline": _render_timeline,
    "drift_report": _render_drift,
    "teams": _render_teams,
    "connect": _render_connect,
    "search": _render_generic,
}
