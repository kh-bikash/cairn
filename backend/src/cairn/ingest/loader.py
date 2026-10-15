"""Turn GitHub snapshots into graph rows (pure) and write them to FalkorDB (idempotent MERGEs)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from cairn.graph.client import GraphStore
from cairn.ingest import systems as sysmap
from cairn.ingest.text import (
    DEPENDENCY_BOTS,
    child_uid,
    clean_body,
    clip,
    doc_uid,
    extract_refs,
    is_bot,
    is_design_doc,
    login_of,
    rationale_signal,
    to_ms,
    top_uid,
)

log = logging.getLogger(__name__)

_BODY_LIMIT = 12_000
_COMMENT_LIMIT = 4_000


@dataclass
class Rows:
    """Everything one or more snapshots contribute to the graph, as plain dicts."""

    people: dict[str, dict[str, Any]] = field(default_factory=dict)
    repos: list[dict[str, Any]] = field(default_factory=list)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    children: list[dict[str, Any]] = field(default_factory=list)
    authored: list[dict[str, Any]] = field(default_factory=list)
    reviewed: list[dict[str, Any]] = field(default_factory=list)
    merged: list[dict[str, Any]] = field(default_factory=list)
    assigned: list[dict[str, Any]] = field(default_factory=list)
    touches: list[dict[str, Any]] = field(default_factory=list)
    closes: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    owns: list[dict[str, Any]] = field(default_factory=list)
    depends: list[dict[str, Any]] = field(default_factory=list)
    # Org structure and non-GitHub sources (Slack, tickets, docs) from workspace importers.
    teams: list[dict[str, Any]] = field(default_factory=list)
    member_of: list[dict[str, Any]] = field(default_factory=list)
    reports_to: list[dict[str, Any]] = field(default_factory=list)
    team_owns: list[dict[str, Any]] = field(default_factory=list)
    mentions: list[dict[str, Any]] = field(default_factory=list)
    skipped_bot_prs: int = 0


def build_rows(snapshots: list[dict[str, Any]]) -> Rows:
    rows = Rows()
    manifests: dict[str, sysmap.Manifest] = {}
    for snap in snapshots:
        _add_snapshot(rows, snap)
        manifests[snap["repo"]] = sysmap.parse_manifests(snap.get("manifests", []))
    rows.depends = [{"src": a, "dst": b, "via": pkg} for a, b, pkg in sysmap.repo_dependencies(manifests)]
    known = {s["uid"] for s in rows.sources}
    # Only keep references between nodes we actually have, and never self-references.
    rows.references = [r for r in _dedupe(rows.references, ("src", "dst")) if r["dst"] in known and r["src"] != r["dst"]]
    rows.closes = [r for r in rows.closes if r["dst"] in known]
    return rows


def _person(rows: Rows, actor: dict[str, Any] | None) -> str | None:
    """Register a human and return their handle; bots and deleted users return None."""
    if is_bot(actor):
        return None
    handle = login_of(actor)
    if handle is None:
        return None
    entry = rows.people.setdefault(handle, {"handle": handle, "name": None})
    if actor and actor.get("name") and not entry["name"]:
        entry["name"] = actor["name"]
    return handle


def _add_snapshot(rows: Rows, snap: dict[str, Any]) -> None:
    repo: str = snap["repo"]
    meta = snap["meta"]
    rows.repos.append(
        {
            "key": repo,
            "name": meta.get("name") or repo.split("/")[-1],
            "description": meta.get("description") or "",
            "url": meta.get("url") or f"https://github.com/{repo}",
            "language": (meta.get("primaryLanguage") or {}).get("name"),
            "stars": meta.get("stargazerCount") or 0,
        }
    )

    for pr in snap["prs"]:
        _add_pr(rows, repo, pr)
    for issue in snap["issues"]:
        _add_issue(rows, repo, issue)
    for disc in snap["discussions"]:
        _add_discussion(rows, repo, disc)
    for doc in snap["docs"]:
        rows.sources.append(
            {
                "uid": doc_uid(repo, doc["path"]),
                "kind": "doc",
                "repo": repo,
                "number": None,
                "title": doc["path"],
                "body": clip(doc["text"], _BODY_LIMIT),
                "url": doc["url"],
                "ts": to_ms(meta.get("fetched_at")),
                "state": "current",
                "author": None,
                "labels": [],
                "signal": rationale_signal(doc["text"]),
                "design": is_design_doc(doc["path"]),
            }
        )

    for rule in _codeowners(meta):
        targets = (
            [repo]
            if sysmap.is_repo_wide(rule.pattern)
            else [m["key"] for m in rows.modules.values() if m["repo"] == repo and sysmap.rule_matches_module(rule.pattern, m["path"])]
        )
        for owner in rule.owners:
            rows.people.setdefault(owner, {"handle": owner, "name": None})
            rows.owns.extend({"handle": owner, "key": t, "via": f"CODEOWNERS {rule.pattern}"} for t in targets)


def _codeowners(meta: dict[str, Any]) -> list[sysmap.OwnerRule]:
    for key in ("codeowners1", "codeowners2", "codeowners3"):
        blob = meta.get(key)
        if isinstance(blob, dict) and blob.get("text"):
            return sysmap.parse_codeowners(blob["text"])
    return []


def _top_source(repo: str, item: dict[str, Any], kind: str, state: str, author: str | None) -> dict[str, Any]:
    return {
        "uid": top_uid(repo, item["number"]),
        "kind": kind,
        "repo": repo,
        "number": item["number"],
        "title": item.get("title") or "",
        "body": clip(clean_body(item.get("body")), _BODY_LIMIT),
        "url": item["url"],
        "ts": to_ms(item["createdAt"]),
        "state": state,
        "author": author or login_of(item.get("author")),
        "labels": [lbl["name"] for lbl in (item.get("labels") or {}).get("nodes", [])],
        "signal": rationale_signal(f"{item.get('title') or ''}\n{item.get('body') or ''}"),
    }


def _add_pr(rows: Rows, repo: str, pr: dict[str, Any]) -> None:
    if (login_of(pr.get("author")) or "").lower() in DEPENDENCY_BOTS:
        rows.skipped_bot_prs += 1
        return
    uid = top_uid(repo, pr["number"])
    author = _person(rows, pr.get("author"))
    state = "merged" if pr.get("mergedAt") else pr["state"].lower()
    src = _top_source(repo, pr, "pr", state, author)
    src["merged_ts"] = to_ms(pr.get("mergedAt"))
    rows.sources.append(src)
    if author:
        rows.authored.append({"handle": author, "uid": uid})
    if (merger := _person(rows, pr.get("mergedBy"))) and src["merged_ts"]:
        rows.merged.append({"handle": merger, "uid": uid, "ts": src["merged_ts"]})

    # Aggregate file churn per module so one PR yields one TOUCHES edge per module.
    churn: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for f in (pr.get("files") or {}).get("nodes", []):
        mod = sysmap.module_path(f["path"])
        if mod is None:
            continue
        key = sysmap.module_key(repo, mod)
        rows.modules.setdefault(key, {"key": key, "repo": repo, "path": mod, "name": mod, "kind": sysmap.module_kind(mod)})
        c = churn[key]
        c[0] += f.get("additions") or 0
        c[1] += f.get("deletions") or 0
        c[2] += 1
    rows.touches.extend({"uid": uid, "key": k, "additions": a, "deletions": d, "files": n} for k, (a, d, n) in churn.items())

    for ref in (pr.get("closingIssuesReferences") or {}).get("nodes", []):
        rows.closes.append({"src": uid, "dst": top_uid(repo, ref["number"])})
    rows.references.extend({"src": uid, "dst": r} for r in extract_refs(f"{src['title']}\n{src['body']}", repo))

    for i, rv in enumerate((pr.get("reviews") or {}).get("nodes", [])):
        reviewer = _person(rows, rv.get("author"))
        if not reviewer:
            continue
        rows.reviewed.append({"handle": reviewer, "uid": uid, "state": rv.get("state"), "ts": to_ms(rv.get("submittedAt"))})
        if (rv.get("body") or "").strip():
            _add_child(rows, repo, uid, pr, rv, i, "review", reviewer, ts_key="submittedAt")
    _add_comments(rows, repo, uid, pr)


def _add_issue(rows: Rows, repo: str, issue: dict[str, Any]) -> None:
    uid = top_uid(repo, issue["number"])
    author = _person(rows, issue.get("author"))
    src = _top_source(repo, issue, "issue", issue["state"].lower(), author)
    rows.sources.append(src)
    if author:
        rows.authored.append({"handle": author, "uid": uid})
    for a in (issue.get("assignees") or {}).get("nodes", []):
        if handle := _person(rows, a):
            rows.assigned.append({"handle": handle, "uid": uid})
    rows.references.extend({"src": uid, "dst": r} for r in extract_refs(f"{src['title']}\n{src['body']}", repo))
    _add_comments(rows, repo, uid, issue)


def _add_discussion(rows: Rows, repo: str, disc: dict[str, Any]) -> None:
    uid = top_uid(repo, disc["number"])
    author = _person(rows, disc.get("author"))
    src = _top_source(repo, disc, "discussion", "closed" if disc.get("closed") else "open", author)
    category = (disc.get("category") or {}).get("name")
    src["labels"] = [category] if category else []
    rows.sources.append(src)
    if author:
        rows.authored.append({"handle": author, "uid": uid})
    _add_comments(rows, repo, uid, disc)


def _add_comments(rows: Rows, repo: str, parent_uid: str, parent: dict[str, Any]) -> None:
    for i, c in enumerate((parent.get("comments") or {}).get("nodes", [])):
        handle = _person(rows, c.get("author"))
        if handle and (c.get("body") or "").strip():
            _add_child(rows, repo, parent_uid, parent, c, i, "comment", handle, ts_key="createdAt")


def _add_child(rows: Rows, repo: str, parent_uid: str, parent: dict[str, Any], item: dict[str, Any], index: int, kind: str, handle: str, *, ts_key: str) -> None:
    uid = child_uid(parent_uid, item.get("url"), index, kind)
    body = clip(clean_body(item.get("body")), _COMMENT_LIMIT)
    rows.children.append(
        {
            "uid": uid,
            "kind": kind,
            "repo": repo,
            "number": parent["number"],
            "title": f"{kind} on #{parent['number']}: {parent.get('title') or ''}"[:300],
            "body": body,
            "url": item.get("url") or parent["url"],
            "ts": to_ms(item.get(ts_key)),
            "state": "",
            "author": handle,
            "labels": [],
            "parent": parent_uid,
        }
    )
    rows.authored.append({"handle": handle, "uid": uid})
    rows.references.extend({"src": uid, "dst": r} for r in extract_refs(body, repo))


def _dedupe(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for it in items:
        k = tuple(it[x] for x in keys)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# --------------------------------------------------------------------------- writing

_SOURCE_SET = """
SET s.kind = r.kind, s.repo = r.repo, s.number = r.number, s.title = r.title, s.body = r.body,
    s.url = r.url, s.ts = r.ts, s.state = r.state, s.author = r.author, s.labels = r.labels, s.signal = coalesce(r.signal, 0), s.design = coalesce(r.design, false)
"""

WRITES: list[tuple[str, str]] = [
    (
        "people",
        "UNWIND $rows AS r MERGE (p:Person {handle: r.handle}) SET p.name = coalesce(r.name, p.name), p.is_bot = false, "
        "p.role = coalesce(r.role, p.role), p.email = coalesce(r.email, p.email), p.left = coalesce(r.left, p.left), "
        "p.joined = coalesce(r.joined, p.joined), p.team = coalesce(r.team, p.team)",
    ),
    ("teams", "UNWIND $rows AS r MERGE (t:Team {key: r.key}) SET t.name = r.name, t.description = r.description"),
    ("member_of", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (t:Team {key: r.team}) MERGE (p)-[:MEMBER_OF]->(t)"),
    ("reports_to", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (m:Person {handle: r.manager}) MERGE (p)-[:REPORTS_TO]->(m)"),
    (
        "repos",
        "UNWIND $rows AS r MERGE (s:System {key: r.key}) "
        "SET s.name = r.name, s.kind = 'repo', s.repo = r.key, s.path = '', s.description = r.description, "
        "s.url = r.url, s.language = r.language, s.stars = r.stars, s.aliases = coalesce(r.aliases, [])",
    ),
    (
        "modules",
        "UNWIND $rows AS r MERGE (m:System {key: r.key}) SET m.name = r.name, m.kind = r.kind, m.repo = r.repo, m.path = r.path "
        "WITH m, r MATCH (repo:System {key: r.repo}) MERGE (m)-[:PART_OF]->(repo)",
    ),
    (
        "sources",
        "UNWIND $rows AS r MERGE (s:Source {uid: r.uid}) " + _SOURCE_SET + ", s.merged_ts = r.merged_ts "
        "WITH s, r MATCH (repo:System {key: r.repo}) MERGE (s)-[:IN_REPO]->(repo)",
    ),
    (
        "children",
        "UNWIND $rows AS r MERGE (s:Source {uid: r.uid}) " + _SOURCE_SET + "WITH s, r MATCH (p:Source {uid: r.parent}) MERGE (s)-[:REPLY_TO]->(p)",
    ),
    ("authored", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (s:Source {uid: r.uid}) MERGE (p)-[:AUTHORED]->(s)"),
    (
        "reviewed",
        "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (s:Source {uid: r.uid}) MERGE (p)-[e:REVIEWED]->(s) SET e.state = r.state, e.ts = r.ts",
    ),
    ("merged", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (s:Source {uid: r.uid}) MERGE (p)-[e:MERGED]->(s) SET e.ts = r.ts"),
    ("assigned", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (s:Source {uid: r.uid}) MERGE (p)-[:ASSIGNED]->(s)"),
    (
        "touches",
        "UNWIND $rows AS r MATCH (s:Source {uid: r.uid}), (m:System {key: r.key}) "
        "MERGE (s)-[e:TOUCHES]->(m) SET e.additions = r.additions, e.deletions = r.deletions, e.files = r.files",
    ),
    ("closes", "UNWIND $rows AS r MATCH (a:Source {uid: r.src}), (b:Source {uid: r.dst}) MERGE (a)-[:CLOSES]->(b)"),
    ("references", "UNWIND $rows AS r MATCH (a:Source {uid: r.src}), (b:Source {uid: r.dst}) MERGE (a)-[:REFERENCES]->(b)"),
    ("owns", "UNWIND $rows AS r MATCH (p:Person {handle: r.handle}), (s:System {key: r.key}) MERGE (p)-[e:OWNS]->(s) SET e.via = r.via"),
    ("depends", "UNWIND $rows AS r MATCH (a:System {key: r.src}), (b:System {key: r.dst}) MERGE (a)-[e:DEPENDS_ON]->(b) SET e.via = r.via"),
    ("team_owns", "UNWIND $rows AS r MATCH (t:Team {key: r.team}), (s:System {key: r.key}) MERGE (t)-[:OWNS]->(s)"),
    ("mentions", "UNWIND $rows AS r MATCH (s:Source {uid: r.uid}), (m:System {key: r.key}) MERGE (s)-[:MENTIONS]->(m)"),
]


def write_rows(store: GraphStore, rows: Rows) -> dict[str, int]:
    store.ensure_schema()
    written: dict[str, int] = {}
    for name, cypher in WRITES:
        data = getattr(rows, name)
        items = list(data.values()) if isinstance(data, dict) else data
        written[name] = store.write_batches(cypher, items)
        log.info("wrote %-10s %6d", name, written[name])
    return written
