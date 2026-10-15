"""Import a company's own knowledge from standard export formats into the Cairn graph.

A workspace directory can contain any subset of:

    workspace.toml      name, description
    people.csv          handle,name,email,team,role,manager,slack_id,jira_name,github,joined,left
    teams.csv           key,name,description
    systems.csv         key,name,kind,team,depends_on,aliases,description   (lists are ';'-separated)
    slack/              a standard Slack export: users.json, channels.json, <channel>/<YYYY-MM-DD>.json
    tickets.csv         a Jira/Linear CSV export (Issue key, Summary, Description, Status, Assignee, Reporter,
                        Created, Resolved, Component/s, Labels, repeated Comment columns "date;author;text")
    docs/**/*.md        markdown/OKF documents with YAML frontmatter (type, title, date, authors, systems,
                        status, id, supersedes, challenges). ADRs and postmortems are parsed into decisions
                        and drift signals without an LLM.
    github/             optional raw GitHub snapshots (same layout as data/raw)

Everything maps onto the same graph as GitHub data (Person, Team, System, Source, Decision, Premise), so every
agent tool works across chats, tickets, docs and code. People are resolved across tools by email, Slack id,
Jira display name and GitHub login.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from cairn.ingest.loader import Rows
from cairn.ingest.text import clip, rationale_signal

log = logging.getLogger(__name__)

_BODY_LIMIT = 12_000
_DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%d/%b/%y %I:%M %p", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M")


def parse_date(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC).timestamp() * 1000)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return int(datetime.strptime(value, fmt).replace(tzinfo=UTC).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in re.split(r"[;,]", value or "") if v.strip()]


def source_link(uid: str) -> str:
    """Imported sources have no public URL; the app opens them in its own source viewer."""
    return f"#/source/{uid}"


# --------------------------------------------------------------------------- identity


@dataclass
class Directory:
    """Resolves the many identities one person has across tools to a single handle."""

    by_key: dict[str, str] = field(default_factory=dict)

    def add(self, handle: str, *aliases: str | None) -> None:
        for alias in (handle, *aliases):
            if alias:
                self.by_key[alias.strip().lower()] = handle

    def resolve(self, *candidates: str | None) -> str | None:
        for c in candidates:
            if c and (h := self.by_key.get(c.strip().lower().lstrip("@"))):
                return h
        return None


@dataclass
class SystemIndex:
    """Finds mentions of known systems (by key, name or alias) in free text."""

    patterns: list[tuple[re.Pattern[str], str]] = field(default_factory=list)

    def add(self, key: str, *aliases: str) -> None:
        for alias in {key, *aliases}:
            if alias and len(alias) >= 3:
                self.patterns.append((re.compile(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", re.I), key))

    def find(self, text: str) -> list[str]:
        found: list[str] = []
        for pattern, key in self.patterns:
            if key not in found and pattern.search(text):
                found.append(key)
        return found


# --------------------------------------------------------------------------- workspace


@dataclass
class Workspace:
    root: Path
    name: str
    description: str = ""
    rows: Rows = field(default_factory=Rows)
    directory: Directory = field(default_factory=Directory)
    systems: SystemIndex = field(default_factory=SystemIndex)
    decisions: list[dict[str, Any]] = field(default_factory=list)  # parsed from ADR-style docs
    challenges: list[dict[str, Any]] = field(default_factory=list)  # parsed from postmortems
    stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def read_workspace(root: Path) -> Workspace:
    meta: dict[str, Any] = {}
    if (root / "workspace.toml").exists():
        meta = tomllib.loads((root / "workspace.toml").read_text(encoding="utf-8"))
    ws = Workspace(root=root, name=str(meta.get("name") or root.name), description=str(meta.get("description") or ""))
    _read_teams(ws)
    _read_people(ws)
    _read_systems(ws)
    _read_slack(ws)
    _read_tickets(ws)
    _read_docs(ws)
    return ws


def _csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def _read_teams(ws: Workspace) -> None:
    for r in _csv(ws.root / "teams.csv"):
        ws.rows.teams.append({"key": r["key"], "name": r.get("name") or r["key"], "description": r.get("description", "")})
        ws.stats["teams"] += 1


def _read_people(ws: Workspace) -> None:
    for r in _csv(ws.root / "people.csv"):
        handle = r["handle"]
        ws.directory.add(handle, r.get("email"), r.get("slack_id"), r.get("jira_name"), r.get("github"), r.get("name"))
        ws.rows.people[handle] = {
            "handle": handle,
            "name": r.get("name") or None,
            "email": r.get("email") or None,
            "role": r.get("role") or None,
            "team": r.get("team") or None,
            "joined": parse_date(r.get("joined")),
            "left": parse_date(r.get("left")),
        }
        if r.get("team"):
            ws.rows.member_of.append({"handle": handle, "team": r["team"]})
        if r.get("manager"):
            ws.rows.reports_to.append({"handle": handle, "manager": r["manager"]})
        ws.stats["people"] += 1


def _read_systems(ws: Workspace) -> None:
    for r in _csv(ws.root / "systems.csv"):
        key = r["key"]
        ws.systems.add(key, r.get("name", ""), *_split(r.get("aliases")))
        ws.rows.repos.append(
            {
                "key": key,
                "name": r.get("name") or key,
                "description": r.get("description", ""),
                "url": "",
                "language": r.get("kind") or None,
                "stars": 0,
                "aliases": _split(r.get("aliases")),
            }
        )
        if r.get("team"):
            ws.rows.team_owns.append({"team": r["team"], "key": key})
        ws.rows.depends.extend({"src": key, "dst": dep, "via": "declared"} for dep in _split(r.get("depends_on")))
        ws.stats["systems"] += 1


def _source(
    ws: Workspace, uid: str, kind: str, title: str, body: str, ts: int | None, author: str | None, *, state: str = "", labels: list[str] | None = None
) -> dict[str, Any]:
    row = {
        "uid": uid,
        "kind": kind,
        "repo": ws.name,
        "number": None,
        "title": title[:300],
        "body": clip(body, _BODY_LIMIT),
        "url": source_link(uid),
        "ts": ts,
        "state": state,
        "author": author,
        "labels": labels or [],
        "signal": rationale_signal(f"{title}\n{body}"),
    }
    for key in ws.systems.find(f"{title}\n{body}"):
        ws.rows.mentions.append({"uid": uid, "key": key})
    if author:
        ws.rows.authored.append({"handle": author, "uid": uid})
    return row


# --------------------------------------------------------------------------- Slack export


_SLACK_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
_SLACK_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")


def _slack_text(text: str, users: dict[str, str]) -> str:
    text = _SLACK_MENTION.sub(lambda m: "@" + users.get(m.group(1), m.group(1)), text)
    return _SLACK_LINK.sub(lambda m: m.group(2) or m.group(1), text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _read_slack(ws: Workspace) -> None:
    root = ws.root / "slack"
    if not root.exists():
        return
    users: dict[str, str] = {}
    if (root / "users.json").exists():
        for u in json.loads((root / "users.json").read_text(encoding="utf-8")):
            email = (u.get("profile") or {}).get("email")
            handle = ws.directory.resolve(u.get("id"), email, u.get("name"), u.get("real_name"))
            if handle:
                users[u["id"]] = handle
                ws.directory.add(handle, u["id"])
    channels = {c["name"]: c for c in json.loads((root / "channels.json").read_text(encoding="utf-8"))} if (root / "channels.json").exists() else {}
    for channel_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        channel = channel_dir.name
        messages: list[dict[str, Any]] = []
        for day in sorted(channel_dir.glob("*.json")):
            messages.extend(m for m in json.loads(day.read_text(encoding="utf-8")) if m.get("type") == "message" and not m.get("subtype"))
        threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for m in messages:
            threads[m.get("thread_ts") or m["ts"]].append(m)
        purpose = (channels.get(channel, {}).get("purpose") or {}).get("value", "")
        for thread_ts, msgs in threads.items():
            msgs.sort(key=lambda m: float(m["ts"]))
            root_msg = msgs[0]
            author = users.get(root_msg.get("user", "")) or ws.directory.resolve(root_msg.get("user"))
            text = _slack_text(root_msg.get("text", ""), users)
            uid = f"slack:{channel}/{thread_ts}"
            first_line = text.strip().splitlines()[0] if text.strip() else "(empty)"
            src = _source(ws, uid, "thread", f"#{channel}: {first_line}", text, int(float(thread_ts) * 1000), author, labels=[f"#{channel}"])
            src["channel"] = channel
            src["purpose"] = purpose
            ws.rows.sources.append(src)
            ws.stats["slack_threads"] += 1
            for reply in msgs[1:]:
                r_author = users.get(reply.get("user", "")) or ws.directory.resolve(reply.get("user"))
                r_text = _slack_text(reply.get("text", ""), users)
                r_uid = f"{uid}/{reply['ts']}"
                child = _source(ws, r_uid, "message", f"reply in #{channel}: {first_line}", r_text, int(float(reply["ts"]) * 1000), r_author)
                child["parent"] = uid
                ws.rows.children.append(child)
                ws.stats["slack_messages"] += 1


# --------------------------------------------------------------------------- Jira / Linear CSV


def _read_tickets(ws: Workspace) -> None:
    path = ws.root / "tickets.csv"
    if not path.exists():
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        for raw in reader:
            row: dict[str, Any] = defaultdict(list)
            for col, value in zip(header, raw, strict=False):
                # Jira repeats columns (Comment, Component/s, Labels); keep every value.
                row[col].append(value.strip())
            first = {k: (v[0] if v else "") for k, v in row.items()}
            key = first.get("Issue key") or first.get("ID") or first.get("Key")
            if not key:
                continue
            uid = f"ticket:{key}"
            reporter = ws.directory.resolve(first.get("Reporter"), first.get("Creator"))
            assignee = ws.directory.resolve(first.get("Assignee"))
            state = (first.get("Status") or "").lower()
            # Jira repeats columns per value; some exports also pack several values into one cell.
            components = [c for v in row.get("Component/s", []) + row.get("Components", []) for c in _split(v)]
            labels = [lbl for v in row.get("Labels", []) for lbl in _split(v)]
            title = f"{key}: {first.get('Summary') or first.get('Title') or ''}"
            body = first.get("Description", "")
            src = _source(ws, uid, "ticket", title, body, parse_date(first.get("Created")), reporter, state=state, labels=labels)
            src["number"] = None
            ws.rows.sources.append(src)
            ws.stats["tickets"] += 1
            if assignee:
                ws.rows.assigned.append({"handle": assignee, "uid": uid})
            for comp in components:
                ws.rows.touches.append({"uid": uid, "key": comp, "additions": 0, "deletions": 0, "files": 0})
            for i, comment in enumerate(c for c in row.get("Comment", []) if c):
                parts = comment.split(";", 2)
                when, who, text = ([*parts, "", "", ""])[:3] if len(parts) == 3 else ("", "", comment)
                c_author = ws.directory.resolve(who)
                child = _source(ws, f"{uid}/c{i + 1}", "comment", f"comment on {title}", text, parse_date(when), c_author)
                child["parent"] = uid
                ws.rows.children.append(child)
                ws.stats["ticket_comments"] += 1


# --------------------------------------------------------------------------- markdown / OKF docs

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.M)
_BULLET = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.M)
_PREMISE_HEADINGS = re.compile(r"context|rationale|why|drivers|reasons|motivation|assumptions", re.I)
_REJECTED_HEADINGS = re.compile(r"alternatives|options considered|rejected|considered options", re.I)
_DECISION_HEADINGS = re.compile(r"^decision$|^decision outcome$|^we decided$|^outcome$", re.I)
_CHALLENGE_HEADINGS = re.compile(r"contradicted|what we got wrong|assumptions that failed|invalidated", re.I)


def _sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    matches = list(_HEADING.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[m.group(2).strip()] = body[m.end() : end].strip()
    return out


def _bullets(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", b).strip() for b in _BULLET.findall(text)]


def _first_paragraph(text: str) -> str:
    para = text.strip().split("\n\n", 1)[0]
    return re.sub(r"\s+", " ", para).strip()


def _read_docs(ws: Workspace) -> None:
    root = ws.root / "docs"
    if not root.exists():
        return
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        meta: dict[str, Any] = {}
        if m := _FRONTMATTER.match(text):
            meta = yaml.safe_load(m.group(1)) or {}
            text = text[m.end() :]
        rel = path.relative_to(root).as_posix()
        uid = f"doc:{rel}"
        kind = str(meta.get("type") or "doc").lower()
        title = str(meta.get("title") or path.stem)
        authors = [h for a in (meta.get("authors") or []) if (h := ws.directory.resolve(str(a)))]
        ts = parse_date(str(meta.get("date"))) if meta.get("date") else None
        src = _source(ws, uid, kind, title, text, ts, authors[0] if authors else None, state=str(meta.get("status") or ""), labels=[kind])
        for extra in authors[1:]:
            ws.rows.authored.append({"handle": extra, "uid": uid})
        for key in meta.get("systems") or []:
            ws.rows.touches.append({"uid": uid, "key": str(key), "additions": 0, "deletions": 0, "files": 0})
        ws.rows.sources.append(src)
        ws.stats[f"docs_{kind}"] += 1
        sections = _sections(text)
        if kind in {"adr", "rfc", "decision"}:
            _parse_decision(ws, uid, meta, title, ts, authors, sections)
        if meta.get("challenges"):
            _parse_challenges(ws, uid, meta, sections)


def _parse_decision(ws: Workspace, uid: str, meta: dict[str, Any], title: str, ts: int | None, authors: list[str], sections: dict[str, str]) -> None:
    statement = next((_first_paragraph(v) for k, v in sections.items() if _DECISION_HEADINGS.search(k)), "")
    premises = [b for k, v in sections.items() if _PREMISE_HEADINGS.search(k) for b in _bullets(v)]
    rejected = []
    for k, v in sections.items():
        if _REJECTED_HEADINGS.search(k):
            for b in _bullets(v):
                # "**Option**: reason", "Option: reason", "Option — reason": the option is the leading phrase.
                option = re.split(r"\*\*:?\s*|:\s+| — | - ", b.strip("* "), maxsplit=1)[0]
                rejected.append({"option": option.strip("*: ")[:200], "text": b})
    if not statement and not premises:
        return
    status = str(meta.get("status") or "accepted").lower()
    ws.decisions.append(
        {
            "uid": f"{uid}::d1",
            "doc_id": str(meta.get("id") or ""),
            "thread": uid,
            "title": re.sub(r"^(ADR|RFC)-?\d+:\s*", "", title, flags=re.I),
            "summary": statement[:1000],
            "statement": statement[:400],
            "category": str(meta.get("category") or "architecture"),
            "ts": ts,
            "status": "superseded" if status in {"superseded", "deprecated"} else "active",
            "deciders": authors,
            "systems": [str(s) for s in meta.get("systems") or []],
            "premises": premises[:6],
            "rejected": rejected[:4],
            "supersedes": [str(s) for s in _as_list(meta.get("supersedes"))],
        }
    )
    ws.stats["decisions_from_docs"] += 1


def _parse_challenges(ws: Workspace, uid: str, meta: dict[str, Any], sections: dict[str, str]) -> None:
    claims = [b for k, v in sections.items() if _CHALLENGE_HEADINGS.search(k) for b in _bullets(v)]
    for target in _as_list(meta.get("challenges")):
        for claim in claims:
            ws.challenges.append({"source": uid, "target_doc_id": str(target), "quote": claim})
            ws.stats["challenges_from_docs"] += 1


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
