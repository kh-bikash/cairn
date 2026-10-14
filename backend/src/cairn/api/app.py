"""HTTP API: streaming agent answers (SSE), direct tool calls for dashboard panels, and graph browsing."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from cairn.actions.service import ActionError, ActionService
from cairn.agent.loop import Agent
from cairn.agent.tools import TOOL_SPECS, ToolError, Tools, dispatch
from cairn.config import Settings, get_settings
from cairn.graph.client import GraphStore, SubGraph
from cairn.llm.client import LLM

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


class WikiRequest(BaseModel):
    reason: str = Field(default="Refresh the knowledge base from the graph", max_length=500)


class State:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = GraphStore(settings)
        self.llm = LLM(settings)
        self.agent = Agent(self.store, self.llm, settings)
        self.tools = Tools(self.store)
        self.actions = ActionService(self.store, settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    state = State(settings)
    if state.store.ping():
        await asyncio.to_thread(state.store.ensure_schema)
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
    )

    def st(request: Request) -> State:
        return request.app.state.cairn  # type: ignore[no-any-return]

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, Any]:
        s = st(request)
        ok = await asyncio.to_thread(s.store.ping)
        return {
            "ok": ok,
            "graph": s.store.name,
            "llm": {role: {"model": getattr(s.settings, f"model_{role}"), "ready": s.llm.available(role)} for role in ("decide", "extract", "agent")},
            "repos": s.settings.repos,
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

    @app.post("/api/actions/wiki")
    async def propose_wiki(body: WikiRequest, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.propose_wiki_update, body.reason)

    @app.post("/api/actions/route-issue")
    async def propose_route(body: RouteIssueRequest, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.propose_issue_routing, body.repo, body.number)

    @app.post("/api/actions/{uid}/approve")
    async def approve_action(uid: str, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.approve, uid)

    @app.post("/api/actions/{uid}/reject")
    async def reject_action(uid: str, request: Request) -> dict[str, Any]:
        return await _act(st(request).actions.reject, uid)

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
