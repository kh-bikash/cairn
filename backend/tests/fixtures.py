"""A small synthetic company in GitHub-snapshot shape, shared by unit and integration tests."""

from __future__ import annotations

from typing import Any

ALICE = {"__typename": "User", "login": "alice", "name": "Alice Doe"}
BOB = {"__typename": "User", "login": "bob", "name": "Bob Roe"}
CAROL = {"__typename": "User", "login": "carol", "name": None}
DEPENDABOT = {"__typename": "Bot", "login": "dependabot"}
CODERABBIT = {"__typename": "Bot", "login": "coderabbitai"}

CACHE_BODY = (
    "We decided to use Redis for the session cache instead of Memcached. "
    "Redis supports persistence, which we need for warm restarts after deploys. "
    "Memcached was rejected because it cannot replicate across zones."
)


def _files(*paths: str) -> dict[str, Any]:
    return {"nodes": [{"path": p, "additions": 40, "deletions": 5} for p in paths]}


def _conn(*items: dict[str, Any]) -> dict[str, Any]:
    return {"nodes": list(items)}


def pr(
    number: int,
    title: str,
    author: dict[str, Any],
    *,
    body: str = "",
    files: tuple[str, ...] = (),
    created: str = "2026-03-01T10:00:00Z",
    merged: str | None = "2026-03-02T10:00:00Z",
    merged_by: dict[str, Any] | None = None,
    reviews: tuple[dict[str, Any], ...] = (),
    comments: tuple[dict[str, Any], ...] = (),
    closes: tuple[int, ...] = (),
    repo: str = "acme/api",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{repo}/pull/{number}",
        "state": "MERGED" if merged else "OPEN",
        "createdAt": created,
        "mergedAt": merged,
        "closedAt": merged,
        "author": author,
        "mergedBy": merged_by,
        "labels": _conn(),
        "files": _files(*files),
        "closingIssuesReferences": _conn(*({"number": n} for n in closes)),
        "reviews": _conn(*reviews),
        "comments": _conn(*comments),
    }


def comment(author: dict[str, Any], body: str, *, url: str, created: str = "2026-03-01T12:00:00Z") -> dict[str, Any]:
    return {"author": author, "body": body, "createdAt": created, "url": url}


def review(author: dict[str, Any], state: str = "APPROVED", body: str = "", *, url: str = "", at: str = "2026-03-01T15:00:00Z") -> dict[str, Any]:
    return {"author": author, "state": state, "body": body, "submittedAt": at, "url": url}


def issue(
    number: int,
    title: str,
    author: dict[str, Any],
    *,
    body: str = "",
    state: str = "OPEN",
    created: str = "2026-02-20T09:00:00Z",
    comments: tuple[dict[str, Any], ...] = (),
    repo: str = "acme/api",
) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "url": f"https://github.com/{repo}/issues/{number}",
        "state": state,
        "createdAt": created,
        "closedAt": None,
        "author": author,
        "labels": _conn(),
        "assignees": _conn(BOB),
        "comments": _conn(*comments),
    }


def api_snapshot() -> dict[str, Any]:
    repo = "acme/api"
    return {
        "repo": repo,
        "meta": {
            "name": "api",
            "url": "https://github.com/acme/api",
            "description": "Acme API",
            "stargazerCount": 10,
            "primaryLanguage": {"name": "Python"},
            "fetched_at": "2026-10-01T00:00:00+00:00",
            "codeowners1": {"text": "* @alice\n/src/api/billing/ @bob\n"},
        },
        "prs": [
            pr(
                10,
                "Use Redis for the session cache",
                ALICE,
                body=CACHE_BODY + " See #5.",
                files=("src/api/cache/redis.py", "src/api/cache/__init__.py", "README.md"),
                merged_by=BOB,
                reviews=(
                    review(BOB, body="Agreed, persistence matters for deploys.", url="https://github.com/acme/api/pull/10#pullrequestreview-1"),
                    review(CODERABBIT, body="Summary by CodeRabbit ...", url="https://github.com/acme/api/pull/10#pullrequestreview-2"),
                ),
                comments=(
                    comment(
                        CAROL,
                        "Latency in staging dropped from 40ms to 9ms with Redis.",
                        url="https://github.com/acme/api/pull/10#issuecomment-100",
                    ),
                ),
                closes=(5,),
            ),
            pr(
                11,
                "Billing: idempotent invoices",
                BOB,
                body="Make invoice creation idempotent using request keys.",
                files=("src/api/billing/invoice.py",),
                created="2026-05-01T10:00:00Z",
                merged="2026-05-02T10:00:00Z",
                merged_by=ALICE,
            ),
            pr(
                12,
                "Billing retries",
                BOB,
                body="Add retries.",
                files=("src/api/billing/retry.py",),
                created="2026-06-01T10:00:00Z",
                merged="2026-06-02T10:00:00Z",
                merged_by=ALICE,
            ),
            pr(13, "Bump requests from 2.31 to 2.32", DEPENDABOT, files=("requirements.txt",)),
            pr(
                14,
                "Drop persistence from the session cache",
                CAROL,
                body="Sessions are now stateless JWTs, so the cache no longer needs persistence across deploys.",
                files=("src/api/cache/redis.py",),
                created="2026-09-01T10:00:00Z",
                merged="2026-09-02T10:00:00Z",
                merged_by=ALICE,
                reviews=(review(ALICE, url="https://github.com/acme/api/pull/14#pullrequestreview-3"),),
            ),
        ],
        "issues": [
            issue(
                5,
                "Sessions lost on every deploy",
                CAROL,
                body="Users are logged out after each deploy.",
                state="CLOSED",
                comments=(comment(ALICE, "We need a persistent cache for this.", url="https://github.com/acme/api/issues/5#issuecomment-50"),),
            ),
            issue(20, "Billing webhook flaky", ALICE, body="Webhook times out under load.", created="2026-09-20T09:00:00Z"),
        ],
        "discussions": [],
        "docs": [
            {
                "path": "docs/adr/0001-cache.md",
                "sha": "x",
                "url": "https://github.com/acme/api/blob/main/docs/adr/0001-cache.md",
                "text": "# ADR 1\n" + CACHE_BODY,
            }
        ],
        "manifests": [{"path": "pyproject.toml", "text": '[project]\nname = "acme-api"\ndependencies = ["acme-client>=1.0", "redis>=5"]\n'}],
    }


def client_snapshot() -> dict[str, Any]:
    repo = "acme/client"
    return {
        "repo": repo,
        "meta": {"name": "client", "url": "https://github.com/acme/client", "fetched_at": "2026-10-01T00:00:00+00:00"},
        "prs": [pr(3, "Retry on 503", BOB, body="Retry idempotent calls on 503.", files=("acme_client/http.py",), repo=repo, merged_by=ALICE)],
        "issues": [],
        "discussions": [],
        "docs": [],
        "manifests": [{"path": "pyproject.toml", "text": '[project]\nname = "acme-client"\ndependencies = ["httpx"]\n'}],
    }
