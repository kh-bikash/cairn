"""Export the graph as an OKF (Open Knowledge Format) bundle: a cross-linked markdown wiki.

Layout
    index.md                         overview (type: index)
    systems/index.md                 all repos
    systems/<repo>/index.md          a repo (type: system)
    systems/<repo>/<module>.md       a module (type: module)
    decisions/index.md               all decisions, newest first
    decisions/<id>.md                a decision with premises, quotes, drift (type: decision)
    people/index.md, people/<h>.md   expertise profiles (type: person)
    log.md                           chronological decision + drift history

The bundle is deterministic: identical graph -> identical bytes, so a wiki PR only shows real changes.
People are referenced as `handle` code spans, never @mentions, so publishing never pings anyone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cairn.agent.tools import Tools, fmt_date
from cairn.graph.client import GraphStore, SubGraph

OKF_VERSION = "0.1"
_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_PEOPLE = 40


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-") or "x"


def repo_dir(repo: str) -> str:
    return f"systems/{slug(repo.split('/')[-1])}"


def module_file(module_key: str) -> str:
    repo, _, path = module_key.partition(":")
    return f"{repo_dir(repo)}/{slug(path)}.md"


def decision_file(uid: str) -> str:
    # 'FalkorDB/falkordb-py#303::d1' -> decisions/falkordb-py-303-d1.md (stable across re-extraction)
    return f"decisions/{slug(uid.split('/', 1)[-1].replace('::', '-'))}.md"


def person_file(handle: str) -> str:
    return f"people/{slug(handle)}.md"


def rel(from_file: str, to_file: str) -> str:
    """Relative link between two bundle files (OKF links are ordinary markdown links)."""
    src = PurePosixPath(from_file).parent
    parts_from = [p for p in src.parts if p != "."]
    parts_to = list(PurePosixPath(to_file).parts)
    common = 0
    while common < min(len(parts_from), len(parts_to)) and parts_from[common] == parts_to[common]:
        common += 1
    up = [".."] * (len(parts_from) - common)
    return "/".join(up + parts_to[common:]) or PurePosixPath(to_file).name


def frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == [] or value == "":
            continue
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(_yaml_scalar(v) for v in value)}]")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def _quote(text: str | None) -> str:
    return (text or "").replace("\n", " ").strip()


@dataclass
class Bundle:
    files: dict[str, str]

    def write(self, root: Path) -> int:
        for path, content in self.files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        return len(self.files)


class Exporter:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.tools = Tools(store)
        self.files: dict[str, str] = {}

    def build(self) -> Bundle:
        repos = self.store.records(
            "MATCH (r:System {kind: 'repo'}) RETURN r.key AS key, r.name AS name, r.description AS description, r.url AS url, r.language AS language ORDER BY key"
        )
        decisions = [r["uid"] for r in self.store.records("MATCH (d:Decision) RETURN d.uid AS uid, d.ts AS ts ORDER BY ts DESC, uid")]
        details = {uid: self.tools._decision_detail(uid, SubGraph(), []) for uid in decisions}
        people = self._people()

        for r in repos:
            self._repo_page(r, details)
        for d in details.values():
            self._decision_page(d)
        for handle in people:
            self._person_page(handle)
        self._indexes(repos, details, people)
        self._log(details)
        return Bundle(dict(sorted(self.files.items())))

    # ------------------------------------------------------------------ pages
    def _repo_page(self, repo: dict[str, Any], details: dict[str, dict[str, Any]]) -> None:
        path = f"{repo_dir(repo['key'])}/index.md"
        deps = self.store.records("MATCH (:System {key: $k})-[e:DEPENDS_ON]->(d:System) RETURN d.key AS key, e.via AS via ORDER BY key", {"k": repo["key"]})
        dependents = self.store.records("MATCH (d:System)-[e:DEPENDS_ON]->(:System {key: $k}) RETURN d.key AS key, e.via AS via ORDER BY key", {"k": repo["key"]})
        experts = self._experts(repo["key"], 5)
        modules = self.store.records(
            """
            MATCH (m:System)-[:PART_OF]->(:System {key: $k}) WHERE m.kind = 'code'
            OPTIONAL MATCH (x:Source)-[:TOUCHES]->(m)
            RETURN m.key AS key, m.path AS path, count(x) AS prs ORDER BY prs DESC, key
            """,
            {"k": repo["key"]},
        )
        modules = [m for m in modules if m["prs"] >= 3]
        body = [
            frontmatter(
                {
                    "type": "system",
                    "title": repo["name"],
                    "description": repo["description"],
                    "resource": repo["url"],
                    "tags": ["repo", *([slug(repo["language"])] if repo.get("language") else [])],
                }
            ),
            f"# {repo['name']}",
            "",
            repo["description"] or "",
            "",
            "## Depends on",
            *([f"- [{d['key']}]({rel(path, f'{repo_dir(d["key"])}/index.md')}) via `{d['via']}`" for d in deps] or ["- nothing in this knowledge base"]),
            "",
            "## Depended on by",
            *([f"- [{d['key']}]({rel(path, f'{repo_dir(d["key"])}/index.md')}) via `{d['via']}`" for d in dependents] or ["- nothing in this knowledge base"]),
            "",
            "## Who knows it",
            *self._expert_lines(path, experts),
            "",
            "## Modules",
            *[f"- [{m['path']}]({rel(path, module_file(m['key']))}): {m['prs']} PRs" for m in modules],
        ]
        repo_decisions = [d for d in details.values() if repo["key"] in d.get("systems", []) or d["decided_in"].startswith(repo["key"] + "#")]
        if repo_decisions:
            body += ["", "## Decisions", *[self._decision_line(path, d) for d in repo_decisions]]
        self.files[path] = "\n".join(body).rstrip() + "\n"
        for m in modules:
            self._module_page(m, repo, details)

    def _module_page(self, module: dict[str, Any], repo: dict[str, Any], details: dict[str, dict[str, Any]]) -> None:
        path = module_file(module["key"])
        experts = self._experts(module["key"], 5)
        recent = self.store.records(
            """
            MATCH (s:Source)-[:TOUCHES]->(:System {key: $k}) WHERE s.state = 'merged'
            RETURN s.uid AS uid, s.title AS title, s.url AS url, s.ts AS ts, s.author AS author ORDER BY ts DESC LIMIT 8
            """,
            {"k": module["key"]},
        )
        mod_decisions = [d for d in details.values() if module["key"] in d.get("systems", [])]
        body = [
            frontmatter(
                {
                    "type": "module",
                    "title": f"{repo['name']}/{module['path']}",
                    "description": f"Module {module['path']} of {repo['name']}",
                    "resource": f"{repo['url']}/tree/HEAD/{module['path']}",
                    "tags": ["module", slug(repo["name"])],
                }
            ),
            f"# {module['path']}",
            "",
            f"Part of [{repo['name']}]({rel(path, f'{repo_dir(repo["key"])}/index.md')}). {module['prs']} PRs touched it.",
            "",
            "## Who knows it",
            *self._expert_lines(path, experts),
            "",
            "## Recent merged changes",
            *[f"- {fmt_date(r['ts'])}: [{r['title']}]({r['url']}) by `{r['author']}`" for r in recent],
        ]
        if mod_decisions:
            body += ["", "## Decisions", *[self._decision_line(path, d) for d in mod_decisions]]
        self.files[path] = "\n".join(body).rstrip() + "\n"

    def _decision_page(self, d: dict[str, Any]) -> None:
        path = decision_file(d["uid"])
        thread = self.store.records("MATCH (s:Source {uid: $u}) RETURN s.url AS url, s.title AS title", {"u": d["decided_in"]})
        url = thread[0]["url"] if thread else None
        status = "superseded" if d["superseded_by"] else ("challenged" if d["drift_detected"] else d["status"])
        body = [
            frontmatter(
                {
                    "type": "decision",
                    "title": d["title"],
                    "description": d["summary"],
                    "timestamp": d["date"],
                    "resource": url,
                    "status": status,
                    "category": d["category"],
                    "grounding": d["grounding"],
                    "tags": ["decision", d["category"]],
                }
            ),
            f"# {d['title']}",
            "",
        ]
        if d["drift_detected"]:
            body += ["> **Drift warning:** later evidence challenges at least one reason behind this decision. Re-validate before relying on it.", ""]
        body += [
            d["summary"],
            "",
            f"Decided {d['date']} in [{d['decided_in']}]({url}) by {', '.join(f'[`{h}`]({rel(path, person_file(h))})' for h in d['deciders']) or 'unknown'}.",
            "",
            "## Why",
        ]
        for p in d["premises"]:
            body.append(f"- {p['claim']}")
            body.append(f"  > {_quote(p['quote'])} ([{p['source']}]({self._url(p['source'])}))")
            for c in p["challenged_by"]:
                body.append(f"  - **Challenged {c['date']}** ({c['verdict']}): {_quote(c['why'])}")
                body.append(f"    > {_quote(c['quote'])} ([{c['source']}]({self._url(c['source'])}))")
        if d["rejected_alternatives"]:
            body += ["", "## Alternatives rejected"]
            for r in d["rejected_alternatives"]:
                body.append(f"- **{r['option']}**: {r['claim']}")
                body.append(f"  > {_quote(r['quote'])} ([{r['source']}]({self._url(r['source'])}))")
        if d["systems"]:
            links = []
            for key in d["systems"]:
                target = f"{repo_dir(key)}/index.md" if ":" not in key else module_file(key)
                links.append(f"[{key}]({rel(path, target)})" if target in self.files or ":" not in key else f"`{key}`")
            body += ["", "## Affects", *[f"- {link}" for link in links]]
        if d["superseded_by"]:
            body += ["", "## Superseded by", *[f"- [{s['title']}]({rel(path, decision_file(s['uid']))})" for s in d["superseded_by"]]]
        self.files[path] = "\n".join(body).rstrip() + "\n"

    def _person_page(self, handle: str) -> None:
        path = person_file(handle)
        profile = self.tools.person_profile(handle).data
        person = profile["person"]
        body = [
            frontmatter(
                {"type": "person", "title": handle, "description": person.get("name") or handle, "resource": f"https://github.com/{handle}", "tags": ["person"]}
            ),
            f"# `{handle}`" + (f" ({person['name']})" if person.get("name") else ""),
            "",
            "## Expertise",
            *[
                f"- {self._system_link(path, e['system'])}: {int(e['share_of_knowledge'] * 100)}% of recent activity, last active {e['last_active']}"
                for e in profile["expertise"]
            ],
        ]
        if profile["would_leave_gap_in"]:
            body += [
                "",
                "## Knowledge risk",
                "Holds at least half of the recent activity in:",
                *[f"- {self._system_link(path, k)}" for k in profile["would_leave_gap_in"]],
            ]
        if profile["decisions"]:
            body += ["", "## Decisions", *[f"- [{d['title']}]({rel(path, decision_file(d['uid']))}) ({d['date']})" for d in profile["decisions"]]]
        self.files[path] = "\n".join(body).rstrip() + "\n"

    def _indexes(self, repos: list[dict[str, Any]], details: dict[str, dict[str, Any]], people: list[str]) -> None:
        drifted = [d for d in details.values() if d["drift_detected"]]
        self.files["index.md"] = (
            "\n".join(
                [
                    frontmatter(
                        {"type": "index", "title": "Cairn knowledge base", "description": "Company brain exported from the Cairn graph", "okf_version": OKF_VERSION}
                    ),
                    "# Cairn knowledge base",
                    "",
                    "Generated from the Cairn graph. Every claim links to the PR, issue or comment it came from.",
                    "",
                    f"- [Systems](systems/index.md): {len(repos)} repositories",
                    f"- [Decisions](decisions/index.md): {len(details)} recorded, {len(drifted)} with drift warnings",
                    f"- [People](people/index.md): {len(people)} profiles",
                    "- [Log](log.md): decision and drift history",
                ]
            )
            + "\n"
        )
        self.files["systems/index.md"] = (
            "\n".join(
                [
                    frontmatter({"type": "index", "title": "Systems"}),
                    "# Systems",
                    "",
                    *[f"- [{r['name']}]({rel('systems/index.md', f'{repo_dir(r["key"])}/index.md')}): {r['description'] or ''}".rstrip(": ") for r in repos],
                ]
            )
            + "\n"
        )
        self.files["decisions/index.md"] = (
            "\n".join(
                [frontmatter({"type": "index", "title": "Decisions"}), "# Decisions", "", *[self._decision_line("decisions/index.md", d) for d in details.values()]]
                if details
                else [frontmatter({"type": "index", "title": "Decisions"}), "# Decisions", "", "No decisions extracted yet."]
            )
            + "\n"
        )
        self.files["people/index.md"] = (
            "\n".join([frontmatter({"type": "index", "title": "People"}), "# People", "", *[f"- [`{h}`]({rel('people/index.md', person_file(h))})" for h in people]])
            + "\n"
        )

    def _log(self, details: dict[str, dict[str, Any]]) -> None:
        events: list[tuple[str, str]] = []
        for d in details.values():
            events.append((d["date"] or "", f"- {d['date']}: decided [{d['title']}]({decision_file(d['uid'])})"))
            for p in d["premises"]:
                for c in p["challenged_by"]:
                    events.append(
                        (
                            c["date"] or "",
                            f"- {c['date']}: [{c['source']}]({self._url(c['source'])}) challenged a reason behind [{d['title']}]({decision_file(d['uid'])})",
                        )
                    )
        lines = [frontmatter({"type": "log", "title": "Decision log"}), "# Decision log", ""]
        lines += [line for _, line in sorted(events, key=lambda e: e[0], reverse=True)] or ["No decisions extracted yet."]
        self.files["log.md"] = "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ helpers
    def _people(self) -> list[str]:
        rows = self.store.records(
            "MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System) WHERE s.kind = 'repo' RETURN p.handle AS h, sum(k.score) AS total ORDER BY total DESC, h LIMIT $n",
            {"n": _MAX_PEOPLE},
        )
        deciders = [r["h"] for r in self.store.records("MATCH (p:Person)-[:DECIDED]->(:Decision) RETURN DISTINCT p.handle AS h ORDER BY h")]
        return sorted(set(_unique([r["h"] for r in rows] + deciders)), key=str.lower)

    def _experts(self, key: str, k: int) -> list[dict[str, Any]]:
        return self.store.records(
            "MATCH (p:Person)-[e:KNOWS_ABOUT]->(:System {key: $key}) RETURN p.handle AS handle, e.score AS score, e.authored AS authored, "
            "e.reviewed AS reviewed, e.last_ts AS last_ts ORDER BY score DESC, handle LIMIT $k",
            {"key": key, "k": k},
        )

    def _expert_lines(self, path: str, experts: list[dict[str, Any]]) -> list[str]:
        total = sum(e["score"] for e in experts) or 1
        return [
            f"- [`{e['handle']}`]({rel(path, person_file(e['handle']))}): {int(100 * e['score'] / total)}%, "
            f"authored {e['authored']} / reviewed {e['reviewed']} PRs, last active {fmt_date(e['last_ts'])}"
            for e in experts
        ] or ["- no recorded activity"]

    def _decision_line(self, path: str, d: dict[str, Any]) -> str:
        flag = " ⚠ drift" if d["drift_detected"] else ""
        return f"- {d['date']}: [{d['title']}]({rel(path, decision_file(d['uid']))}){flag}"

    def _system_link(self, path: str, key: str) -> str:
        target = f"{repo_dir(key)}/index.md" if ":" not in key else module_file(key)
        return f"[{key}]({rel(path, target)})" if target in self.files else f"`{key}`"

    def _url(self, uid: str) -> str:
        rows = self.store.records("MATCH (s:Source {uid: $u}) RETURN s.url AS url", {"u": uid})
        return rows[0]["url"] if rows and rows[0]["url"] else ""


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def export_bundle(store: GraphStore) -> Bundle:
    return Exporter(store).build()
