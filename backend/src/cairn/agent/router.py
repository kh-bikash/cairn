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

from cairn.agent.tools import ToolResult, Tools, _named_terms, _stem
from cairn.multihop import render_departed, render_stale, render_trace

if TYPE_CHECKING:
    from cairn.agent.loop import Ledger

_INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("act_wiki", re.compile(r"\b(update|refresh|publish|sync|regenerate)\b.*\b(wiki|knowledge ?base|docs?)\b", re.I)),
    ("act_route", re.compile(r"\b(route|triage|assign)\b.*#\s*\d+", re.I)),
    ("incident", re.compile(r"\b(incident|outage|is down|went down|broken|failing|errors?|sev ?[12]|paged|alert(?:ing)?|on-?call)\b", re.I)),
    (
        "stale_runbook",
        re.compile(r"\brunbooks?\b.*\b(out of date|outdated|stale|obsolete)\b|\b(out of date|outdated|stale)\b.*\brunbooks?\b", re.I),
    ),
    (
        "departed",
        re.compile(
            r"\bdecisions?\b.*\b(left|departed|no longer (?:here|with us|at the company))\b"
            r"|\b(left|departed)\b.*\bdecisions?\b|\b(people|those|someone|engineers?) who (?:have )?(?:since )?left\b",
            re.I,
        ),
    ),
    (
        "trace",
        re.compile(
            # Past tense only: "which services break if we migrate X" is a change question.
            r"\b(which|what) (?:of our )?(?:projects?|repos?|repositories|services)\b.*\b(adopted|migrated|implemented)\b"
            r"|\bwho drove\b|\bearlier (?:fix|change|pr|issue)\b",
            re.I,
        ),
    ),
    (
        "change",
        re.compile(r"\b(before (?:i|we) change|planning to change|want to change|about to change|rename|deprecate|remove|upgrade|migrat\w*|change to)\b", re.I),
    ),
    ("handoff", re.compile(r"\b(handoff|hand over|handover|leaving|resign\w*|going on leave|parental leave|notice period)\b", re.I)),
    ("drift", re.compile(r"\b(drift|stale|outdated|still (true|valid|hold)|no longer|out of date)\b", re.I)),
    ("risk", re.compile(r"\b(bus factor|leaves?|left|quits?|single point|knowledge risk|concentrat\w*|handoff|hand off)\b", re.I)),
    ("teams", re.compile(r"\b(teams?|who works with|groups?)\b", re.I)),
    ("connect", re.compile(r"\b(connected|related|relationship|path)\b.*\b(between|to|and)\b", re.I)),
    # Impact before "who": "who should be warned if X breaks" is a blast-radius question.
    ("impact", re.compile(r"\b(break|breaks|breaking|impact|depends?|dependen\w*|blast radius|goes down|downstream|affects?|warned)\b", re.I)),
    ("who", re.compile(r"\bwho\b.*\b(knows?|expert|owns?|review|ask|maintains?|best|should|worked|works|go-to|responsible|person)\b|\bexperts?\b", re.I)),
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


_CATALOG: dict[str, _Catalog] = {}  # by graph name: one catalog per workspace
_CATALOG_TTL = 300.0


def _catalog(tools: Tools) -> _Catalog:
    cached = _CATALOG.get(tools.store.name)
    if cached and time.monotonic() - cached.loaded_at < _CATALOG_TTL:
        return cached
    systems: list[tuple[str, str]] = []
    activity: dict[str, int] = {}
    rows = tools.store.records(
        "MATCH (s:System) WHERE s.kind IN ['repo', 'code'] OPTIONAL MATCH (x:Source)-[:TOUCHES|IN_REPO]->(s) "
        # coalesce: FalkorDB crashes when an aggregate query returns a property name the graph has never stored.
        "RETURN s.key AS key, s.name AS name, s.path AS path, coalesce(s.aliases, []) AS aliases, count(x) AS act"
    )
    for r in rows:
        activity[r["key"]] = r["act"]
    for r in rows:
        texts = {r["key"], r["name"], r["path"], *(r["aliases"] or [])}
        # "ingestion" should find "graphrag_sdk/ingestion": index a module's last segment too.
        if r["path"] and "/" in r["path"]:
            texts.add(r["path"].rsplit("/", 1)[1])
        # People say "payments service" for payments-service and "graph rag" for graph_rag.
        texts |= {re.sub(r"[-_]+", " ", t) for t in texts if t and "/" not in t and ":" not in t}
        for text in texts:
            if text and len(text) >= 3:
                systems.append((text.lower(), r["key"]))
    # Longest (most specific) text first; among equal texts, the most active system first.
    systems.sort(key=lambda t: (-len(t[0]), -activity.get(t[1], 0)))
    handles = {r["h"].lower(): r["h"] for r in tools.store.records("MATCH (p:Person) RETURN p.handle AS h")}
    cat = _Catalog(systems, handles, time.monotonic())
    _CATALOG[tools.store.name] = cat
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


_LEADING_WHY = re.compile(r"^\s*(why|how come|what was the (reason|rationale))\b", re.I)


def intent_of(question: str) -> str:
    # "Why did we bound our dependencies?" is about rationale even though it mentions dependencies.
    if _LEADING_WHY.search(question):
        return "why"
    for name, pattern in _INTENTS:
        if pattern.search(question):
            return name
    return "search"


@dataclass
class Plan:
    intent: str
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    note: str | None = None  # set when the router declines instead of answering

    def render(self, results: list[tuple[str, ToolResult]], ledger: Ledger) -> str:
        parts = [RENDERERS.get(name, _render_generic)(r, ledger) for name, r in results]
        body = "\n\n".join(p for p in parts if p).strip()
        if self.note and body:
            return f"{self.note}\n\n{body}"
        return body or self.note or "The graph returned no results for that question."


# Words that frame a question rather than name its topic.
_GENERIC = frozenset(
    [
        "who",
        "whom",
        "whose",
        "what",
        "which",
        "when",
        "where",
        "why",
        "how",
        "does",
        "did",
        "do",
        "should",
        "would",
        "could",
        "will",
        "can",
        "is",
        "are",
        "was",
        "were",
        "the",
        "a",
        "an",
        "of",
        "in",
        "on",
        "for",
        "to",
        "with",
        "and",
        "or",
        "our",
        "we",
        "us",
        "they",
        "it",
        "this",
        "that",
        "these",
        "those",
        "best",
        "most",
        "go-to",
        "person",
        "people",
        "knows",
        "know",
        "owns",
        "own",
        "owner",
        "expert",
        "experts",
        "review",
        "reviews",
        "reviewer",
        "works",
        "work",
        "worked",
        "responsible",
        "ask",
        "talk",
        "about",
        "speak",
        "contact",
        "team",
        "code",
        "module",
        "repo",
        "repository",
        "project",
        "change",
        "changes",
        "breaking",
        "break",
        "breaks",
        "warned",
        "warn",
        "depends",
        "depend",
        "downstream",
        "happens",
        "happen",
        "if",
        "leaves",
        "left",
        "lose",
        "goes",
        "go",
        "down",
        "fails",
        "fail",
        "outage",
        "crash",
        "crashes",
        "stops",
        "stop",
        "slow",
        "recently",
        "lately",
        "since",
        "until",
        "changed",
        "happened",
        "anything",
        "something",
    ]
)


def unmatched_topic(tools: Tools, question: str, systems: list[str], people: list[str]) -> list[str]:
    """Topic words not explained by a matched system/person that never appear together anywhere in the graph.

    "Who owns the Kubernetes operator?" mentions words that exist separately but never co-occur, so answering
    with whatever system matched would be confidently wrong. Returns those words, or [] if the topic exists.
    """
    covered = {w for key in systems for w in re.split(r"[/:#\s_.-]+", key.lower()) if w}
    covered |= {p.lower() for p in people}
    words = [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", question.lower()) if w not in _GENERIC and w not in covered]
    words = [w for w in words if not any(w in c or c in w for c in covered if len(c) >= 4)]
    if not words:
        return []
    query = " ".join(sorted(set(words), key=len, reverse=True)[:3])  # space = AND in the full-text engine
    for label in ("Source", "Decision", "System"):
        hits = tools.store.ro_query(f"CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node RETURN count(node)", {"q": query})
        if hits and hits[0][0]:
            return []
    return words


def _uncovered_words(question: str, systems: list[str], people: list[str]) -> list[str]:
    """Topic words a who-question asks about that the matched system and people don't explain.

    Only triggers on a named thing ("Kubernetes"): ordinary words ("client", "tests") describe the system itself.
    """
    covered = {w for key in systems for w in re.split(r"[/:#\s_.-]+", key.lower()) if w} | {p.lower() for p in people}
    words = [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", question.lower()) if w not in _GENERIC and w not in covered]
    words = [w for w in words if not any(w in c or c in w for c in covered if len(c) >= 4)]
    named = _named_terms(question)
    return words if any(_stem(w) in named for w in words) else []


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
    if intent == "incident":
        return Plan(intent, [("incident_memory", {"description": question})])
    if intent == "departed":
        return Plan(intent, [("departed_decisions", {})])
    if intent == "stale_runbook":
        return Plan(intent, [("stale_runbooks", {})])
    if intent == "trace":
        return Plan(intent, [("trace_topic", {"topic": question})])
    if intent == "change" and systems:
        return Plan(intent, [("change_impact", {"system": systems[0], "change": question})])
    if intent == "handoff" and people:
        return Plan(intent, [("handoff_plan", {"handle": people[0]})])
    if intent in {"who", "impact", "timeline", "search"} and (missing := unmatched_topic(tools, question, systems, people)):
        plan.note = f"I couldn't find anything about “{' '.join(missing)}” in the graph, so I can't answer that reliably."
        return plan
    if intent == "who" and systems and (extra := _uncovered_words(question, systems, people)):
        # "Who owns the FalkorDB Kubernetes operator?": the repo matched, the thing asked about isn't a tracked system.
        # Naming the repo's experts would answer a different question; show where it's mentioned instead.
        plan.note = f"I couldn't find a system called “{' '.join(extra)}”; these threads mention it:"
        plan.calls.append(("search", {"query": " ".join(extra)}))
        return plan
    if intent == "drift":
        plan.calls.append(("drift_report", {}))
    elif intent == "risk":
        if people:
            plan.calls.append(("handoff_plan", {"handle": people[0]}))
        elif systems:
            # "Is knowledge of the retrieval code concentrated?": each person's share of that system answers it.
            plan.calls.append(("who_knows", {"system": systems[0]}))
        else:
            plan.calls.append(("bus_factor", {}))
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
        if r.data.get("no_record_of"):
            return str(r.data["note"])
        srcs = r.data.get("sources") or []
        if not srcs:
            return "I found no recorded decision or discussion on that topic."
        lines = ["I found no recorded decision explaining this, and nothing confirms it happened. The closest related threads:"]
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
    if r.data.get("owning_team"):
        lines[0] += f" (owned by {', '.join(r.data['owning_team'])})"
    for e in experts:
        who = f"@{e['handle']}" + (f" ({', '.join(x for x in (e.get('role'), e.get('team')) if x)})" if e.get("role") or e.get("team") else "")
        did = ", ".join(f"{k} {n}" for k, n in (e.get("activity") or {}).items())
        work = "".join(_cite(ledger, w["uid"]) for w in e.get("recent_work", [])[:2])
        lines.append(f"- {who}: {int(e['share'] * 100)}% of recent activity" + (f"; {did}" if did else "") + f", last active {e['last_active']}{work}")
    for d in r.data.get("departed_experts", []):
        lines.append(f"- ⚠ @{d['handle']} held {int(d['share'] * 100)}% of this knowledge but left on {d['left_company']}.")
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
    departed = r.data.get("departed_experts") or []
    if not mods and not departed:
        return "No system has knowledge concentrated in a single person."
    lines = []
    if mods:
        lines.append("Systems where knowledge is concentrated:")
        for m in mods[:8]:
            line = f"- {m['system']}: @{m['top_expert']} holds {int(m['top_share'] * 100)}% (bus factor {m['bus_factor']})"
            if m.get("top_expert_left"):
                line += f" ⚠ left on {m['top_expert_left']}"
            if m.get("next_best"):
                line += f"; next best @{m['next_best']}"
            lines.append(line)
    for d in departed[:5]:
        held = ", ".join(f"{s['system']} ({int(s['all_time_share'] * 100)}%)" for s in d["systems"][:4])
        lines.append(f"- ⚠ @{d['handle']} left on {d['left_company']} and held: {held}")
    if r.data.get("no_clear_owner"):
        lines += ["", "No clear owner: " + ", ".join(d["system"] for d in r.data["no_clear_owner"][:6])]
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


def _render_draft(r: ToolResult, ledger: Ledger) -> str:
    return str(r.data.get("draft") or r.summary)


def _render_incident(r: ToolResult, ledger: Ledger) -> str:
    d = r.data
    if not d["similar_incidents"]:
        return "I found no similar past incident in the graph."
    lines = ["Similar past incidents:"]
    for i in d["similar_incidents"]:
        who = ", ".join(f"@{h['handle']}" for h in i["handled_by"] if not h["left_company"])
        fix = f" Fix: {i['what_fixed_it']}" if i["what_fixed_it"] else ""
        lines.append(f"- {i['date']}: {i['title']}{_cite(ledger, i['uid'])}.{fix}" + (f" Handled by {who}." if who else ""))
    for rb in d["runbooks"]:
        flag = f" ⚠ possibly outdated: {'; '.join(rb['why'])}" if rb["possibly_outdated"] else ""
        lines.append(f"\nRunbook: {rb['title']}{_cite(ledger, rb['uid'])}{flag}")
    if d["who_to_page"]:
        lines.append("\nWho to page now: " + ", ".join(f"@{p['handle']} ({p['system']})" for p in d["who_to_page"]))
    return "\n".join(lines)


RENDERERS: dict[str, Callable[[ToolResult, Ledger], str]] = {
    "change_impact": _render_draft,
    "handoff_plan": _render_draft,
    "incident_memory": _render_incident,
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
    "departed_decisions": render_departed,
    "stale_runbooks": render_stale,
    "trace_topic": render_trace,
}
