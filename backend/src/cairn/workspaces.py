"""Workspaces: one FalkorDB graph per company or tenant.

FalkorDB keeps many isolated graphs in one instance, so each workspace is simply a graph named
`<graph_name>` or `<graph_name>_<slug>`. The registry is the database's own graph list; there is no
separate metadata store to keep in sync.
"""

from __future__ import annotations

import re
from typing import Any

from falkordb import FalkorDB

from cairn.config import Settings
from cairn.graph.client import GraphStore

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_workspace_graph(settings: Settings, graph: str) -> bool:
    return bool(_SAFE.match(graph)) and (graph == settings.graph_name or graph.startswith(settings.graph_name + "_"))


def graph_names(settings: Settings) -> list[str]:
    password = settings.falkordb_password.get_secret_value() if settings.falkordb_password else None
    db = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port, password=password)
    graphs = db.list_graphs()
    # Test graphs created by the integration suite are never workspaces.
    return sorted(g for g in graphs if is_workspace_graph(settings, g) and not g.startswith(f"{settings.graph_name}_test_"))


def describe(store: GraphStore) -> dict[str, Any]:
    meta = store.records("MATCH (w:Workspace {key: 'self'}) RETURN w.name AS name, w.description AS description")
    counts = store.counts()["nodes"]
    name = meta[0]["name"] if meta else None
    if not name:
        repos = store.records("MATCH (s:System {kind: 'repo'}) RETURN s.key AS key LIMIT 1")
        name = repos[0]["key"].split("/")[0] if repos and "/" in repos[0]["key"] else store.name
    return {
        "graph": store.name,
        "name": name,
        "description": meta[0]["description"] if meta else "",
        "people": counts.get("Person", 0),
        "decisions": counts.get("Decision", 0),
        "sources": counts.get("Source", 0),
    }


def list_workspaces(settings: Settings) -> list[dict[str, Any]]:
    return [describe(GraphStore(settings, graph_name=g)) for g in graph_names(settings)]
