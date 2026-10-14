"""Pure helpers for identities, timestamps and cross-references. No I/O."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# Accounts that are bots even when GraphQL reports them as a User (or omits __typename).
KNOWN_BOTS = frozenset(
    {
        "dependabot",
        "renovate",
        "codecov",
        "coderabbitai",
        "copilot-pull-request-reviewer",
        "copilot-swe-agent",
        "copilot",
        "github-actions",
        "anthropic-code-agent",
        "sonarcloud",
        "netlify",
        "vercel",
        "changeset-bot",
        "pre-commit-ci",
        "gitguardian",
        "deepsource-autofix",
        "socket-security",
    }
)

# PRs from these authors are pure dependency churn and are skipped entirely.
DEPENDENCY_BOTS = frozenset({"dependabot", "renovate"})


def is_bot(actor: dict[str, Any] | None) -> bool:
    if not actor:
        return True  # deleted ("ghost") accounts carry no attributable knowledge
    login = (actor.get("login") or "").lower()
    if actor.get("__typename") == "Bot" or login.endswith("[bot]"):
        return True
    return login.removesuffix("[bot]") in KNOWN_BOTS


def login_of(actor: dict[str, Any] | None) -> str | None:
    if not actor or not actor.get("login"):
        return None
    return str(actor["login"]).removesuffix("[bot]")


def to_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def top_uid(repo: str, number: int) -> str:
    """Issues, PRs and discussions share one number space per repo, so repo#n is unique."""
    return f"{repo}#{number}"


_FRAGMENT = re.compile(r"#(issuecomment|pullrequestreview|discussioncomment|discussion_r|r)-?(\d+)$")


def child_uid(parent_uid: str, url: str | None, index: int, kind: str) -> str:
    if url:
        m = _FRAGMENT.search(url)
        if m:
            return f"{parent_uid}/{m.group(1)}-{m.group(2)}"
    return f"{parent_uid}/{kind}-{index}"


def doc_uid(repo: str, path: str) -> str:
    return f"{repo}:{path}"


# `#123`, `owner/repo#123`, or full GitHub URLs to issues/PRs/discussions.
_SHORT_REF = re.compile(r"(?<![\w/&])(?:([\w.-]+/[\w.-]+))?#(\d{1,6})\b")
_URL_REF = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull|discussions)/(\d+)", re.I)


def extract_refs(text: str | None, repo: str) -> set[str]:
    """Return uids referenced from `text`, resolving bare `#n` against `repo`."""
    if not text:
        return set()
    refs: set[str] = set()
    for m in _URL_REF.finditer(text):
        refs.add(top_uid(m.group(1), int(m.group(2))))
    for m in _SHORT_REF.finditer(text):
        refs.add(top_uid(m.group(1) or repo, int(m.group(2))))
    return refs


def clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def clean_body(text: str | None) -> str:
    """Strip PR-template HTML comments, which are boilerplate rather than content."""
    if not text:
        return ""
    return _HTML_COMMENT.sub("", text).strip()
