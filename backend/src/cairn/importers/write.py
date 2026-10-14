"""Write an imported workspace into its own FalkorDB graph."""

from __future__ import annotations

import logging
from typing import Any

from cairn.agent.tools import _stem, _words
from cairn.derive.expertise import derive_collaboration, derive_expertise
from cairn.extract.pipeline import EXTRACT_VERSION
from cairn.github.fetch import load_snapshot
from cairn.graph.client import GraphStore
from cairn.importers.workspace import Workspace
from cairn.ingest.loader import Rows, build_rows, write_rows

log = logging.getLogger(__name__)


def merge_github(ws: Workspace) -> None:
    """Fold optional GitHub snapshots (workspace/github/<owner>__<repo>/) into the workspace rows."""
    gh_dir = ws.root / "github"
    if not gh_dir.exists():
        return
    repos = sorted(d.name.replace("__", "/", 1) for d in gh_dir.iterdir() if (d / "meta.json").exists())
    if not repos:
        return
    gh = build_rows([load_snapshot(gh_dir, r) for r in repos])
    for name in Rows.__dataclass_fields__:
        mine, theirs = getattr(ws.rows, name), getattr(gh, name)
        if isinstance(mine, dict):
            for k, v in theirs.items():
                mine.setdefault(k, v)
        elif isinstance(mine, list):
            mine.extend(theirs)
    ws.stats["github_repos"] = len(repos)


def import_workspace(store: GraphStore, ws: Workspace, *, reset: bool = True) -> dict[str, Any]:
    merge_github(ws)
    if reset:
        store.drop()
    written = write_rows(store, ws.rows)
    decisions = _write_decisions(store, ws)
    challenges = _write_challenges(store, ws)
    knows = derive_expertise(store)
    works = derive_collaboration(store)
    store.query(
        "MERGE (w:Workspace {key: 'self'}) SET w.name = $name, w.description = $desc",
        {"name": ws.name, "desc": ws.description},
    )
    return {**dict(ws.stats), "written": sum(written.values()), "decisions": decisions, "challenges": challenges, "knows_about": knows, "works_with": works}


def _write_decisions(store: GraphStore, ws: Workspace) -> int:
    by_doc_id = {d["doc_id"]: d["uid"] for d in ws.decisions if d["doc_id"]}
    for d in ws.decisions:
        store.query(
            """
            MATCH (t:Source {uid: $thread})
            MERGE (d:Decision {uid: $uid})
            SET d.title = $title, d.summary = $summary, d.category = $category, d.ts = $ts, d.repo = $repo,
                d.status = $status, d.confidence = 1.0, d.statement = $statement, d.url = t.url, d.doc_id = $doc_id
            MERGE (d)-[:DECIDED_IN]->(t)
            // ADRs are authoritative: skip LLM extraction for this document.
            SET t.extract_version = $version, t.decision_count = 1
            """,
            {
                **{k: d[k] for k in ("uid", "title", "summary", "category", "ts", "status", "statement", "doc_id")},
                "thread": d["thread"],
                "repo": ws.name,
                "version": EXTRACT_VERSION,
            },
        )
        if d["deciders"]:
            store.query(
                "MATCH (d:Decision {uid: $uid}) UNWIND $h AS h MATCH (p:Person {handle: h}) MERGE (p)-[:DECIDED]->(d)", {"uid": d["uid"], "h": d["deciders"]}
            )
        if d["systems"]:
            store.query(
                "MATCH (d:Decision {uid: $uid}) UNWIND $k AS k MATCH (s:System {key: k}) MERGE (d)-[:AFFECTS]->(s)", {"uid": d["uid"], "k": d["systems"]}
            )
        rows = [{"uid": f"{d['uid']}::p{i + 1}", "text": p, "rel": "BECAUSE", "option": None} for i, p in enumerate(d["premises"])]
        rows += [{"uid": f"{d['uid']}::r{i + 1}", "text": r["text"], "rel": "REJECTED", "option": r["option"]} for i, r in enumerate(d["rejected"])]
        for rel in ("BECAUSE", "REJECTED"):
            subset = [r for r in rows if r["rel"] == rel]
            if subset:
                store.query(
                    f"""
                    MATCH (d:Decision {{uid: $uid}}), (t:Source {{uid: $thread}})
                    UNWIND $rows AS r
                    MERGE (p:Premise {{uid: r.uid}})
                    SET p.text = r.text, p.ts = $ts, p.kind = toLower('{rel}'), p.option = r.option
                    MERGE (d)-[:{rel}]->(p)
                    MERGE (p)-[e:EVIDENCED_BY]->(t) SET e.quote = r.text
                    """,
                    {"uid": d["uid"], "thread": d["thread"], "rows": subset, "ts": d["ts"]},
                )
        for old_id in d["supersedes"]:
            if old := by_doc_id.get(old_id):
                store.query(
                    "MATCH (n:Decision {uid: $new}), (o:Decision {uid: $old}) MERGE (n)-[:SUPERSEDES]->(o) SET o.status = 'superseded'",
                    {"new": d["uid"], "old": old},
                )
    return len(ws.decisions)


def _write_challenges(store: GraphStore, ws: Workspace) -> int:
    """A postmortem that names a decision and lists failed assumptions challenges that decision's closest premise."""
    by_doc_id = {d["doc_id"]: d for d in ws.decisions if d["doc_id"]}
    n = 0
    for c in ws.challenges:
        decision = by_doc_id.get(c["target_doc_id"])
        if not decision or not decision["premises"]:
            continue
        claim = {_stem(w) for w in _words(c["quote"])}
        scored = [(len(claim & {_stem(w) for w in _words(p)}), i) for i, p in enumerate(decision["premises"])]
        best, idx = max(scored)
        if best == 0:
            continue
        store.query(
            """
            MATCH (s:Source {uid: $src}), (p:Premise {uid: $premise}), (d:Decision {uid: $decision})
            MERGE (s)-[e:CHALLENGES]->(p)
            SET e.quote = $quote, e.verdict = 'contradicts', e.confidence = 1.0,
                e.explanation = 'Stated as a failed assumption in a postmortem.', e.decided_by = 'document'
            SET d.drift = true
            """,
            {"src": c["source"], "premise": f"{decision['uid']}::p{idx + 1}", "decision": decision["uid"], "quote": c["quote"]},
        )
        n += 1
    return n
