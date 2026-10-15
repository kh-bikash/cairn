from __future__ import annotations

from cairn.ingest import systems as sysmap
from cairn.ingest.loader import build_rows
from cairn.ingest.text import child_uid, extract_refs, is_bot, is_design_doc, to_ms
from tests.fixtures import ALICE, CODERABBIT, DEPENDABOT, api_snapshot, client_snapshot


def test_bot_detection() -> None:
    assert is_bot(DEPENDABOT)
    assert is_bot(CODERABBIT)
    assert is_bot({"__typename": "User", "login": "copilot-swe-agent"})  # known AI agent reported as a User
    assert is_bot({"login": "renovate[bot]"})
    assert is_bot(None)  # deleted account
    assert not is_bot(ALICE)


def test_extract_refs_resolves_short_and_cross_repo_and_urls() -> None:
    text = "Fixes #12, see acme/client#3 and https://github.com/acme/web/pull/7. Not a ref: abc#4 or &#39;"
    assert extract_refs(text, "acme/api") == {"acme/api#12", "acme/client#3", "acme/web#7"}


def test_child_uid_uses_url_fragment() -> None:
    assert child_uid("acme/api#10", "https://github.com/acme/api/pull/10#issuecomment-55", 0, "comment") == "acme/api#10/issuecomment-55"
    assert child_uid("acme/api#10", None, 3, "review") == "acme/api#10/review-3"


def test_to_ms() -> None:
    assert to_ms("1970-01-01T00:00:01Z") == 1000
    assert to_ms(None) is None


def test_module_path_strips_layout_dirs_and_collapses_repeats() -> None:
    assert sysmap.module_path("graphrag_sdk/src/graphrag_sdk/ingestion/loader.py") == "graphrag_sdk/ingestion"
    assert sysmap.module_path("app/src/components/ui/button.tsx") == "app/components"
    assert sysmap.module_path("src/services/x.ts") == "services"
    assert sysmap.module_path("README.md") is None


def test_module_kind() -> None:
    assert sysmap.module_kind("tests/integration") == "tests"
    assert sysmap.module_kind("graphrag_sdk/tests") == "tests"
    assert sysmap.module_kind(".github/workflows") == "tooling"
    assert sysmap.module_kind("examples/data") == "docs"
    assert sysmap.module_kind("app/public") == "assets"
    assert sysmap.module_kind("api/routes") == "code"


def test_design_docs_are_decision_records_only() -> None:
    for path in ("docs/adr/0003-kafka.md", "rfcs/0007-solver.md", "docs/architecture.md", "design-notes.md", "docs/MCP_SERVER_DESIGN.md"):
        assert is_design_doc(path), path
    for path in ("README.md", "CONTRIBUTING.md", "SECURITY.md", ".github/PULL_REQUEST_TEMPLATE.md", "docs/address-book.md"):
        assert not is_design_doc(path), path


def test_codeowners_parsing_skips_teams() -> None:
    rules = sysmap.parse_codeowners("# comment\n* @alice @acme/core\n/src/billing/ @bob\n")
    assert rules == [sysmap.OwnerRule("*", ("alice",)), sysmap.OwnerRule("/src/billing/", ("bob",))]
    assert sysmap.rule_matches_module("/src/billing/", "billing")
    assert not sysmap.rule_matches_module("/src/billing/", "cache")


def test_manifests_namespace_by_ecosystem() -> None:
    py = sysmap.parse_manifests([{"path": "pkg/pyproject.toml", "text": '[project]\nname = "falkordb"\ndependencies = ["redis>=5"]'}])
    js = sysmap.parse_manifests([{"path": "package.json", "text": '{"name": "app", "dependencies": {"falkordb": "^6"}}'}])
    assert py.provides == {"pypi:falkordb"}
    assert "npm:falkordb" in js.requires
    # An npm package of the same name must not create a dependency on the PyPI publisher.
    assert sysmap.repo_dependencies({"py": py, "js": js}) == []


def test_build_rows_end_to_end() -> None:
    rows = build_rows([api_snapshot(), client_snapshot()])
    uids = {s["uid"] for s in rows.sources}
    assert "acme/api#13" not in uids, "dependency-bot PRs are skipped"
    assert rows.skipped_bot_prs == 1
    assert {"acme/api#10", "acme/api#5", "acme/client#3", "acme/api:docs/adr/0001-cache.md"} <= uids
    assert set(rows.people) == {"alice", "bob", "carol"}, "bots never become people"
    # One TOUCHES row per (PR, module), with churn summed across files.
    cache_touch = [t for t in rows.touches if t["uid"] == "acme/api#10"]
    assert cache_touch == [{"uid": "acme/api#10", "key": "acme/api:api/cache", "additions": 80, "deletions": 10, "files": 2}]
    # Bot reviews are dropped; human review bodies become child sources.
    assert [c["uid"] for c in rows.children if c["kind"] == "review"] == ["acme/api#10/pullrequestreview-1"]
    assert {"src": "acme/api#10", "dst": "acme/api#5"} in rows.closes
    assert {"src": "acme/api#10", "dst": "acme/api#5"} in rows.references
    assert rows.depends == [{"src": "acme/api", "dst": "acme/client", "via": "pypi:acme-client"}]
    owners = {(o["handle"], o["key"]) for o in rows.owns}
    assert ("alice", "acme/api") in owners and ("bob", "acme/api:api/billing") in owners
