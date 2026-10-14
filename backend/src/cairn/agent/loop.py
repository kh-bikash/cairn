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

from cairn.agent.router import route
from cairn.agent.tools import TOOL_SPECS, Evidence, ToolError, ToolResult, Tools, dispatch, fmt_date
from cairn.config import Settings
from cairn.graph.client import GraphStore, SubGraph
from cairn.llm.client import LLM, LLMNotConfigured

log = logging.getLogger(__name__)

_CITE = re.compile(r"\[(\d{1,3})\]")
_MAX_TOOL_CHARS = 9_000

SYSTEM_PROMPT = """You are Cairn, a company brain for an engineering organisation. You answer questions about
people, systems, decisions and their history by calling graph tools. The graph covers these repositories: {repos}.

How to work:
- Always ground answers in tool results. Call tools before answering; prefer `why` for "why/how come" questions,
  `who_knows` for expertise, `impact` for "what breaks / depends on", `bus_factor` and `person_profile` for risk,
  `timeline` for "what changed / as of", `connect` for "how is X related to Y", `drift_report` for stale decisions.
- If a tool says no system/person matches, call `list_systems` or `search` and retry with a valid name.
- Each tool result lists `citable_sources` with a number `ref`. Cite every factual claim with [ref], e.g. "… was chosen
  for speed [3]." Only use refs you were given. Never invent PR numbers, people, dates or quotes.
- If a decision has `challenged_by` entries or `drift_detected`, say so explicitly: the reason may no longer hold.
- If the graph does not contain the answer, say what you checked and that you don't know.
- Be concise: lead with the answer in 1-2 sentences, then supporting bullets. Use @handles for people and dates as YYYY-MM-DD.
"""


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
        ref = int(m.group(1))
        if ref in valid:
            cited.setdefault(ref, valid[ref])
            return m.group(0)
        return ""

    cleaned = _CITE.sub(repl, text)
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


def tool_payload(result: ToolResult, ledger: Ledger) -> str:
    """What the model sees: the tool's data plus numbered citable sources, size-capped without breaking JSON."""
    sources = [{"ref": ledger.add(ev), "uid": ev.uid, "title": ev.title, **({"quote": ev.quote} if ev.quote else {})} for ev in result.evidence]
    body: dict[str, Any] = {"result": result.data, "citable_sources": sources}
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

    async def run(self, question: str, history: list[dict[str, str]] | None = None) -> AsyncIterator[dict[str, Any]]:
        question = question.strip()
        yield {"type": "start", "question": question}
        ledger = Ledger()
        used = SubGraph()
        started = time.perf_counter()

        if self.llm.available("agent"):
            try:
                async for event in self._llm_loop(question, history or [], ledger, used):
                    yield event
                yield {"type": "done", "usage": self.llm.usage.snapshot(), "seconds": round(time.perf_counter() - started, 2)}
                return
            except (LLMNotConfigured, openai.APIError) as exc:
                # Quota exhausted or provider down mid-demo: degrade to deterministic routing instead of failing.
                log.warning("agent LLM unavailable (%s); falling back to graph router", exc)
                yield {"type": "notice", "message": f"LLM unavailable ({type(exc).__name__}); answering directly from the graph."}

        async for event in self._router_answer(question, ledger, used):
            yield event
        yield {"type": "done", "usage": self.llm.usage.snapshot(), "seconds": round(time.perf_counter() - started, 2)}

    # ------------------------------------------------------------------ LLM mode
    async def _llm_loop(self, question: str, history: list[dict[str, str]], ledger: Ledger, used: SubGraph) -> AsyncIterator[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(repos=", ".join(self.settings.repos))},
            *history[-6:],
            {"role": "user", "content": question},
        ]
        for step in range(self.settings.agent_max_steps):
            final_step = step == self.settings.agent_max_steps - 1
            result = await self.llm.chat("agent", messages, tools=None if final_step else TOOL_SPECS, use_cache=False)
            if not result.tool_calls:
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
        yield await self._finish(question, "I ran out of reasoning steps before reaching an answer.", ledger, used, mode="agent")

    async def _run_tool(self, call_id: str, name: str, args: dict[str, Any], ledger: Ledger, used: SubGraph) -> tuple[dict[str, Any], str, ToolResult | None]:
        try:
            result = await asyncio.to_thread(dispatch, self.tools, name, args)
        except ToolError as exc:
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
        return event, content, result

    # ------------------------------------------------------------------ router mode
    async def _router_answer(self, question: str, ledger: Ledger, used: SubGraph) -> AsyncIterator[dict[str, Any]]:
        plan = await asyncio.to_thread(route, self.tools, question)
        results: list[tuple[str, ToolResult]] = []
        for i, (name, args) in enumerate(plan.calls):
            call_id = f"route_{i}"
            yield {"type": "tool_call", "id": call_id, "name": name, "args": args}
            event, _, result = await self._run_tool(call_id, name, args, ledger, used)
            yield event
            if result is not None:
                results.append((name, result))
        text = plan.render(results, ledger) if results else "I couldn't find anything in the graph for that question."
        yield await self._finish(question, text, ledger, used, mode="router")

    # ------------------------------------------------------------------ shared
    async def _finish(self, question: str, text: str, ledger: Ledger, used: SubGraph, *, mode: str) -> dict[str, Any]:
        cleaned, citations = clean_citations(text, ledger)
        answer_uid = f"answer-{uuid.uuid4().hex[:12]}"
        try:
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
