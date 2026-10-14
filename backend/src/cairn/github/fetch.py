"""Fetch a GitHub repository into an on-disk snapshot.

Snapshots decouple the network from graph building: loading is deterministic and works offline,
and the demo can be pinned to a frozen snapshot.

Layout: data/raw/<owner>__<repo>/{meta.json, prs.jsonl, issues.jsonl, discussions.jsonl, docs.jsonl}
"""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cairn.github import queries
from cairn.github.client import GitHubClient

log = logging.getLogger(__name__)

# Markdown files that tend to carry rationale: design docs, ADRs, RFCs, architecture notes.
_DOC_HINT = re.compile(r"(adr|decision|rfc|design|architecture|proposal|roadmap|contributing|changelog)", re.I)
_MAX_DOCS = 30
_MAX_DOC_BYTES = 80_000
MANIFEST_NAMES = frozenset({"pyproject.toml", "package.json", "requirements.txt", "Cargo.toml", "go.mod", "pom.xml"})
_MANIFEST_MAX_DEPTH = 2  # e.g. graphrag_sdk/pyproject.toml, packages/x/package.json


@dataclass
class FetchStats:
    repo: str
    prs: int = 0
    issues: int = 0
    discussions: int = 0
    docs: int = 0
    manifests: int = 0


def snapshot_dir(raw_dir: Path, repo: str) -> Path:
    owner, name = repo.split("/", 1)
    return raw_dir / f"{owner}__{name}"


def fetch_repo(gh: GitHubClient, repo: str, raw_dir: Path, *, since: str, max_items: int) -> FetchStats:
    owner, name = repo.split("/", 1)
    out = snapshot_dir(raw_dir, repo)
    out.mkdir(parents=True, exist_ok=True)
    stats = FetchStats(repo=repo)
    variables = {"owner": owner, "name": name}

    meta = gh.graphql(queries.REPO_META, variables)["repository"]
    meta["fetched_at"] = datetime.now(UTC).isoformat()
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    stats.prs = _write_jsonl(out / "prs.jsonl", _paginate(gh, queries.PULL_REQUESTS, variables, "pullRequests", since, max_items))
    stats.issues = _write_jsonl(out / "issues.jsonl", _paginate(gh, queries.ISSUES, variables, "issues", since, max_items))
    stats.discussions = _write_jsonl(out / "discussions.jsonl", _paginate(gh, queries.DISCUSSIONS, variables, "discussions", since, max_items))
    branch = (meta.get("defaultBranchRef") or {}).get("name") or "main"
    tree = _fetch_tree(gh, owner, name, branch)
    stats.docs = _write_jsonl(out / "docs.jsonl", _fetch_docs(gh, owner, name, branch, tree))
    stats.manifests = _write_jsonl(out / "manifests.jsonl", _fetch_manifests(gh, owner, name, tree))
    log.info("fetched %s: %s", repo, stats)
    return stats


def _paginate(gh: GitHubClient, query: str, variables: dict[str, Any], field: str, since: str, max_items: int) -> Iterator[dict[str, Any]]:
    cursor: str | None = None
    emitted = 0
    while True:
        data = gh.graphql(query, {**variables, "cursor": cursor})
        conn = (data.get("repository") or {}).get(field)
        if conn is None:  # e.g. discussions disabled on the repo
            return
        for node in conn["nodes"]:
            # Ordered newest-first, so the first item older than `since` ends the walk.
            if node["createdAt"][:10] < since:
                return
            yield node
            emitted += 1
            if emitted >= max_items:
                return
        if not conn["pageInfo"]["hasNextPage"]:
            return
        cursor = conn["pageInfo"]["endCursor"]


def _fetch_tree(gh: GitHubClient, owner: str, name: str, branch: str) -> list[dict[str, Any]]:
    try:
        tree = gh.rest("GET", f"/repos/{owner}/{name}/git/trees/{branch}", params={"recursive": "1"})
    except httpx.HTTPStatusError as exc:
        log.warning("no tree for %s/%s: %s", owner, name, exc)
        return []
    return [t for t in tree.get("tree", []) if t["type"] == "blob"]


def _blob_text(gh: GitHubClient, owner: str, name: str, sha: str) -> str | None:
    try:
        blob = gh.rest("GET", f"/repos/{owner}/{name}/git/blobs/{sha}")
    except httpx.HTTPStatusError as exc:
        log.warning("skip blob %s: %s", sha, exc)
        return None
    return base64.b64decode(blob["content"]).decode("utf-8", errors="replace")


def _fetch_manifests(gh: GitHubClient, owner: str, name: str, tree: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for item in tree:
        path: str = item["path"]
        wanted = path.rsplit("/", 1)[-1] in MANIFEST_NAMES and path.count("/") < _MANIFEST_MAX_DEPTH and "node_modules" not in path
        if wanted and (text := _blob_text(gh, owner, name, item["sha"])) is not None:
            yield {"path": path, "text": text}


def _fetch_docs(gh: GitHubClient, owner: str, name: str, branch: str, tree: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    candidates = [t for t in tree if t["path"].lower().endswith(".md") and t.get("size", 0) <= _MAX_DOC_BYTES]
    candidates.sort(key=_doc_priority)
    for item in candidates[:_MAX_DOCS]:
        if (text := _blob_text(gh, owner, name, item["sha"])) is None:
            continue
        yield {
            "path": item["path"],
            "sha": item["sha"],
            "url": f"https://github.com/{owner}/{name}/blob/{branch}/{item['path']}",
            "text": text,
        }


def _doc_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    path: str = item["path"]
    is_root_readme = path.lower() == "readme.md"
    hinted = bool(_DOC_HINT.search(path))
    in_docs = path.lower().startswith(("docs/", "doc/", "design/", "adr/"))
    rank = 0 if is_root_readme else 1 if hinted else 2 if in_docs else 3
    return (rank, path.count("/"), path)


def _write_jsonl(path: Path, items: Iterator[dict[str, Any]]) -> int:
    n = 0
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(path)  # atomic: a crashed fetch never leaves a half-written snapshot
    return n


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_snapshot(raw_dir: Path, repo: str) -> dict[str, Any]:
    d = snapshot_dir(raw_dir, repo)
    if not (d / "meta.json").exists():
        raise FileNotFoundError(f"no snapshot for {repo} in {d}; run `cairn fetch` first")
    read: Callable[[str], list[dict[str, Any]]] = lambda n: read_jsonl(d / f"{n}.jsonl")  # noqa: E731
    return {
        "repo": repo,
        "meta": json.loads((d / "meta.json").read_text(encoding="utf-8")),
        "prs": read("prs"),
        "issues": read("issues"),
        "discussions": read("discussions"),
        "docs": read("docs"),
        "manifests": read("manifests"),
    }
