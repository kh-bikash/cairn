"""Thin, typed wrapper around the FalkorDB client."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from falkordb import Edge, FalkorDB, Graph, Node, Path

from cairn.config import Settings
from cairn.graph.schema import FULLTEXT_INDEXES, KEY_PROPS, RANGE_INDEXES

log = logging.getLogger(__name__)

# Large text properties we never ship to the UI in graph payloads.
_HEAVY_PROPS = frozenset({"body"})


@dataclass
class SubGraph:
    """Nodes and edges in a UI-friendly shape, deduplicated by id."""

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, value: Any) -> None:
        """Add any FalkorDB value (node, edge, path, or nested list/dict of them)."""
        nodes: list[Node] = []
        edges: list[Edge] = []
        _collect(value, nodes, edges)
        # Nodes first: an edge can only be resolved once both endpoints are known.
        for n in nodes:
            self._add_node(n)
        for e in edges:
            self._add_edge(e)

    def node(self, label: str, key: Any, **props: Any) -> str:
        """Add a node built from query records (rather than a FalkorDB Node object)."""
        nid = f"{label}:{key}"
        existing = self.nodes.get(nid)
        clean = {k: v for k, v in props.items() if v is not None and k not in _HEAVY_PROPS}
        if existing:
            existing["props"].update(clean)
        else:
            self.nodes[nid] = {"id": nid, "label": label, "props": {KEY_PROPS.get(label, "key"): key, **clean}}
        return nid

    def edge(self, src: str, rel: str, dst: str, **props: Any) -> None:
        eid = f"{src}|{rel}|{dst}"
        self.edges.setdefault(eid, {"id": eid, "type": rel, "source": src, "target": dst, "props": props})

    def merge(self, other: SubGraph) -> None:
        self.nodes.update(other.nodes)
        self.edges.update(other.edges)

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        # Drop edges whose endpoints we never saw; the UI can't draw them.
        edges = [e for e in self.edges.values() if e["source"] in self.nodes and e["target"] in self.nodes]
        return {"nodes": list(self.nodes.values()), "edges": edges}

    # FalkorDB internal ids are reused after deletes, so the UI gets stable ids from key props.
    _internal_to_id: dict[int, str] = field(default_factory=dict)

    def _add_node(self, n: Node) -> None:
        nid = node_id(n)
        self._internal_to_id[n.id] = nid
        if nid in self.nodes:
            return
        label = n.labels[0] if n.labels else "Node"
        props = {k: v for k, v in n.properties.items() if k not in _HEAVY_PROPS}
        self.nodes[nid] = {"id": nid, "label": label, "props": props}

    def _add_edge(self, e: Edge) -> None:
        src = self._internal_to_id.get(e.src_node)
        dst = self._internal_to_id.get(e.dest_node)
        if src is None or dst is None:
            return
        eid = f"{src}|{e.relation}|{dst}"
        self.edges.setdefault(eid, {"id": eid, "type": e.relation, "source": src, "target": dst, "props": dict(e.properties)})


def _collect(value: Any, nodes: list[Node], edges: list[Edge]) -> None:
    if isinstance(value, Node):
        nodes.append(value)
    elif isinstance(value, Edge):
        edges.append(value)
    elif isinstance(value, Path):
        nodes.extend(value.nodes())
        edges.extend(value.edges())
    elif isinstance(value, Mapping):
        for v in value.values():
            _collect(v, nodes, edges)
    elif isinstance(value, list | tuple):
        for v in value:
            _collect(v, nodes, edges)


def node_id(n: Node) -> str:
    label = n.labels[0] if n.labels else "Node"
    key = KEY_PROPS.get(label)
    if key and key in n.properties:
        return f"{label}:{n.properties[key]}"
    return f"{label}#{n.id}"


class GraphStore:
    """All Cypher goes through here so we have one place for logging, params and errors."""

    def __init__(self, settings: Settings, graph_name: str | None = None) -> None:
        password = settings.falkordb_password.get_secret_value() if settings.falkordb_password else None
        self._db = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port, password=password)
        self.name = graph_name or settings.graph_name
        self._graph: Graph = self._db.select_graph(self.name)

    # ------------------------------------------------------------------ querying
    def query(self, cypher: str, params: Mapping[str, Any] | None = None, *, timeout_ms: int = 60_000) -> list[list[Any]]:
        result = self._graph.query(cypher, clean_params(dict(params or {})), timeout=timeout_ms)
        rows: list[list[Any]] = result.result_set
        return rows

    def ro_query(self, cypher: str, params: Mapping[str, Any] | None = None, *, timeout_ms: int = 10_000) -> list[list[Any]]:
        result = self._graph.ro_query(cypher, dict(params or {}), timeout=timeout_ms)
        rows: list[list[Any]] = result.result_set
        return rows

    def records(self, cypher: str, params: Mapping[str, Any] | None = None, *, timeout_ms: int = 10_000) -> list[dict[str, Any]]:
        """Read-only query returning rows as dicts keyed by column name."""
        result = self._graph.ro_query(cypher, dict(params or {}), timeout=timeout_ms)
        cols = [h[1] if isinstance(h, list | tuple) else h for h in result.header]
        return [dict(zip(cols, row, strict=True)) for row in result.result_set]

    def subgraph(self, cypher: str, params: Mapping[str, Any] | None = None) -> SubGraph:
        sg = SubGraph()
        for row in self.ro_query(cypher, params):
            sg.add(row)
        return sg

    def write_batches(self, cypher: str, rows: Sequence[Mapping[str, Any]], *, batch_size: int = 500) -> int:
        """Run an `UNWIND $rows AS r ...` statement in batches. Returns rows written."""
        total = 0
        for chunk in _chunks(rows, batch_size):
            # The stock image defaults to a 1s query timeout; batched MERGEs need more headroom.
            self._graph.query(cypher, {"rows": clean_params(list(chunk))}, timeout=120_000)
            total += len(chunk)
        return total

    # ------------------------------------------------------------------ lifecycle
    def ping(self) -> bool:
        try:
            return bool(self._db.connection.ping())
        except Exception:  # noqa: BLE001 - any connection failure means "not healthy"
            return False

    def ensure_schema(self) -> None:
        existing = self._existing_indexes()
        for idx in RANGE_INDEXES:
            if "RANGE" not in existing.get((idx.label, idx.prop), set()):
                self._graph.query(idx.cypher())
        for ft in FULLTEXT_INDEXES:
            if not all("FULLTEXT" in existing.get((ft.label, p), set()) for p in ft.props):
                self._graph.query(ft.cypher())
        log.info("schema ready on graph %s", self.name)

    def drop(self) -> None:
        try:
            self._graph.delete()
        except Exception as exc:  # noqa: BLE001 - deleting a graph that doesn't exist is fine
            log.debug("drop graph %s: %s", self.name, exc)
        self._graph = self._db.select_graph(self.name)

    def counts(self) -> dict[str, Any]:
        nodes = {r[0]: r[1] for r in self.ro_query("MATCH (n) RETURN labels(n)[0], count(n)")}
        rels = {r[0]: r[1] for r in self.ro_query("MATCH ()-[r]->() RETURN type(r), count(r)")}
        return {"nodes": nodes, "relationships": rels}

    def _existing_indexes(self) -> dict[tuple[str, str], set[str]]:
        out: dict[tuple[str, str], set[str]] = {}
        try:
            rows = self.ro_query("CALL db.indexes() YIELD label, properties, types RETURN label, properties, types")
        except Exception:  # noqa: BLE001 - empty graph has no index catalogue yet
            return out
        for label, _props, types in rows:
            for prop, kinds in dict(types).items():
                out.setdefault((label, prop), set()).update(kinds)
        return out


def _chunks(rows: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def flatten(values: Iterable[Iterable[Any]]) -> list[Any]:
    return [v for row in values for v in row]


_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_params(value: Any) -> Any:
    """Replace unpaired UTF-16 surrogates (half an emoji in scraped text) so FalkorDB never stores invalid UTF-8."""
    if isinstance(value, str):
        return _SURROGATE.sub("�", value) if _SURROGATE.search(value) else value
    if isinstance(value, dict):
        return {k: clean_params(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean_params(v) for v in value]
    return value
