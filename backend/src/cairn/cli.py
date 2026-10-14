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
# Logs go to stderr: stdout must stay clean for piping and for the MCP stdio transport.
log_console = Console(stderr=True)


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=log_console, show_path=False, markup=False)],
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


snapshot_app = typer.Typer(no_args_is_help=True, help="Reproducible demo snapshot (raw data + LLM cache).")
app.add_typer(snapshot_app, name="snapshot")


@snapshot_app.command("pack")
def snapshot_pack(out: Annotated[str, typer.Option()] = "demo/cairn-snapshot.tar.gz") -> None:
    """Bundle data/raw and data/llm-cache into one archive."""
    from pathlib import Path

    from cairn.snapshot import pack

    counts = pack(get_settings().data_dir, Path(out))
    size = Path(out).stat().st_size / 1e6
    console.print(f"[green]✓[/] {out} ({size:.1f} MB): {counts}")


@snapshot_app.command("unpack")
def snapshot_unpack(src: Annotated[str, typer.Option()] = "demo/cairn-snapshot.tar.gz") -> None:
    """Restore a snapshot into the data directory."""
    from pathlib import Path

    from cairn.snapshot import unpack

    n = unpack(Path(src), get_settings().data_dir)
    console.print(f"[green]✓[/] restored {n} files from {src}")


@app.command()
def demo(src: Annotated[str, typer.Option()] = "demo/cairn-snapshot.tar.gz") -> None:
    """One command for judges: restore the snapshot, build the graph, replay extraction and drift (no keys needed)."""
    from pathlib import Path

    from cairn.derive.expertise import derive_collaboration, derive_expertise
    from cairn.extract.drift import run_drift
    from cairn.extract.pipeline import run_extraction
    from cairn.github.fetch import load_snapshot
    from cairn.graph.client import GraphStore
    from cairn.ingest.loader import build_rows, write_rows
    from cairn.llm.client import LLM
    from cairn.snapshot import unpack

    s = get_settings()
    if Path(src).exists():
        console.print(f"restored {unpack(Path(src), s.data_dir)} files from {src}")
    store = GraphStore(s)
    store.drop()
    repos = [r for r in s.repos if (s.raw_dir / r.replace("/", "__") / "meta.json").exists()]
    write_rows(store, build_rows([load_snapshot(s.raw_dir, r) for r in repos]))
    derive_expertise(store)
    derive_collaboration(store)
    llm = LLM(s)
    console.print_json(json.dumps(asyncio.run(run_extraction(store, llm, s, limit=100_000))))
    console.print_json(json.dumps(asyncio.run(run_drift(store, llm, s, limit=100_000))))
    console.print("[green]✓[/] demo graph ready")
    console.print_json(json.dumps(store.counts()))


workspace_app = typer.Typer(no_args_is_help=True, help="Import a company's exports (Slack, Jira/Linear CSV, docs, org chart).")
app.add_typer(workspace_app, name="workspace")


def workspace_graph(name: str) -> str:
    import re

    return f"{get_settings().graph_name}_{re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')}"


@workspace_app.command("import")
def workspace_import(
    path: str,
    graph: Annotated[str | None, typer.Option(help="Graph name; defaults to cairn_<workspace name>.")] = None,
) -> None:
    """Load a workspace directory into its own FalkorDB graph (one graph per company)."""
    from pathlib import Path

    from cairn.graph.client import GraphStore
    from cairn.importers.workspace import read_workspace
    from cairn.importers.write import import_workspace

    ws = read_workspace(Path(path))
    name = graph or workspace_graph(ws.name)
    report = import_workspace(GraphStore(get_settings(), graph_name=name), ws)
    console.print(f"[green]✓[/] imported {ws.name!r} into graph {name}")
    console.print_json(json.dumps(report))


@workspace_app.command("list")
def workspace_list() -> None:
    """List workspaces (one FalkorDB graph each)."""
    from cairn.workspaces import list_workspaces

    for w in list_workspaces(get_settings()):
        console.print(f"{w['graph']:24} {w['name']:28} {w['decisions']:>4} decisions  {w['people']:>4} people")


sample_app = typer.Typer(no_args_is_help=True, help="Generate sample company data in real export formats.")
app.add_typer(sample_app, name="sample")


@sample_app.command("orbiton")
def sample_orbiton(out: Annotated[str, typer.Option()] = "../workspaces/orbiton", seed: int = 7) -> None:
    """Write the fictional 'Orbiton' company as Slack/Jira/markdown exports."""
    from pathlib import Path

    from cairn.samples.orbiton import write

    console.print_json(json.dumps(write(Path(out), seed=seed)))


@app.command("eval")
def eval_cmd(
    systems: Annotated[str, typer.Option(help="Comma-separated: cairn,rag,router")] = "cairn,rag,router",
    questions: Annotated[str, typer.Option()] = "../eval/questions.jsonl",
    out: Annotated[str, typer.Option()] = "../eval",
    only: Annotated[str | None, typer.Option(help="Comma-separated question ids")] = None,
) -> None:
    """Score Cairn against a plain-RAG baseline and graph-only routing on questions with known answers."""
    from pathlib import Path

    from cairn.evaluation import load_questions, run_eval, write_results
    from cairn.graph.client import GraphStore
    from cairn.llm.client import LLM

    s = get_settings()
    qs = load_questions(Path(questions))
    if only:
        wanted = set(only.split(","))
        qs = [q for q in qs if q.id in wanted]
    llm = LLM(s)
    scores = asyncio.run(run_eval(GraphStore(s), llm, s, qs, [x.strip() for x in systems.split(",") if x.strip()]))
    md = write_results(scores, qs, Path(out))
    console.print(md.read_text(encoding="utf-8"), markup=False)
    console.print_json(json.dumps(llm.usage.snapshot()))


@app.command("mcp")
def mcp_cmd(transport: Annotated[str, typer.Option(help="stdio | streamable-http | sse")] = "stdio") -> None:
    """Run Cairn as an MCP server (read-only graph tools for Claude Desktop, Cursor, ...)."""
    from cairn.mcp_server import main

    main(transport)


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
