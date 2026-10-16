"""The Cairn agent: an LLM tool loop over the graph, streamed as events.

Event types (all JSON-serialisable):
    start        {question}
    tool_call    {id, name, args}
    tool_result  {id, name, summary, subgraph, evidence, error?}
    answer       {text, citations, answer_uid, mode}
    error        {message}
    done         {usage}

Grounding contract: every source a tool returns is entered into a ledger with a number. The model
must cite claims as [n]; citations that don't match a ledger entry are removed before the answer
is shown. The answer and the nodes it used are written back to the graph as an audit trail.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

import openai

from cairn.actions.service import ActionError, ActionService
from cairn.agent.router import route
from cairn.agent.tools import TOOL_SPECS, Evidence, ToolError, ToolResult, Tools, dispatch, fmt_date
from cairn.config import Settings
from cairn.graph.client import GraphStore, SubGraph
from cairn.llm.client import LLM, LLMNotConfigured

log = logging.getLogger(__name__)

_DASHES = "-‐‑‒–—"  # models write ranges with any of these: [1-3], [1‑3], [1–3]
_ITEM = rf"\d+(?:\.\d+)?(?:\s*[{_DASHES}]\s*\d+)?"
_CITE = re.compile(rf"\[\s*({_ITEM}(?:\s*,\s*{_ITEM})*)\s*\]")
# gpt-oss cites with lenticular brackets (【1】); some models use full-width ones (［1］).
_BRACKETS = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]"})
_RANGE = re.compile(rf"^(\d+)\s*[{_DASHES}]\s*(\d+)$")
_MAX_TOOL_CHARS = 9_000

SYSTEM_PROMPT = """You are Cairn, a company brain for an engineering organisation. You answer questions about
people, systems, decisions and their history by calling graph tools. The graph covers these repositories: {repos}.

How to work:
- Always ground answers in tool results. Call tools before answering; prefer `why` for "why/how come" questions,
  `who_knows` for expertise, `impact` for "what breaks / depends on", `bus_factor` and `person_profile` for risk,
  `timeline` for "what changed / as of", `connect` for "how is X related to Y", `drift_report` for stale decisions.
- If a tool says no system/person matches, call `list_systems` or `search` and retry with a valid name.
- When the user names an area inside a repo (e.g. "GraphRAG-SDK ingestion", "QueryWeaver API routes"), pass that module
  to the tool (e.g. "graphrag_sdk/ingestion", "api/routes"), not just the repo.
- Items in tool results carry a citation number `ref`. Cite every factual claim with it in square brackets, e.g.
  "… was chosen for speed [3]." Use only integer refs you were given. Never invent PR numbers, people, dates or quotes.
- For "why" questions, give each premise (reason) behind the decision, quote its key phrase briefly, and cite its ref.
  Mention rejected alternatives when present.
- To open a specific PR/issue/decision by its uid (e.g. 'FalkorDB/falkordb-py#271'), call `details`, not `search`.
- If a decision has `challenged_by` entries or `drift_detected`, say so explicitly: the reason may no longer hold.
- If the graph does not contain the answer, say what you checked and that you don't know.
- Be concise: lead with the answer in 1-2 sentences, then supporting bullets. Use @handles for people and dates as YYYY-MM-DD.
- You can PROPOSE actions (`propose_wiki_update`, `propose_issue_routing`) when the user asks you to do something, e.g. update
  the wiki after drift or route an issue. Proposals wait for a human to approve them in the Actions panel; never claim they ran.
"""

_S = {"type": "string"}
ACTION_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "propose_wiki_update",
            "description": "Propose a PR that refreshes the OKF knowledge-base wiki from the graph (needs human approval).",
            "parameters": {"type": "object", "properties": {"reason": _S}, "required": ["reason"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_issue_routing",
            "description": "Propose a comment suggesting the right experts for an issue in the sandbox repo (needs human approval).",
            "parameters": {
                "type": "object",
                "properties": {"repo": {**_S, "description": "owner/name"}, "number": {"type": "integer"}},
                "required": ["repo", "number"],
            },
        },
    },
]
ACTION_NAMES = {spec["function"]["name"] for spec in ACTION_SPECS}


@dataclass
class Ledger:
    """Numbers sources in first-seen order so citations stay stable across tool calls."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, ev: Evidence) -> int:
        if ev.uid not in self.entries:
            self.entries[ev.uid] = {"ref": len(self.entries) + 1, **asdict(ev), "date": fmt_date(ev.ts)}
        elif ev.quote and not self.entries[ev.uid].get("quote"):
            self.entries[ev.uid]["quote"] = ev.quote
        return int(self.entries[ev.uid]["ref"])

    def by_ref(self) -> dict[int, dict[str, Any]]:
        return {e["ref"]: e for e in self.entries.values()}


def clean_citations(text: str, ledger: Ledger) -> tuple[str, list[dict[str, Any]]]:
    """Drop citations to unknown refs; return the cleaned text and the cited ledger entries in order."""
    valid = ledger.by_ref()
    cited: dict[int, dict[str, Any]] = {}

    def repl(m: re.Match[str]) -> str:
        kept = []
        refs: list[str] = []
        for part in m.group(1).split(","):
            part = part.strip()
            span = _RANGE.match(part)
            if span and int(span.group(1)) <= int(span.group(2)) <= int(span.group(1)) + 10:
                refs += [str(n) for n in range(int(span.group(1)), int(span.group(2)) + 1)]  # "[1-3]" -> [1][2][3]
            else:
                refs.append(part)
        for part in refs:
            if part.isdigit() and int(part) in valid:  # "[10.67]" or unknown numbers are dropped, never shown
                cited.setdefault(int(part), valid[int(part)])
                kept.append(f"[{int(part)}]")
        return "".join(kept)

    cleaned = _CITE.sub(repl, text.translate(_BRACKETS))
    cleaned = re.sub(r"[ \t]+([.,;:])", r"\1", cleaned)
    citations = [
        {"ref": r, "uid": e["uid"], "title": e["title"], "url": e["url"], "kind": e["kind"], "date": e["date"], "quote": e.get("quote")}
        for r, e in sorted(cited.items())
    ]
    return cleaned.strip(), citations


def _shrink(value: Any, max_items: int) -> Any:
    if isinstance(value, list):
        return [_shrink(v, max_items) for v in value[:max_items]]
    if isinstance(value, dict):
        return {k: _shrink(v, max_items) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 600:
        return value[:600] + "…"
    return value


_UID_KEYS = frozenset({"uid", "source", "decided_in", "challenged_by"})


def annotate_refs(value: Any, ledger: Ledger) -> Any:
    """Add `ref` beside any uid that has a citation number, e.g. {"source": "x#1", "ref": 4}."""
    if isinstance(value, list):
        return [annotate_refs(v, ledger) for v in value]
    if isinstance(value, dict):
        out = {k: annotate_refs(v, ledger) for k, v in value.items()}
        for key in _UID_KEYS & value.keys():
            uid = value[key]
            if isinstance(uid, str) and uid in ledger.entries and "ref" not in out:
                out["ref"] = ledger.entries[uid]["ref"]
        return out
    return value


def tool_payload(result: ToolResult, ledger: Ledger) -> str:
    """What the model sees: the tool's data plus numbered citable sources, size-capped without breaking JSON."""
    sources = [{"ref": ledger.add(ev), "uid": ev.uid, "title": ev.title, **({"quote": ev.quote} if ev.quote else {})} for ev in result.evidence]
    body: dict[str, Any] = {"result": annotate_refs(result.data, ledger), "citable_sources": sources}
    for max_items in (50, 15, 8, 4):
        payload = json.dumps(_shrink(body, max_items), default=str, ensure_ascii=False)
        if len(payload) <= _MAX_TOOL_CHARS:
            return payload
    return payload


class Agent:
    def __init__(self, store: GraphStore, llm: LLM, settings: Settings) -> None:
        self.store = store
        self.llm = llm
        self.settings = settings
        self.tools = Tools(store)
        self.actions = ActionService(store, settings)
        self.persist_answers = True  # evaluation runs turn this off so test questions don't fill the audit trail

    async def run(self, question: str, history: list[dict[str, str]] | None = None, *, use_llm: bool = True) -> AsyncIterator[dict[str, Any]]:
        question = question.strip()
        yield {"type": "start", "question": question}
        ledger = Ledger()
        used = SubGraph()
        started = time.perf_counter()

        if use_llm and self.llm.available("agent"):
            try:
                async for event in self._llm_loop(question, history or [], ledger, used):
                    yield event
                yield {"type": "done", "usage": self.llm.usage.snapshot(), "seconds": round(time.perf_counter() - started, 2)}
                return
            except (LLMNotConfigured, openai.APIError) as exc:
                # Quota exhausted or provider down mid-demo: degrade to deterministic routing instead of failing.
                log.warning("agent LLM unavailable (%s); falling back to graph router", exc)
                yield {"type": "notice", "message": "The AI model is busy right now, so this answer comes straight from the graph."}

        async for event in self._router_answer(question, ledger, used):
            yield event
        yield {"type": "done", "usage": self.llm.usage.snapshot(), "seconds": round(time.perf_counter() - started, 2)}

    # ------------------------------------------------------------------ LLM mode
    async def _llm_loop(self, question: str, history: list[dict[str, str]], ledger: Ledger, used: SubGraph) -> AsyncIterator[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(repos=", ".join(self.settings.repos))},
            *history[-6:],
        ]
        # Retrieval first: run the tool the deterministic router would pick, so the model starts from the right
        # evidence instead of hunting for it (LLMs are inconsistent about which tool to open first).
        prefetched = []
        plan = await asyncio.to_thread(route, self.tools, question, self.settings.action_repos)
        for i, (name, args) in enumerate(plan.calls[:1]):
            if name in ACTION_NAMES or name == "search":
                continue
            call_id = f"prefetch_{i}"
            yield {"type": "tool_call", "id": call_id, "name": name, "args": args}
            event, content, fetched = await self._run_tool(call_id, name, args, ledger, used)
            yield event
            if fetched is not None:
                prefetched.append(f"Result of `{name}({json.dumps(args, ensure_ascii=False)})`:\n{content}")
        user = question
        if prefetched:
            user += "\n\nGraph context already retrieved for you (cite its refs; call more tools only if it's not enough):\n\n" + "\n\n".join(prefetched)
        messages.append({"role": "user", "content": user})
        for step in range(self.settings.agent_max_steps):
            final_step = step == self.settings.agent_max_steps - 1
            result = await self.llm.chat("agent", messages, tools=None if final_step else TOOL_SPECS + ACTION_SPECS, use_cache=False)
            if not result.tool_calls:
                if not (result.content or "").strip():
                    break  # an empty answer is no answer: fall through to the graph router below
                yield await self._finish(question, result.content, ledger, used, mode="agent")
                return
            messages.append(result.message())
            for tc in result.tool_calls:
                call_id = tc["id"] or f"call_{uuid.uuid4().hex[:8]}"
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                yield {"type": "tool_call", "id": call_id, "name": tc["name"], "args": args}
                event, content, _ = await self._run_tool(call_id, tc["name"], args, ledger, used)
                yield event
                messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        # The model kept searching instead of answering (common on questions with no recorded answer). Rather than
        # admit defeat, answer from the graph router over the evidence already gathered: it declines honestly.
        log.info("agent used all %d steps without answering; using the graph router", self.settings.agent_max_steps)
        async for event in self._router_answer(question, ledger, used):
            yield event

    async def _run_tool(self, call_id: str, name: str, args: dict[str, Any], ledger: Ledger, used: SubGraph) -> tuple[dict[str, Any], str, ToolResult | None]:
        try:
            if name in ACTION_NAMES:
                result = await asyncio.to_thread(self._propose, name, args)
            else:
                result = await asyncio.to_thread(dispatch, self.tools, name, args)
        except (ToolError, ActionError) as exc:
            event = {
                "type": "tool_result",
                "id": call_id,
                "name": name,
                "summary": str(exc),
                "error": True,
                "subgraph": {"nodes": [], "edges": []},
                "evidence": [],
            }
            return event, json.dumps({"error": str(exc)}), None
        content = tool_payload(result, ledger)
        used.merge(result.subgraph)
        event = {
            "type": "tool_result",
            "id": call_id,
            "name": name,
            "summary": result.summary,
            "subgraph": result.subgraph.to_dict(),
            "evidence": [ledger.entries[ev.uid] for ev in result.evidence],
        }
        if name in ACTION_NAMES:
            event["action"] = result.data["action"]
        return event, content, result

    def _propose(self, name: str, args: dict[str, Any]) -> ToolResult:
        try:
            if name == "propose_wiki_update":
                action = self.actions.propose_wiki_update(str(args.get("reason") or "Requested by the agent"))
            else:
                action = self.actions.propose_issue_routing(str(args["repo"]), int(args["number"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise ToolError(f"bad arguments for {name}: {exc}") from exc
        data = {"action": action.public(), "note": "Pending human approval in the Actions panel. It has NOT been executed."}
        return ToolResult(data, f"proposed {action.kind} ({action.uid}), awaiting approval")

    # ------------------------------------------------------------------ router mode
    async def _router_answer(self, question: str, ledger: Ledger, used: SubGraph) -> AsyncIterator[dict[str, Any]]:
        plan = await asyncio.to_thread(route, self.tools, question, self.settings.action_repos)
        results: list[tuple[str, ToolResult]] = []
        for i, (name, args) in enumerate(plan.calls):
            call_id = f"route_{i}"
            yield {"type": "tool_call", "id": call_id, "name": name, "args": args}
            event, _, result = await self._run_tool(call_id, name, args, ledger, used)
            yield event
            if result is not None:
                results.append((name, result))
        text = plan.render(results, ledger)
        yield await self._finish(question, text, ledger, used, mode="router")

    # ------------------------------------------------------------------ shared
    async def _finish(self, question: str, text: str, ledger: Ledger, used: SubGraph, *, mode: str) -> dict[str, Any]:
        cleaned, citations = clean_citations(text, ledger)
        answer_uid = f"answer-{uuid.uuid4().hex[:12]}"
        try:
            if self.persist_answers:
                await asyncio.to_thread(self._persist, answer_uid, question, cleaned, used)
        except Exception as exc:  # noqa: BLE001 - the audit trail must never block an answer
            log.warning("could not persist answer: %s", exc)
        return {"type": "answer", "text": cleaned, "citations": citations, "answer_uid": answer_uid, "mode": mode}

    def _persist(self, uid: str, question: str, text: str, used: SubGraph) -> None:
        refs = [n["id"].split(":", 1) for n in list(used.nodes.values())[:60]]
        self.store.query(
            "CREATE (a:Answer {uid: $uid, question: $q, text: $t, ts: timestamp()})",
            {"uid": uid, "q": question[:1000], "t": text[:8000]},
        )
        for label in ("Person", "System", "Source", "Decision", "Premise"):
            keys = [k for lbl, k in refs if lbl == label]
            if keys:
                prop = {"Person": "handle", "System": "key"}.get(label, "uid")
                self.store.query(
                    f"MATCH (a:Answer {{uid: $uid}}) UNWIND $keys AS k MATCH (n:{label} {{{prop}: k}}) MERGE (a)-[:USED]->(n)",
                    {"uid": uid, "keys": keys},
                )
