"""Cairn as an MCP server: the company brain's graph tools inside Claude Desktop, Cursor or any MCP client.

Read-only by design: tools query the FalkorDB graph and return data plus the sources behind it.
Actions that write to GitHub stay in Cairn's own UI/CLI, where a human approves them.

    cairn mcp                      # stdio (Claude Desktop / Cursor)
    cairn mcp --transport streamable-http
"""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any, Literal, cast

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from cairn.agent.tools import ToolResult, Tools
from cairn.config import get_settings
from cairn.graph.client import GraphStore

INSTRUCTIONS = """Cairn is a company brain built on a FalkorDB knowledge graph of an engineering org's people,
systems (repos/modules), PRs, issues, docs and the decisions extracted from them. Every result includes
`sources` (URLs, and verbatim quotes where available); cite them. Start with `search` or `list_systems`
to find valid names, use `why` for rationale, `who_knows` for expertise, `impact` for blast radius,
`suggest_owners` to route an issue, and `drift_report` for decisions whose reasons no longer hold."""

mcp = MCPServer(name="cairn", title="Cairn company brain", instructions=INSTRUCTIONS, version="0.1.0")
_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


@lru_cache(maxsize=1)
def _tools() -> Tools:
    return Tools(GraphStore(get_settings()))


def _out(result: ToolResult) -> dict[str, Any]:
    sources = {e.uid: {k: v for k, v in asdict(e).items() if v is not None} for e in result.evidence}
    return {"summary": result.summary, "data": result.data, "sources": list(sources.values())}


@mcp.tool(annotations=_READ_ONLY)
def search(query: str) -> dict[str, Any]:
    """Full-text search over decisions, PRs, issues, docs and systems."""
    return _out(_tools().search(query))


@mcp.tool(annotations=_READ_ONLY)
def why(topic: str) -> dict[str, Any]:
    """Why is something the way it is? Decisions on a topic with premises, verbatim evidence, rejected options and drift."""
    return _out(_tools().why(topic))


@mcp.tool(annotations=_READ_ONLY)
def who_knows(system: str) -> dict[str, Any]:
    """Rank people by recency-weighted expertise on a repo ('FalkorDB/QueryWeaver') or module ('api/routes')."""
    return _out(_tools().who_knows(system))


@mcp.tool(annotations=_READ_ONLY)
def impact(system: str) -> dict[str, Any]:
    """Blast radius of a system: transitive dependents, contacts, open work and relevant decisions."""
    return _out(_tools().impact(system))


@mcp.tool(annotations=_READ_ONLY)
def suggest_owners(text: str) -> dict[str, Any]:
    """Given an issue or bug description, find the systems it likely touches and who should handle it."""
    return _out(_tools().suggest_owners(text))


@mcp.tool(annotations=_READ_ONLY)
def change_impact(system: str, change: str = "") -> dict[str, Any]:
    """Before changing a system: dependents, constraining decisions, colliding work, and who to notify (with a draft)."""
    return _out(_tools().change_impact(system, change))


@mcp.tool(annotations=_READ_ONLY)
def incident_memory(description: str) -> dict[str, Any]:
    """When something breaks: similar past incidents, what fixed them, who handled them, and stale runbooks."""
    return _out(_tools().incident_memory(description))


@mcp.tool(annotations=_READ_ONLY)
def handoff_plan(handle: str) -> dict[str, Any]:
    """When someone leaves: what only they know, successors, docs to read, decisions and work to re-own."""
    return _out(_tools().handoff_plan(handle))


@mcp.tool(annotations=_READ_ONLY)
def details(uid: str) -> dict[str, Any]:
    """Open one PR/issue/doc ('owner/repo#123') or decision ('owner/repo#123::d1') with people, links and decisions."""
    return _out(_tools().details(uid))


@mcp.tool(annotations=_READ_ONLY)
def person_profile(handle: str) -> dict[str, Any]:
    """What a person knows, decided and is working on, and where their absence would leave a gap."""
    return _out(_tools().person_profile(handle))


@mcp.tool(annotations=_READ_ONLY)
def timeline(system: str, since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """What happened to a system between two dates (YYYY-MM-DD)."""
    return _out(_tools().timeline(system, since, until))


@mcp.tool(annotations=_READ_ONLY)
def bus_factor(repo: str | None = None) -> dict[str, Any]:
    """Modules whose knowledge is concentrated in one person, plus bridge people (betweenness centrality)."""
    return _out(_tools().bus_factor(repo))


@mcp.tool(annotations=_READ_ONLY)
def drift_report(repo: str | None = None) -> dict[str, Any]:
    """Decisions whose premises were contradicted by later evidence."""
    return _out(_tools().drift_report(repo))


@mcp.tool(annotations=_READ_ONLY)
def teams() -> dict[str, Any]:
    """De-facto teams inferred by community detection over who reviews and replies to whom."""
    return _out(_tools().teams())


@mcp.tool(annotations=_READ_ONLY)
def connect(a: str, b: str) -> dict[str, Any]:
    """Shortest path between two entities: people, systems, or uids like 'FalkorDB/QueryWeaver#123'."""
    return _out(_tools().connect(a, b))


@mcp.tool(annotations=_READ_ONLY)
def list_systems(repo: str | None = None) -> dict[str, Any]:
    """List known repos and modules with activity counts."""
    return _out(_tools().list_systems(repo))


Transport = Literal["stdio", "sse", "streamable-http"]


def main(transport: str = "stdio") -> None:
    if transport not in ("stdio", "sse", "streamable-http"):
        raise ValueError(f"unknown transport {transport!r}")
    mcp.run(transport=cast(Transport, transport))
