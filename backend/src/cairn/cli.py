"""`cairn` command-line interface: fetch → load → derive → extract → serve."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from cairn.config import get_settings

# Windows consoles default to cp1252; force UTF-8 so ✓ and non-ASCII names in data print safely.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Cairn: the company brain that remembers why.")
console = Console()


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, markup=False)],
    )
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def doctor() -> None:
    """Check FalkorDB, GitHub and LLM provider configuration."""
    from cairn.graph.client import GraphStore
    from cairn.llm.client import LLM

    s = get_settings()
    table = Table("check", "status", "detail")
    store = GraphStore(s)
    ok = store.ping()
    table.add_row("FalkorDB", _mark(ok), f"{s.falkordb_host}:{s.falkordb_port} graph={s.graph_name}")
    table.add_row("GitHub token", _mark(bool(s.resolve_github_token())), "GITHUB_TOKEN or `gh auth token`")
    llm = LLM(s)
    for role in ("decide", "extract", "agent"):
        ref = getattr(s, f"model_{role}")
        table.add_row(f"LLM {role}", _mark(llm.available(role)), ref)
    console.print(table)
    if ok:
        console.print_json(json.dumps(store.counts()))


@app.command()
def fetch(repos: Annotated[list[str] | None, typer.Argument(help="owner/name; defaults to CAIRN_REPOS")] = None) -> None:
    """Download GitHub repos into data/raw snapshots."""
    from cairn.github.client import GitHubClient
    from cairn.github.fetch import fetch_repo

    s = get_settings()
    token = s.resolve_github_token()
    if not token:
        raise typer.BadParameter("No GitHub token: set GITHUB_TOKEN or run `gh auth login`.")
    with GitHubClient(token) as gh:
        for repo in repos or s.repos:
            stats = fetch_repo(gh, repo, s.raw_dir, since=s.since, max_items=s.max_items_per_repo)
            console.print(f"[green]✓[/] {repo}: {stats.prs} PRs, {stats.issues} issues, {stats.discussions} discussions, {stats.docs} docs")


@app.command()
def load(
    repos: Annotated[list[str] | None, typer.Argument()] = None,
    reset: Annotated[bool, typer.Option(help="Drop the graph first.")] = False,
) -> None:
    """Build the structural graph from snapshots (no LLM), then derive expertise."""
    from cairn.derive.expertise import derive_collaboration, derive_expertise
    from cairn.github.fetch import load_snapshot
    from cairn.graph.client import GraphStore
    from cairn.ingest.loader import build_rows, write_rows

    s = get_settings()
    store = GraphStore(s)
    if reset:
        store.drop()
    snapshots = [load_snapshot(s.raw_dir, r) for r in repos or s.repos]
    rows = build_rows(snapshots)
    console.print(f"skipped {rows.skipped_bot_prs} dependency-bot PRs")
    write_rows(store, rows)
    n = derive_expertise(store)
    c = derive_collaboration(store)
    console.print(f"[green]✓[/] derived {n} KNOWS_ABOUT and {c} WORKS_WITH edges")
    console.print_json(json.dumps(store.counts()))


@app.command()
def derive() -> None:
    """Recompute derived edges (expertise) without reloading."""
    from cairn.derive.expertise import derive_collaboration, derive_expertise
    from cairn.graph.client import GraphStore

    store = GraphStore(get_settings())
    n = derive_expertise(store)
    c = derive_collaboration(store)
    console.print(f"[green]✓[/] derived {n} KNOWS_ABOUT and {c} WORKS_WITH edges")


@app.command()
def extract(
    limit: Annotated[int, typer.Option(help="Max threads to process this run.")] = 200,
    repo: Annotated[str | None, typer.Option(help="Restrict to one repo.")] = None,
) -> None:
    """Use the LLM to extract decisions + premises (with verified quotes) from discussion threads."""
    from cairn.extract.pipeline import run_extraction
    from cairn.graph.client import GraphStore
    from cairn.llm.client import LLM

    s = get_settings()
    llm = LLM(s)
    report = asyncio.run(run_extraction(GraphStore(s), llm, s, limit=limit, repo=repo))
    console.print_json(json.dumps(report))
    console.print_json(json.dumps(llm.usage.snapshot()))


@app.command()
def drift(limit: Annotated[int, typer.Option()] = 200) -> None:
    """Check decision premises against later evidence and record challenges."""
    from cairn.extract.drift import run_drift
    from cairn.graph.client import GraphStore
    from cairn.llm.client import LLM

    s = get_settings()
    llm = LLM(s)
    report = asyncio.run(run_drift(GraphStore(s), llm, s, limit=limit))
    console.print_json(json.dumps(report))
    console.print_json(json.dumps(llm.usage.snapshot()))


okf_app = typer.Typer(no_args_is_help=True, help="Open Knowledge Format bundle export.")
app.add_typer(okf_app, name="okf")


@okf_app.command("export")
def okf_export(out: Annotated[str, typer.Option(help="Output directory.")] = "data/okf") -> None:
    """Write the graph as an OKF markdown bundle."""
    from pathlib import Path

    from cairn.graph.client import GraphStore
    from cairn.okf.export import export_bundle

    n = export_bundle(GraphStore(get_settings())).write(Path(out))
    console.print(f"[green]✓[/] wrote {n} OKF pages to {out}")


actions_app = typer.Typer(no_args_is_help=True, help="Propose and approve agent actions (sandbox repos only).")
app.add_typer(actions_app, name="actions")


@actions_app.command("list")
def actions_list() -> None:
    from cairn.actions.service import ActionService
    from cairn.graph.client import GraphStore

    s = get_settings()
    table = Table("uid", "kind", "status", "title", "result")
    for a in ActionService(GraphStore(s), s).list():
        table.add_row(a.uid, a.kind, a.status, a.title, a.result_url or a.error or "")
    console.print(table)


@actions_app.command("wiki")
def actions_wiki(reason: str = "Refresh the knowledge base from the graph") -> None:
    """Propose a wiki PR with the current OKF bundle."""
    from cairn.actions.service import ActionService
    from cairn.graph.client import GraphStore

    s = get_settings()
    a = ActionService(GraphStore(s), s).propose_wiki_update(reason)
    console.print(a.preview, markup=False)
    console.print(f"pending: [bold]{a.uid}[/]  →  cairn actions approve {a.uid}")


@actions_app.command("route")
def actions_route(repo: str, number: int) -> None:
    """Propose a routing comment on an issue in an allowed repo."""
    from cairn.actions.service import ActionService
    from cairn.graph.client import GraphStore

    s = get_settings()
    a = ActionService(GraphStore(s), s).propose_issue_routing(repo, number)
    console.print(a.preview, markup=False)
    console.print(f"pending: [bold]{a.uid}[/]  →  cairn actions approve {a.uid}")


@actions_app.command("reject")
def actions_reject(uid: str) -> None:
    from cairn.actions.service import ActionService
    from cairn.graph.client import GraphStore

    s = get_settings()
    console.print(ActionService(GraphStore(s), s).reject(uid).status)


@actions_app.command("approve")
def actions_approve(uid: str) -> None:
    from cairn.actions.service import ActionService
    from cairn.graph.client import GraphStore

    s = get_settings()
    a = ActionService(GraphStore(s), s).approve(uid)
    console.print(f"[green]{a.status}[/] {a.result_url or 'nothing to change'}")


@app.command()
def ask(question: str) -> None:
    """Ask the agent a question from the terminal."""
    from cairn.agent.loop import Agent
    from cairn.graph.client import GraphStore
    from cairn.llm.client import LLM

    s = get_settings()
    agent = Agent(GraphStore(s), LLM(s), s)

    async def run() -> None:
        async for event in agent.run(question):
            if event["type"] == "tool_call":
                console.print(f"[cyan]→ {event['name']}[/] {json.dumps(event['args'])}")
            elif event["type"] == "tool_result":
                console.print(f"[dim]  {event['summary']}[/]")
            elif event["type"] == "answer":
                console.rule("answer")
                console.print(event["text"])
                for c in event["citations"]:
                    console.print(f"[dim][{c['ref']}] {c['title']} {c['url']}[/]")

    asyncio.run(run())


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("cairn.api.app:app", host=host, port=port, reload=reload)


def _mark(ok: bool) -> str:
    return "[green]ok[/]" if ok else "[red]missing[/]"


if __name__ == "__main__":
    app()
