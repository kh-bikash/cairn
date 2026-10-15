"""Export a graph to plain JSON and import it back: an exact, version-independent copy of a workspace.

The demo snapshot ships finished graphs this way, so `cairn demo` restores exactly what the live site shows,
independent of which models or prompts produced the decisions. Rebuilding from raw data remains possible
(`cairn demo --rebuild`), but replays only what the LLM cache holds.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from cairn.graph.client import GraphStore

_PAGE = 5_000  # stays under the server's RESULTSET_SIZE
_PID = "_pid"  # temporary property linking edges to the nodes created for them


def export_graph(store: GraphStore) -> dict[str, Any]:
    # Keyset paging from 0 upward: FalkorDB returns no rows at all for `id(n) > -1`, so bounds stay non-negative.
    nodes: list[dict[str, Any]] = []
    start = 0
    while True:
        rows = store.ro_query(
            "MATCH (n) WHERE id(n) >= $start RETURN id(n), labels(n), properties(n) ORDER BY id(n) LIMIT $page",
            {"start": start, "page": _PAGE},
            timeout_ms=60_000,
        )
        nodes += [{"id": r[0], "labels": list(r[1]), "props": r[2]} for r in rows]
        if len(rows) < _PAGE:
            break
        start = rows[-1][0] + 1
    edges: list[dict[str, Any]] = []
    start = 0
    while True:
        rows = store.ro_query(
            "MATCH (a)-[r]->(b) WHERE id(r) >= $start RETURN id(r), type(r), id(a), id(b), properties(r) ORDER BY id(r) LIMIT $page",
            {"start": start, "page": _PAGE},
            timeout_ms=60_000,
        )
        edges += [{"type": r[1], "src": r[2], "dst": r[3], "props": r[4]} for r in rows]
        if len(rows) < _PAGE:
            break
        start = rows[-1][0] + 1
    return {"format": "cairn-graph/1", "graph": store.name, "nodes": nodes, "edges": edges}


def import_graph(store: GraphStore, data: dict[str, Any]) -> dict[str, int]:
    """Replace the store's graph with an exported one."""
    if data.get("format") != "cairn-graph/1":
        raise ValueError(f"unsupported graph export format: {data.get('format')!r}")
    store.drop()
    store.ensure_schema()
    by_labels: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    label_of: dict[int, str] = {}
    for n in data["nodes"]:
        labels = tuple(n["labels"])
        by_labels[labels].append({"id": n["id"], "props": n["props"]})
        label_of[n["id"]] = labels[0]
    primary = sorted({labels[0] for labels in by_labels})
    for label in primary:
        store.query(f"CREATE INDEX FOR (n:{label}) ON (n.{_PID})")
    for labels, rows in by_labels.items():
        store.write_batches(f"UNWIND $rows AS r CREATE (n:{':'.join(labels)}) SET n = r.props, n.{_PID} = r.id", rows)
    by_shape: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in data["edges"]:
        by_shape[(label_of[e["src"]], e["type"], label_of[e["dst"]])].append({"s": e["src"], "d": e["dst"], "props": e["props"]})
    for (src, rel, dst), rows in by_shape.items():
        store.write_batches(
            f"UNWIND $rows AS r MATCH (a:{src} {{{_PID}: r.s}}), (b:{dst} {{{_PID}: r.d}}) CREATE (a)-[e:{rel}]->(b) SET e = r.props",
            rows,
        )
    for label in primary:
        store.query(f"MATCH (n:{label}) SET n.{_PID} = NULL")
        store.query(f"DROP INDEX FOR (n:{label}) ON (n.{_PID})")
    return {"nodes": len(data["nodes"]), "edges": len(data["edges"])}


def save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 and sorted keys: identical graphs give identical bytes, so the snapshot only changes when data does.
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        gz.write(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def load(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as fh:
        data: dict[str, Any] = json.loads(fh.read().decode("utf-8"))
    return data
