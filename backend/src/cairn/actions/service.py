"""Agent actions with a human in the loop.

The agent (or UI) can only *propose* actions; each is stored as a pending `(:Action)` node with a full
preview. Nothing touches GitHub until a person approves it, and execution is refused for any repo
not listed in CAIRN_ACTION_REPOS. Text we publish never contains @mentions, so real people are never pinged.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from cairn.agent.tools import Tools
from cairn.config import Settings
from cairn.github.client import GitHubClient
from cairn.graph.client import GraphStore
from cairn.okf.export import export_bundle

log = logging.getLogger(__name__)

WIKI_ROOT = "wiki"  # bundle lives under this folder in the wiki repo
_MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9][A-Za-z0-9-]*)")


class ActionError(RuntimeError):
    pass


_GH_LINK = re.compile(r"https://github\.com/(?=[\w.-]+/[\w.-]+/(?:issues|pull|discussions)/\d)")


def defang_mentions(text: str) -> str:
    """Make published text inert: @handle -> `handle` (no notifications), and issue/PR links go through
    redirect.github.com (as Dependabot does) so we never add "mentioned this" events to other repos."""
    return _GH_LINK.sub("https://redirect.github.com/", _MENTION.sub(r"`\1`", text))


@dataclass
class Action:
    uid: str
    kind: str
    status: str
    title: str
    repo: str
    preview: str
    payload: dict[str, Any]
    ts: int
    result_url: str | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> Action:
        return cls(
            uid=r["uid"],
            kind=r["kind"],
            status=r["status"],
            title=r["title"],
            repo=r["repo"],
            preview=r["preview"],
            payload=json.loads(r["payload"] or "{}"),
            ts=r["ts"],
            result_url=r.get("result_url"),
            error=r.get("error"),
        )

    def public(self) -> dict[str, Any]:
        out = vars(self).copy()
        out.pop("payload")
        out["files"] = len(self.payload.get("files", {})) if self.kind == "wiki_pr" else None
        return out


class ActionService:
    def __init__(self, store: GraphStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings

    # ------------------------------------------------------------------ proposals
    def propose_wiki_update(self, reason: str = "Refresh the knowledge base from the graph") -> Action:
        repo = self._require_repo(self.settings.wiki_repo)
        bundle = export_bundle(self.store)
        files = {f"{WIKI_ROOT}/{path}": content for path, content in bundle.files.items()}
        decisions = sum(1 for p in bundle.files if p.startswith("decisions/") and p != "decisions/index.md")
        drifted = sum(1 for p, c in bundle.files.items() if p.startswith("decisions/") and "Drift warning" in c)
        body = defang_mentions(
            "\n".join(
                [
                    "Automated knowledge-base update from **Cairn**, the company brain.",
                    "",
                    f"Reason: {reason}",
                    "",
                    f"- {len(bundle.files)} OKF pages ({decisions} decisions, {drifted} with drift warnings)",
                    "- Every claim links to the PR, issue or comment it was extracted from.",
                    "- Format: Open Knowledge Format (OKF) v0.1 bundle under `wiki/`.",
                    "",
                    "Proposed by the Cairn agent and approved by a human before publishing.",
                ]
            )
        )
        preview = f"Open a PR on **{repo}** updating {len(files)} OKF pages under `{WIKI_ROOT}/`.\n\n{body}"
        return self._save("wiki_pr", f"Update knowledge base ({len(files)} pages)", repo, preview, {"files": files, "body": body, "reason": reason})

    def propose_issue_routing(self, repo: str, number: int) -> Action:
        repo = self._require_repo(repo)
        with self._github() as gh:
            issue = gh.rest("GET", f"/repos/{repo}/issues/{number}")
        text = f"{issue['title']}\n{issue.get('body') or ''}"
        suggestion = Tools(self.store).suggest_owners(text)
        matches = [s for s in suggestion.data.get("systems", []) if s["experts"]]
        if not matches:
            raise ActionError("Couldn't map this issue to any known system; add more detail to the issue.")
        systems = [m["system"] for m in matches]
        lines = [
            "**Cairn routing suggestion**",
            "",
            f"This issue most likely touches {', '.join(f'`{s}`' for s in systems)}, based on similar past work.",
            "",
        ]
        for m in matches:
            lines.append(f"Best placed to look at `{m['system']}`:")
            for e in m["experts"]:
                prs = ", ".join(f"[#{p['uid'].split('#')[-1]}]({_pr_url(p['uid'])})" for p in e["recent_prs"][:2])
                lines.append(
                    f"- `{e['handle']}`: {int(e['share'] * 100)}% of recent activity, last active {e['last_active']}" + (f" (recent: {prs})" if prs else "")
                )
            lines.append("")
        uids = [w["uid"] for w in suggestion.data.get("similar_past_work", [])[:3]]
        related = {ev.uid: ev for ev in suggestion.evidence if ev.uid in uids}
        if related:
            lines += ["Similar past work:", *[f"- [{related[u].title}]({related[u].url}) ({related[u].kind})" for u in uids if u in related], ""]
        lines.append("_Suggested by Cairn from the expertise graph. People are named without mentions, so nobody is notified._")
        body = defang_mentions("\n".join(lines))
        preview = f"Comment on **{repo}#{number}** ({issue['title']}):\n\n{body}"
        return self._save("issue_comment", f"Route {repo}#{number}", repo, preview, {"number": number, "body": body, "systems": systems})

    # ------------------------------------------------------------------ lifecycle
    def list(self, limit: int = 30) -> list[Action]:
        rows = self.store.records(
            "MATCH (a:Action) RETURN a.uid AS uid, a.kind AS kind, a.status AS status, a.title AS title, a.repo AS repo, a.preview AS preview, "
            "a.payload AS payload, a.ts AS ts, a.result_url AS result_url, a.error AS error ORDER BY a.ts DESC LIMIT $n",
            {"n": limit},
        )
        return [Action.from_row(r) for r in rows]

    def get(self, uid: str) -> Action:
        rows = self.store.records(
            "MATCH (a:Action {uid: $u}) RETURN a.uid AS uid, a.kind AS kind, a.status AS status, a.title AS title, a.repo AS repo, "
            "a.preview AS preview, a.payload AS payload, a.ts AS ts, a.result_url AS result_url, a.error AS error",
            {"u": uid},
        )
        if not rows:
            raise ActionError(f"no action {uid}")
        return Action.from_row(rows[0])

    def reject(self, uid: str) -> Action:
        action = self.get(uid)
        if action.status != "pending":
            raise ActionError(f"action is {action.status}, not pending")
        self._set(uid, status="rejected")
        return self.get(uid)

    def approve(self, uid: str) -> Action:
        action = self.get(uid)
        if action.status != "pending":
            raise ActionError(f"action is {action.status}, not pending")
        self._require_repo(action.repo)  # re-check: the allow-list may have changed since proposal
        self._set(uid, status="running")
        try:
            with self._github() as gh:
                if action.kind == "wiki_pr":
                    url = open_pull_request(gh, action.repo, action.payload["files"], title=action.title, body=action.payload["body"])
                elif action.kind == "issue_comment":
                    res = gh.rest("POST", f"/repos/{action.repo}/issues/{action.payload['number']}/comments", json={"body": action.payload["body"]})
                    url = res["html_url"]
                else:
                    raise ActionError(f"unknown action kind {action.kind}")
        except (ActionError, httpx.HTTPError) as exc:
            self._set(uid, status="failed", error=str(exc)[:500])
            raise ActionError(str(exc)) from exc
        self._set(uid, status="done" if url else "noop", result_url=url or "")
        return self.get(uid)

    # ------------------------------------------------------------------ internals
    def _require_repo(self, repo: str | None) -> str:
        if not repo or repo not in self.settings.action_repos:
            raise ActionError(f"writes are only allowed to CAIRN_ACTION_REPOS ({', '.join(self.settings.action_repos) or 'none configured'}); got {repo!r}")
        return repo

    def _github(self) -> GitHubClient:
        token = self.settings.resolve_github_token()
        if not token:
            raise ActionError("no GitHub token configured (GITHUB_TOKEN)")
        return GitHubClient(token)

    def _save(self, kind: str, title: str, repo: str, preview: str, payload: dict[str, Any]) -> Action:
        uid = f"action-{uuid.uuid4().hex[:12]}"
        self.store.query(
            "CREATE (:Action {uid: $uid, kind: $kind, status: 'pending', title: $title, repo: $repo, preview: $preview, payload: $payload, ts: $ts})",
            {"uid": uid, "kind": kind, "title": title, "repo": repo, "preview": preview, "payload": json.dumps(payload), "ts": int(time.time() * 1000)},
        )
        return self.get(uid)

    def _set(self, uid: str, **fields: Any) -> None:
        sets = ", ".join(f"a.{k} = ${k}" for k in fields)
        self.store.query(f"MATCH (a:Action {{uid: $uid}}) SET {sets}", {"uid": uid, **fields})


def _pr_url(uid: str) -> str:
    repo, _, number = uid.partition("#")
    return f"https://github.com/{repo}/pull/{number}"


def open_pull_request(gh: GitHubClient, repo: str, files: dict[str, str], *, title: str, body: str) -> str | None:
    """Commit `files` on a new branch and open a PR. Returns the PR URL, or None if nothing changed."""
    meta = gh.rest("GET", f"/repos/{repo}")
    base = meta["default_branch"]
    base_sha = gh.rest("GET", f"/repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
    base_tree = gh.rest("GET", f"/repos/{repo}/git/commits/{base_sha}")["tree"]["sha"]
    # Stale wiki pages (e.g. a decision that no longer exists) are removed by listing them with sha=None.
    existing = gh.rest("GET", f"/repos/{repo}/git/trees/{base_tree}", params={"recursive": "1"}).get("tree", [])
    stale = [t["path"] for t in existing if t["type"] == "blob" and t["path"].startswith(f"{WIKI_ROOT}/") and t["path"] not in files]
    entries: list[dict[str, Any]] = [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in sorted(files.items())]
    entries += [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in stale]
    tree = gh.rest("POST", f"/repos/{repo}/git/trees", json={"base_tree": base_tree, "tree": entries})
    if tree["sha"] == base_tree:
        log.info("wiki already up to date; no PR needed")
        return None
    commit = gh.rest(
        "POST",
        f"/repos/{repo}/git/commits",
        json={"message": f"{title}\n\nGenerated by Cairn.", "tree": tree["sha"], "parents": [base_sha]},
    )
    branch = f"cairn/wiki-{time.strftime('%Y%m%d-%H%M%S')}"
    gh.rest("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    pr = gh.rest("POST", f"/repos/{repo}/pulls", json={"title": title, "head": branch, "base": base, "body": body})
    return str(pr["html_url"])
