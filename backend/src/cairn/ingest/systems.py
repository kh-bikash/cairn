"""Map repository structure to `System` nodes: modules, ownership and dependencies."""

from __future__ import annotations

import fnmatch
import json
import re
import tomllib
from dataclasses import dataclass, field
from typing import Any

# Layout-only directories that carry no meaning of their own.
_WRAPPERS = frozenset({"src", "lib"})
_MODULE_DEPTH = 2
_TEST_DIRS = frozenset({"test", "tests", "__tests__", "spec", "e2e", "testing"})
_TOOLING_DIRS = frozenset({".github", ".circleci", "ci", "scripts", ".devcontainer", "docker", ".husky"})
_DOC_DIRS = frozenset({"docs", "doc", "examples", "example", "samples"})
_ASSET_DIRS = frozenset({"assets", "static", "public", "images", "img", "media", "fonts", "css"})


def module_path(file_path: str) -> str | None:
    """Collapse a file path to the module it belongs to; root-level files belong to the repo only.

    >>> module_path("graphrag_sdk/src/graphrag_sdk/ingestion/loader.py")
    'graphrag_sdk/ingestion'
    >>> module_path("README.md") is None
    True
    """
    dirs = [d for d in file_path.strip("/").split("/")[:-1] if d not in _WRAPPERS]
    if not dirs:
        return None
    collapsed = [d for i, d in enumerate(dirs) if i == 0 or d != dirs[i - 1]]
    return "/".join(collapsed[:_MODULE_DEPTH])


def module_key(repo: str, path: str) -> str:
    return f"{repo}:{path}"


def module_kind(path: str) -> str:
    segments = {seg.lower() for seg in path.split("/")}
    if segments & _TEST_DIRS:
        return "tests"
    if path.split("/", 1)[0].lower() in _TOOLING_DIRS:
        return "tooling"
    if segments & _DOC_DIRS:
        return "docs"
    if segments & _ASSET_DIRS:
        return "assets"
    return "code"


# --------------------------------------------------------------------------- CODEOWNERS


@dataclass(frozen=True)
class OwnerRule:
    pattern: str
    owners: tuple[str, ...]


def parse_codeowners(text: str | None) -> list[OwnerRule]:
    rules: list[OwnerRule] = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        # Team handles (@org/team) can't be resolved to people without org-admin scope.
        users = tuple(o[1:] for o in owners if o.startswith("@") and "/" not in o)
        if users:
            rules.append(OwnerRule(pattern, users))
    return rules


def rule_matches_module(pattern: str, module: str) -> bool:
    p = pattern.strip("/")
    if p in {"*", "**", ""}:
        return False  # repo-wide rules attach to the repo node instead
    p = p.removesuffix("/**").removesuffix("/*")
    for candidate in (module, *(f"{w}/{module}" for w in sorted(_WRAPPERS))):
        if candidate == p or candidate.startswith(p + "/") or p.startswith(candidate + "/") or fnmatch.fnmatch(candidate, p):
            return True
    return False


def is_repo_wide(pattern: str) -> bool:
    return pattern.strip("/") in {"*", "**", ""}


# --------------------------------------------------------------------------- manifests


@dataclass
class Manifest:
    provides: set[str] = field(default_factory=set)
    requires: set[str] = field(default_factory=set)


def normalize_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _req_name(spec: str) -> str | None:
    m = _REQ_NAME.match(spec)
    return f"pypi:{normalize_pkg(m.group(1))}" if m else None


def _pkg(ecosystem: str, name: str) -> str:
    # Same name can exist in several registries (e.g. `falkordb` on PyPI and npm).
    return f"{ecosystem}:{normalize_pkg(name)}"


_FILE_KIND = {
    "pyproject.toml": "pyproject",
    "requirements.txt": "requirements",
    "package.json": "packageJson",
    "Cargo.toml": "cargo",
    "go.mod": "gomod",
    "pom.xml": "pom",
}


def parse_manifests(files: list[dict[str, Any]]) -> Manifest:
    """Read package names a repo publishes and the packages it depends on, across all its manifest files."""
    out = Manifest()
    for f in files:
        kind = _FILE_KIND.get(f["path"].rsplit("/", 1)[-1])
        if kind:
            _parse_one(out, kind, f["text"])
    out.requires -= out.provides
    return out


def _parse_one(out: Manifest, kind: str, content: str) -> None:
    def blob(key: str) -> str | None:
        return content if key == kind else None

    if text := blob("pyproject"):
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project", {})
        poetry = data.get("tool", {}).get("poetry", {})
        for name in (project.get("name"), poetry.get("name")):
            if name:
                out.provides.add(_pkg("pypi", name))
        specs: list[str] = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            specs.extend(group)
        out.requires.update(n for s in specs if (n := _req_name(s)))
        out.requires.update(_pkg("pypi", k) for k in poetry.get("dependencies", {}) if k != "python")

    if text := blob("requirements"):
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith(("#", "-")) and (n := _req_name(line)):
                out.requires.add(n)

    if text := blob("packageJson"):
        try:
            pkg = json.loads(text)
        except json.JSONDecodeError:
            pkg = {}
        if pkg.get("name"):
            out.provides.add(_pkg("npm", pkg["name"]))
        for section in ("dependencies", "peerDependencies", "devDependencies"):
            out.requires.update(_pkg("npm", k) for k in pkg.get(section, {}))

    if text := blob("cargo"):
        try:
            cargo = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            cargo = {}
        if name := cargo.get("package", {}).get("name"):
            out.provides.add(_pkg("cargo", name))
        out.requires.update(_pkg("cargo", k) for k in cargo.get("dependencies", {}))

    if text := blob("gomod"):
        if m := re.search(r"^module\s+(\S+)", text, re.M):
            out.provides.add(_pkg("go", m.group(1)))
        out.requires.update(_pkg("go", m.group(1)) for m in re.finditer(r"^\s*(?:require\s+)?([\w./-]+\.[\w./-]+)\s+v[\d.]", text, re.M))

    if text := blob("pom"):
        if m := re.search(r"<artifactId>([^<]+)</artifactId>", text):
            out.provides.add(_pkg("maven", m.group(1)))
        deps = re.findall(r"<dependency>.*?<artifactId>([^<]+)</artifactId>", text, re.S)
        out.requires.update(_pkg("maven", d) for d in deps)


def repo_dependencies(manifests: dict[str, Manifest]) -> list[tuple[str, str, str]]:
    """Return (dependent_repo, dependency_repo, package) for packages published by an ingested repo."""
    publisher = {pkg: repo for repo, m in manifests.items() for pkg in m.provides}
    edges: list[tuple[str, str, str]] = []
    for repo, m in manifests.items():
        for pkg in sorted(m.requires):
            dep = publisher.get(pkg)
            if dep and dep != repo:
                edges.append((repo, dep, pkg))
    return edges
