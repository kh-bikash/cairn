"""Background sync: connect a repository from the UI and bring it into the graph.

One job runs at a time (the graph is shared and ingestion is write-heavy). Each job walks the same pipeline
as the CLI (fetch, load, derive, extract, drift) and reports per-stage progress the UI can poll.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from cairn.config import Settings
from cairn.derive.expertise import derive_collaboration, derive_expertise
from cairn.extract.drift import run_drift
from cairn.extract.pipeline import run_extraction
from cairn.github.client import GitHubClient
from cairn.github.fetch import fetch_repo, load_snapshot
from cairn.graph.client import GraphStore
from cairn.ingest.loader import build_rows, write_rows
from cairn.llm.client import LLM

log = logging.getLogger(__name__)

STAGES = ("fetch", "load", "extract", "drift")
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


class SyncError(ValueError):
    pass


@dataclass
class Job:
    id: str
    repos: list[str]
    status: str = "queued"  # queued | running | done | failed
    stage: str | None = None
    detail: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    error: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return asdict(self)


def snapshot_repos(settings: Settings) -> list[str]:
    """Every repo with a snapshot on disk, as owner/name."""
    if not settings.raw_dir.exists():
        return []
    return sorted(d.name.replace("__", "/", 1) for d in settings.raw_dir.iterdir() if (d / "meta.json").exists())


def normalize_repo(value: str) -> str:
    repo = value.strip().removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if not _REPO.match(repo):
        raise SyncError(f"expected owner/name, got {value!r}")
    return repo


class SyncManager:
    def __init__(self, store: GraphStore, llm: LLM, settings: Settings) -> None:
        self.store = store
        self.llm = llm
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    def latest(self) -> Job | None:
        return max(self.jobs.values(), key=lambda j: j.started, default=None)

    def start(self, repos: list[str], *, extract_limit: int = 150) -> Job:
        running = self.latest()
        if running and running.status in {"queued", "running"}:
            raise SyncError("a sync is already running")
        if not self.settings.resolve_github_token():
            raise SyncError("the server has no GitHub token (GITHUB_TOKEN)")
        job = Job(id=uuid.uuid4().hex[:10], repos=[normalize_repo(r) for r in repos])
        self.jobs[job.id] = job
        asyncio.get_running_loop().create_task(self._run(job, extract_limit))
        return job

    async def _run(self, job: Job, extract_limit: int) -> None:
        async with self._lock:
            job.status = "running"
            try:
                await self._stage(job, "fetch", self._fetch, job)
                await self._stage(job, "load", self._load, job)
                if self.llm.available("extract"):
                    job.stage, job.detail = "extract", "reading threads for decisions"
                    job.stats["extract"] = await run_extraction(self.store, self.llm, self.settings, limit=extract_limit)
                    job.stage, job.detail = "drift", "checking decisions against later work"
                    job.stats["drift"] = await run_drift(self.store, self.llm, self.settings, limit=extract_limit * 2)
                else:
                    job.stats["extract"] = {"skipped": "no LLM key configured"}
                job.status, job.detail = "done", "up to date"
            except Exception as exc:
                log.exception("sync %s failed", job.id)
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = time.time()

    async def _stage(self, job: Job, name: str, fn: Any, *args: Any) -> None:
        job.stage = name
        job.stats[name] = await asyncio.to_thread(fn, *args)

    def _fetch(self, job: Job) -> dict[str, Any]:
        token = self.settings.resolve_github_token()
        assert token is not None
        out = {}
        with GitHubClient(token) as gh:
            for repo in job.repos:
                job.detail = f"downloading {repo}"
                stats = fetch_repo(gh, repo, self.settings.raw_dir, since=self.settings.since, max_items=self.settings.max_items_per_repo)
                out[repo] = {"prs": stats.prs, "issues": stats.issues, "docs": stats.docs}
        return out

    def _load(self, job: Job) -> dict[str, Any]:
        # Rebuild from every snapshot on disk so cross-repo edges (dependencies, references) stay complete
        # and repos connected from the UI survive restarts.
        available = snapshot_repos(self.settings)
        job.detail = f"building the graph from {len(available)} repositories"
        written = write_rows(self.store, build_rows([load_snapshot(self.settings.raw_dir, r) for r in available]))
        job.detail = "computing expertise"
        return {"repos": available, "written": sum(written.values()), "knows_about": derive_expertise(self.store), "works_with": derive_collaboration(self.store)}
