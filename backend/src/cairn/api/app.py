"""HTTP API: streaming agent answers (SSE), browsable decisions/people/systems, approvals and repository sync."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cairn.actions.service import ActionError, ActionService
from cairn.agent.loop import Agent
from cairn.agent.tools import TOOL_SPECS, ToolError, Tools, dispatch
from cairn.api import catalog
from cairn.api.security import RateLimiter, client_key, require_admin
from cairn.config import Settings, get_settings
from cairn.graph.client import GraphStore, SubGraph
from cairn.llm.client import LLM
from cairn.sync import SyncError, SyncManager, snapshot_repos
from cairn.workspaces import describe, is_workspace_graph
from cairn.workspaces import graph_names as workspace_graphs

log = logging.getLogger(__name__)

_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2_000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)


class ToolRequest(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class RouteIssueRequest(BaseModel):
    repo: str
    number: int = Field(ge=1)


class ChangeRequest(BaseModel):
    system: str = Field(min_length=1, max_length=200)
    change: str = Field(default="", max_length=1_000)


class IncidentRequest(BaseModel):
    description: str = Field(min_length=3, max_length=2_000)


class HandoffRequest(BaseModel):
    handle: str = Field(min_length=1, max_length=100)

class GuardRequest(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(default="", max_length=6_000)
    files: list[str] = Field(default_factory=list, max_length=100)

class SyncRequest(BaseModel):
    repos: list[str] = Field(min_length=1, max_length=10)


class WikiRequest(BaseModel):
    reason: str = Field(default="Refresh the knowledge base from the graph", max_length=500)


class Workspace:
    """Everything bound to one workspace graph. Shared pieces (settings, LLM, rate limiter) come from State."""

    def __init__(self, state: State, graph: str) -> None:
        self.settings = state.settings
        self.llm = state.llm
        self.ask_limiter = state.ask_limiter
        self.graph = graph
        self.store = GraphStore(state.settings, graph_name=graph)
        self.agent = Agent(self.store, self.llm, state.settings)
        self.tools = Tools(self.store)
        self.actions = ActionService(self.store, state.settings)
        self.sync = SyncManager(self.store, self.llm, state.settings)


class State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.llm = LLM(settings)
        self.ask_limiter = RateLimiter(settings.ask_rate_limit_per_minute, 60.0)
        self._workspaces: dict[str, Workspace] = {}
        self._known: tuple[float, list[str]] = (0.0, [])
        self.default = self.workspace(settings.graph_name)

    def graphs(self) -> list[str]:
        stamp, cached = self._known
        if time.monotonic() - stamp > 30 or not cached:
            try:
                cached = workspace_graphs(self.settings) or [self.settings.graph_name]
            except Exception:  # noqa: BLE001 - FalkorDB down: fall back to the default graph
                cached = [self.settings.graph_name]
            self._known = (time.monotonic(), cached)
        return cached

    def workspace(self, graph: str) -> Workspace:
        if graph not in self._workspaces:
            self._workspaces[graph] = Workspace(self, graph)
        return self._workspaces[graph]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    state = State(settings)
    if state.default.store.ping():
        await asyncio.to_thread(state.default.store.ensure_schema)
    else:
        log.error("FalkorDB is not reachable at %s:%s", settings.falkordb_host, settings.falkordb_port)
    app.state.cairn = state
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Cairn", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Retry-After"],
    )

    def st(request: Request) -> Workspace:
        """The workspace this request is about: `X-Cairn-Workspace` header or `ws` query param, default graph otherwise."""
        state: State = request.app.state.cairn
        graph = request.headers.get("x-cairn-workspace") or request.query_params.get("ws") or state.settings.graph_name
        if graph != state.settings.graph_name and (not is_workspace_graph(state.settings, graph) or graph not in state.graphs()):
            raise HTTPException(404, f"unknown workspace {graph!r}")
        return state.workspace(graph)

    @app.get("/api/workspaces")
    async def workspaces(request: Request) -> dict[str, Any]:
        state: State = request.app.state.cairn
        graphs = await asyncio.to_thread(state.graphs)
        items = [await asyncio.to_thread(describe, state.workspace(g).store) for g in graphs]
        return {"items": items, "default": state.settings.graph_name}

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        s = st(request)
        ok = await asyncio.to_thread(s.store.ping)
        return {
            "ok": ok,
            "graph": s.store.name,
            "llm": {role: {"model": getattr(s.settings, f"model_{role}"), "ready": s.llm.available(role)} for role in ("decide", "extract", "agent")},
            "repos": snapshot_repos(s.settings) or s.settings.repos,
            "admin_required": s.settings.admin_token is not None,
        }

    @app.get("/api/stats")
    async def stats(request: Request) -> dict[str, Any]:
        s = st(request)
        counts = await asyncio.to_thread(s.store.counts)
        extra = await asyncio.to_thread(
            s.store.records,
            """
            MATCH (s:Source) WITH count(s) AS sources,
                 count(CASE WHEN s.extract_version IS NOT NULL THEN 1 END) AS processed,
                 max(s.ts) AS newest, min(s.ts) AS oldest
            RETURN sources, processed, newest, oldest
            """,
        )
        return {**counts, "coverage": extra[0] if extra else {}, "llm_usage": s.llm.usage.snapshot()}

    @app.post("/api/ask")
    async def ask(body: AskRequest, request: Request) -> StreamingResponse:
        s = st(request)
        s.ask_limiter.check(client_key(request))

        async def events() -> AsyncIterator[bytes]:
            try:
                async for event in s.agent.run(body.question, body.history):
                    if await request.is_disconnected():
                        log.info("client disconnected; stopping agent")
                        return
                    yield f"data: {json.dumps(event, default=str, ensure_ascii=False)}\n\n".encode()
            except Exception as exc:
                log.exception("agent failed")
                yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'})}\n\n".encode()

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/tools/{name}")
    async def run_tool(name: str, body: ToolRequest, request: Request) -> dict[str, Any]:
        if name not in _TOOL_NAMES:
            raise HTTPException(404, f"unknown tool {name}")
        s = st(request)
        try:
            result = await asyncio.to_thread(dispatch, s.tools, name, body.args)
        except ToolError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "summary": result.summary,
            "data": result.data,
            "subgraph": result.subgraph.to_dict(),
            "evidence": [vars(e) for e in result.evidence],
        }

    @app.get("/api/graph/overview")
    async def overview(request: Request, people: int = 25) -> dict[str, Any]:
        """Starting canvas: repos, their main modules, dependencies, and the most active people."""
        s = st(request)

        def build() -> dict[str, Any]:
            sg = SubGraph()
            for r in s.store.records("MATCH (r:System {kind: 'repo'}) RETURN r.key AS key, r.name AS name, r.stars AS stars, r.language AS language"):
                sg.node("System", r["key"], name=r["name"], kind="repo", stars=r["stars"], language=r["language"])
            for r in s.store.records("MATCH (a:System)-[e:DEPENDS_ON]->(b:System) RETURN a.key AS a, b.key AS b, e.via AS via"):
                sg.edge(f"System:{r['a']}", "DEPENDS_ON", f"System:{r['b']}", via=r["via"])
            for r in s.store.records(
                """
                MATCH (m:System {kind: 'code'})-[:PART_OF]->(repo:System)
                MATCH (pr:Source)-[:TOUCHES]->(m)
                WITH m, repo, count(pr) AS prs WHERE prs >= 5
                RETURN m.key AS key, m.path AS path, repo.key AS repo, prs ORDER BY prs DESC LIMIT 40
                """
            ):
                sg.edge(sg.node("System", r["key"], name=r["path"], kind="code", prs=r["prs"]), "PART_OF", f"System:{r['repo']}")
            # Two simple queries instead of aggregate-then-filtered-MATCH, which FalkorDB evaluates unreliably.
            top = [
                r["handle"]
                for r in s.store.records(
                    "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE s.kind = 'repo' "
                    "RETURN p.handle AS handle, sum(k.score) AS total ORDER BY total DESC LIMIT $n",
                    {"n": people},
                )
            ]
            for r in s.store.records(
                "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE p.handle IN $handles AND s.kind = 'repo' AND k.score >= 0.5 "
                "RETURN p.handle AS handle, p.name AS name, s.key AS key, k.score AS score",
                {"handles": top},
            ):
                pid = sg.node("Person", r["handle"], name=r["name"] or r["handle"])
                sg.edge(pid, "KNOWS_ABOUT", f"System:{r['key']}", score=r["score"])
            for r in s.store.records(
                "MATCH (d:Decision) RETURN d.uid AS uid, d.title AS title, d.ts AS ts, d.drift AS drift, d.repo AS repo ORDER BY d.ts DESC LIMIT 30"
            ):
                sg.edge(sg.node("Decision", r["uid"], title=r["title"], ts=r["ts"], drift=r["drift"]), "AFFECTS", f"System:{r['repo']}")
            return sg.to_dict()

        return await asyncio.to_thread(build)

    @app.get("/api/node")
    async def node(request: Request, id: str) -> dict[str, Any]:
        """Node detail plus its immediate neighbourhood, by UI id ('Label:key')."""
        label, _, key = id.partition(":")
        prop = {"Person": "handle", "System": "key"}.get(label, "uid")
        if label not in {"Person", "System", "Source", "Decision", "Premise", "Answer"} or not key:
            raise HTTPException(400, "id must look like 'Label:key'")
        s = st(request)

        def build() -> dict[str, Any]:
            rows = s.store.records(f"MATCH (n:{label} {{{prop}: $k}}) RETURN properties(n) AS props", {"k": key})
            if not rows:
                raise HTTPException(404, "node not found")
            sg = s.store.subgraph(
                f"MATCH (n:{label} {{{prop}: $k}})-[r]-(m) WHERE NOT m:Answer RETURN n, r, m LIMIT 60",
                {"k": key},
            )
            return {"id": id, "label": label, "props": rows[0]["props"], "neighbors": sg.to_dict()}

        return await asyncio.to_thread(build)

    # ------------------------------------------------------------------ actions (human in the loop)
    async def _act(fn: Any, *args: Any) -> dict[str, Any]:
        try:
            action = await asyncio.to_thread(fn, *args)
        except ActionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return action.public()  # type: ignore[no-any-return]

    @app.get("/api/actions")
    async def list_actions(request: Request) -> dict[str, Any]:
        s = st(request)
        items = await asyncio.to_thread(s.actions.list)
        return {"actions": [a.public() for a in items], "allowed_repos": s.settings.action_repos, "wiki_repo": s.settings.wiki_repo}

    @app.post("/api/actions/wiki", dependencies=[Depends(require_admin)])
    async def propose_wiki(body: WikiRequest, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.propose_wiki_update, body.reason)

    @app.post("/api/actions/route-issue", dependencies=[Depends(require_admin)])
    async def propose_route(body: RouteIssueRequest, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.propose_issue_routing, body.repo, body.number)

    @app.post("/api/actions/{uid}/approve", dependencies=[Depends(require_admin)])
    async def approve_action(uid: str, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.approve, uid)

    @app.post("/api/actions/{uid}/reject", dependencies=[Depends(require_admin)])
    async def reject_action(uid: str, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.reject, uid)

    # ------------------------------------------------------------------ browsable knowledge
    @app.get("/api/workspace")
    async def workspace(request: Request) -> dict[str, Any]:
        s = st(request)
        info = await asyncio.to_thread(catalog.workspace, s.store)
        return {**info, "suggestions": await asyncio.to_thread(catalog.suggestions, s.store)}

    @app.get("/api/decisions")
    async def decisions(request: Request, q: str | None = None, repo: str | None = None, drift: bool = False, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        s = st(request)
        return await asyncio.to_thread(catalog.list_decisions, s.store, q=q, repo=repo, drift_only=drift, limit=min(limit, 200), offset=max(offset, 0))

    @app.get("/api/decisions/{uid:path}")
    async def decision(uid: str, request: Request) -> dict[str, Any]:
        found = await asyncio.to_thread(catalog.decision_detail, st(request).store, uid)
        if found is None:
            raise HTTPException(404, "decision not found")
        return found

    @app.get("/api/people")
    async def people(request: Request, q: str | None = None, limit: int = 60) -> dict[str, Any]:
        return await asyncio.to_thread(catalog.list_people, st(request).store, q=q, limit=min(limit, 200))

    @app.get("/api/people/{handle}")
    async def person(handle: str, request: Request) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(catalog.person_detail, st(request).store, handle)
        except ToolError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/systems")
    async def systems(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(catalog.list_systems, st(request).store)

    @app.get("/api/sources/{uid:path}")
    async def source(uid: str, request: Request) -> dict[str, Any]:
        found = await asyncio.to_thread(catalog.source_detail, st(request).store, uid)
        if found is None:
            raise HTTPException(404, "source not found")
        return found

    @app.get("/api/systems/{key:path}")
    async def system(key: str, request: Request) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(catalog.system_detail, st(request).store, key)
        except ToolError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ------------------------------------------------------------------ workflows
    def _result(r: Any) -> dict[str, Any]:
        return {"summary": r.summary, "data": r.data, "subgraph": r.subgraph.to_dict(), "evidence": [vars(e) for e in r.evidence]}

    async def _workflow(fn: Any, *args: Any) -> dict[str, Any]:
        try:
            return _result(await asyncio.to_thread(fn, *args))
        except ToolError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/workflows/change-impact")
    async def wf_change(body: ChangeRequest, request: Request) -> dict[str, Any]:
        return await _workflow(st(request).tools.change_impact, body.system, body.change)

    @app.post("/api/workflows/incident")
    async def wf_incident(body: IncidentRequest, request: Request) -> dict[str, Any]:
        return await _workflow(st(request).tools.incident_memory, body.description)

    @app.post("/api/workflows/decision-guard")
    async def wf_decision_guard(body: GuardRequest, request: Request) -> dict[str, Any]:
        return await _workflow(st(request).tools.decision_guard, body.title, body.body, body.files)

    @app.post("/api/workflows/handoff")
    async def wf_handoff(body: HandoffRequest, request: Request) -> dict[str, Any]:
        return await _workflow(st(request).tools.handoff_plan, body.handle)

    @app.post("/api/workflows/change-impact/propose", dependencies=[Depends(require_admin)])
    async def wf_change_propose(body: ChangeRequest, request: Request) -> dict[str, Any]:
        ws = st(request)
        result = await asyncio.to_thread(ws.tools.change_impact, body.system, body.change)
        title = f"Heads-up: planned change to {result.data['system']['key']}"
        return await _act(ws.actions.propose_issue, title, result.data["draft"])

    @app.post("/api/workflows/handoff/propose", dependencies=[Depends(require_admin)])
    async def wf_handoff_propose(body: HandoffRequest, request: Request) -> dict[str, Any]:
        ws = st(request)
        result = await asyncio.to_thread(ws.tools.handoff_plan, body.handle)
        handle = result.data["person"]["handle"]
        return await _act(ws.actions.propose_document, f"handoff/{handle}.md", result.data["draft"], f"Handoff plan for {handle}", f"Handoff plan for {handle}.")

    # ------------------------------------------------------------------ repository sync
    @app.get("/api/sync")
    async def sync_status(request: Request) -> dict[str, Any]:
        s = st(request)
        job = s.sync.latest()
        return {"job": job.public() if job else None, "repos": snapshot_repos(s.settings), "github_ready": bool(s.settings.resolve_github_token())}

    @app.post("/api/sync", dependencies=[Depends(require_admin)])
    async def sync_start(body: SyncRequest, request: Request) -> dict[str, Any]:
        ws = st(request)
        if ws.graph != ws.settings.graph_name:
            raise HTTPException(422, "GitHub sync runs in the GitHub workspace; import company exports with `cairn workspace import`.")
        try:
            job = ws.sync.start(body.repos)
        except SyncError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"job": job.public()}

    @app.get("/api/answers")
    async def answers(request: Request, limit: int = 20) -> list[dict[str, Any]]:
        s = st(request)
        return await asyncio.to_thread(
            s.store.records,
            """
            MATCH (a:Answer) OPTIONAL MATCH (a)-[:USED]->(n)
            WITH a, count(n) AS used ORDER BY a.ts DESC LIMIT $limit
            RETURN a.uid AS uid, a.question AS question, a.text AS text, a.ts AS ts, used
            """,
            {"limit": min(limit, 100)},
        )

    return app


app = create_app()
