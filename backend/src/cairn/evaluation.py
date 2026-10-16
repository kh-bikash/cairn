"""Evaluation: Cairn vs. RAG baselines vs. graph-only routing, on questions with known answers.

Ground truth in eval/questions.jsonl was written from the raw data (PR text, file lists, manifests; the Orbiton Slack,
Jira and doc exports), not from Cairn's output, so the evaluation isn't circular. Systems:
- cairn: the graph agent (LLM + graph tools + citation ledger)
- router: Cairn's deterministic graph routing, no LLM
- vector: dense RAG (chunked sources, NVIDIA embeddings, FalkorDB vector index) + one LLM call, same model
- rag: keyword RAG (full-text search) + one LLM call, same model

Each answer gets a strict, automatic score:
- cited the right source (an expected uid, or a reply/comment inside it, among its citations or named in the text),
- covered the key facts (at least half of the expected terms present),
- named the right people (an expected handle present),
- for false-premise questions: declined instead of inventing a rationale.
and, optionally, a judged score: a different model compares the answer with the written ground truth.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel

from cairn.agent.loop import Agent, Ledger, clean_citations
from cairn.agent.tools import Evidence
from cairn.config import PROVIDER_BASE_URLS, Settings
from cairn.extract.drift import search_terms
from cairn.graph.client import GraphStore
from cairn.llm.client import LLM, PROVIDER_RPM

SYSTEMS = ("cairn", "router", "vector", "rag")
_SYSTEM_NOTES = {
    "cairn": "graph agent (LLM + graph tools + citation ledger)",
    "router": "Cairn's deterministic graph routing, no LLM",
    "vector": "dense RAG: chunked sources, NVIDIA embeddings, FalkorDB vector index, one LLM call (same model)",
    "rag": "keyword RAG: full-text search over the same sources, one LLM call (same model)",
}
_REFUSAL = re.compile(
    r"(don't|do not|couldn't|could not|cannot|can't|unable to|no (record|evidence|information|data|mention|decision|such)|"
    r"not (find|found|aware|in the graph|recorded|present)|isn't|is not (in|part|recorded)|no extracted decision|nothing (in|about))",
    re.I,
)


@dataclass
class Question:
    id: str
    category: str
    question: str
    expect: dict[str, Any]
    truth: str = ""
    workspace: str = ""


@dataclass
class Score:
    id: str
    category: str
    system: str
    passed: bool
    uid_hit: bool | None
    term_recall: float | None
    handle_hit: bool | None
    refused: bool | None
    seconds: float
    citations: list[str] = field(default_factory=list)
    answer: str = ""
    error: str | None = None
    workspace: str = ""
    judged: bool | None = None
    judge_reason: str = ""


def load_questions(path: Path) -> list[Question]:
    with path.open(encoding="utf-8") as fh:
        return [Question(**json.loads(line)) for line in fh if line.strip()]


def _mentions_uid(text: str, uid: str) -> bool:
    if uid in text:
        return True
    repo, _, number = uid.partition("#")
    name = repo.split("/")[-1].lower()
    return bool(number) and name in text.lower() and re.search(rf"#\s*{re.escape(number)}\b", text) is not None


def _cites(citations: list[str], uid: str) -> bool:
    """A reply or comment inside the expected thread/ticket counts as citing it ('ticket:PAY-341/c1' -> 'ticket:PAY-341')."""
    return any(c == uid or c.startswith(uid + "/") for c in citations)


def score(q: Question, system: str, answer: str, citations: list[str], seconds: float, error: str | None = None) -> Score:
    exp = q.expect
    uid_hit = term_recall = handle_hit = refused = None
    checks: list[bool] = []
    if error is not None:
        return Score(q.id, q.category, system, False, None, None, None, None, seconds, citations, answer, error, q.workspace)
    if exp.get("refuse"):
        refused = bool(_REFUSAL.search(answer))
        checks.append(refused)
    if any_uids := exp.get("any_uids"):
        uid_hit = any(_cites(citations, u) or _mentions_uid(answer, u) for u in any_uids)
        checks.append(uid_hit)
    if all_terms := exp.get("all_terms"):
        term_recall = sum(t.lower() in answer.lower() for t in all_terms) / len(all_terms)
        checks.append(term_recall >= 0.5)
    if any_handles := exp.get("any_handles"):
        handle_hit = any(h.lower() in answer.lower() for h in any_handles)
        checks.append(handle_hit)
    passed = all(checks) and bool(checks)
    return Score(q.id, q.category, system, passed, uid_hit, term_recall, handle_hit, refused, seconds, citations, answer, None, q.workspace)


# --------------------------------------------------------------------------- baselines

_RAG_PROMPT = """Answer the question using ONLY the numbered sources below. Cite claims with [n].
If the sources don't contain the answer, say you don't know. Be concise.

Sources:
{sources}

Question: {question}"""


async def _answer_from(llm: LLM, question: str, hits: list[dict[str, Any]]) -> tuple[str, list[str]]:
    ledger = Ledger()
    blocks = []
    for h in hits:
        ref = ledger.add(Evidence(h["uid"], h["title"], h["url"], h["kind"], h["ts"]))
        blocks.append(f"[{ref}] {h['uid']} ({h['kind']}): {h['title']}\n{h['body']}")
    prompt = _RAG_PROMPT.format(sources="\n\n".join(blocks) or "(no sources found)", question=question)
    result = await llm.chat("agent", [{"role": "user", "content": prompt}], use_cache=True)
    text, cites = clean_citations(result.content, ledger)
    return text, [c["uid"] for c in cites]


async def rag_answer(store: GraphStore, llm: LLM, question: str, k: int = 8) -> tuple[str, list[str]]:
    """Keyword RAG: full-text retrieval over the same sources, then one LLM call. No graph structure, no tools."""
    terms = search_terms(question, limit=10)
    hits = (
        store.records(
            "CALL db.idx.fulltext.queryNodes('Source', $q) YIELD node, score "
            "RETURN node.uid AS uid, node.kind AS kind, node.title AS title, node.url AS url, node.ts AS ts, "
            "substring(coalesce(node.body, ''), 0, 1500) AS body, score ORDER BY score DESC LIMIT $k",
            {"q": terms, "k": k},
        )
        if terms
        else []
    )
    return await _answer_from(llm, question, hits)


class VectorIndex:
    """Dense retrieval baseline: chunked sources embedded with an NVIDIA model, stored in a FalkorDB vector index.

    The index lives in its own graph (``ragvec_<workspace>``), so it never mixes with the product graph and is built
    once, then reused by later runs.
    """

    MODEL = "nvidia/nemotron-3-embed-1b"
    DIM = 2048
    CHUNK = 1500
    OVERLAP = 200
    MAX_CHUNKS = 4
    BATCH = 50

    def __init__(self, settings: Settings, store: GraphStore) -> None:
        key = settings.api_key_for("nvidia")
        if not key:
            raise RuntimeError("the vector baseline needs NVIDIA_API_KEY for embeddings")
        self.store = store
        self.vstore = GraphStore(settings, graph_name=f"ragvec_{store.name}")
        self._client = AsyncOpenAI(api_key=key, base_url=PROVIDER_BASE_URLS["nvidia"], timeout=90.0, max_retries=0)
        self._gap = 60.0 / PROVIDER_RPM.get("nvidia", 36)
        self._next = 0.0
        self._lock = asyncio.Lock()

    def _built(self) -> int:
        try:
            rows = self.vstore.records("MATCH (m:IndexMeta {model: $m}) RETURN m.chunks AS n", {"m": self.MODEL})
        except Exception:  # noqa: BLE001 - a missing graph just means "not built yet"
            return 0
        return int(rows[0]["n"]) if rows else 0

    def _chunks(self) -> list[dict[str, Any]]:
        out = []
        for s in self.store.records("MATCH (s:Source) RETURN s.uid AS uid, s.kind AS kind, s.title AS title, s.url AS url, s.ts AS ts, s.body AS body"):
            text = f"{s['title'] or ''}\n{s['body'] or ''}".strip()
            if not text:
                continue
            step = self.CHUNK - self.OVERLAP
            for i in range(min(self.MAX_CHUNKS, max(1, -(-(len(text) - self.OVERLAP) // step)))):
                piece = text[i * step : i * step + self.CHUNK]
                if piece.strip():
                    meta = {"source": s["uid"], "kind": s["kind"], "title": s["title"] or "", "url": s["url"], "ts": s["ts"]}
                    out.append({"uid": f"{s['uid']}~{i}", **meta, "text": piece})
        return out

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        for attempt in range(6):
            async with self._lock:  # pace to the free tier's per-minute limit
                wait = self._next - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._next = time.monotonic() + self._gap
            try:
                resp = await self._client.embeddings.create(model=self.MODEL, input=texts, extra_body={"input_type": input_type, "truncate": "END"})
                return [d.embedding for d in resp.data]
            except Exception:
                if attempt == 5:
                    raise
                await asyncio.sleep(5.0 * (attempt + 1))
        raise RuntimeError("unreachable")

    async def ensure(self) -> int:
        """Build the index if it isn't there yet. Returns the number of chunks indexed."""
        if n := self._built():
            return n
        chunks = self._chunks()
        self.vstore.drop()
        self.vstore.query(f"CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {{dimension: {self.DIM}, similarityFunction: 'cosine'}}")
        for start in range(0, len(chunks), self.BATCH):
            batch = chunks[start : start + self.BATCH]
            vectors = await self._embed([c["text"] for c in batch], "passage")
            rows = [{**c, "embedding": v} for c, v in zip(batch, vectors, strict=True)]
            self.vstore.write_batches(
                "UNWIND $rows AS r CREATE (c:Chunk {uid: r.uid, source: r.source, kind: r.kind, title: r.title, url: r.url, ts: r.ts, "
                "text: r.text, embedding: vecf32(r.embedding)})",
                rows,
                batch_size=self.BATCH,
            )
        self.vstore.query("CREATE (:IndexMeta {model: $m, chunks: $n})", {"m": self.MODEL, "n": len(chunks)})
        return len(chunks)

    async def search(self, question: str, k: int = 8) -> list[dict[str, Any]]:
        vector = (await self._embed([question], "query"))[0]
        return self.vstore.records(
            "CALL db.idx.vector.queryNodes('Chunk', 'embedding', $k, vecf32($v)) YIELD node, score "
            "RETURN node.source AS uid, node.kind AS kind, node.title AS title, node.url AS url, node.ts AS ts, node.text AS body, score",
            {"k": k, "v": vector},
        )


async def vector_answer(index: VectorIndex, llm: LLM, question: str, k: int = 8) -> tuple[str, list[str]]:
    """Dense RAG: the k most similar chunks, then one LLM call with the same prompt as keyword RAG."""
    return await _answer_from(llm, question, await index.search(question, k))


async def agent_answer(agent: Agent, question: str, *, use_llm: bool) -> tuple[str, list[str], str]:
    """Returns (text, citations, mode); mode is 'unavailable' when the model could not be reached at all."""
    text, citations, mode, unavailable = "", [], "", False
    async for event in agent.run(question, use_llm=use_llm):
        if event["type"] == "answer":
            text, mode = event["text"], event["mode"]
            citations = [c["uid"] for c in event["citations"]]
        elif event["type"] == "notice":
            unavailable = True  # the agent says the model is unavailable and answers from the graph instead
        elif event["type"] == "error":
            raise RuntimeError(event["message"])
    return text, citations, ("unavailable" if unavailable else mode)


# --------------------------------------------------------------------------- judge


class _Judgement(BaseModel):
    correct: bool
    reason: str = ""


_JUDGE_PROMPT = """You grade answers against a reference written by someone who read the source material.

Question: {question}
Reference answer: {truth}
Candidate answer: {answer}

Is the candidate answer correct? It does not need every detail of the reference, but its main point must agree with
the reference and it must not state anything that contradicts it. If the reference says the question has a false
premise, the candidate is correct only if it declines or corrects the premise; inventing a reason is incorrect.
"I don't know" is incorrect unless the reference says the premise is false.
Return JSON: {{"correct": bool, "reason": str (one sentence)}}."""


async def judge(judge_llm: LLM, q: Question, answer: str) -> tuple[bool, str]:
    if not answer.strip() or not q.truth:
        return False, "no answer" if not answer.strip() else "no reference"
    verdict = await judge_llm.structured(
        "extract",
        [{"role": "user", "content": _JUDGE_PROMPT.format(question=q.question, truth=q.truth, answer=answer[:4000])}],
        _Judgement,
        max_tokens=200,
    )
    return verdict.correct, verdict.reason[:300]


# --------------------------------------------------------------------------- run


async def run_eval(settings: Settings, llm: LLM, questions: list[Question], systems: list[str], *, judge_llm: LLM | None = None, k: int = 8) -> list[Score]:
    stores: dict[str, GraphStore] = {}
    agents: dict[str, Agent] = {}
    indexes: dict[str, VectorIndex] = {}

    def workspace(q: Question) -> str:
        return q.workspace or settings.graph_name

    scores: list[Score] = []
    for q in questions:
        ws = workspace(q)
        if ws not in stores:
            stores[ws] = GraphStore(settings, graph_name=ws)
            agents[ws] = Agent(stores[ws], llm, settings)
            agents[ws].persist_answers = False
        for system in systems:
            started = time.perf_counter()
            try:
                if system == "rag":
                    text, cites = await rag_answer(stores[ws], llm, q.question, k)
                elif system == "vector":
                    if ws not in indexes:
                        indexes[ws] = VectorIndex(settings, stores[ws])
                        await indexes[ws].ensure()
                    text, cites = await vector_answer(indexes[ws], llm, q.question, k)
                else:
                    text, cites, mode = await agent_answer(agents[ws], q.question, use_llm=system == "cairn")
                    # The agent may finish with the graph router when it searched without answering; that is still
                    # Cairn's answer. Only a model that could not be reached at all voids the run.
                    if system == "cairn" and mode == "unavailable":
                        raise RuntimeError("agent LLM unavailable; answer came from the router")
                s = score(q, system, text, cites, time.perf_counter() - started)
            except Exception as exc:  # noqa: BLE001 - record and continue; one failure must not void the run
                s = score(q, system, "", [], time.perf_counter() - started, error=f"{type(exc).__name__}: {str(exc)[:200]}")
            if judge_llm is not None and s.error is None:
                try:
                    s.judged, s.judge_reason = await judge(judge_llm, q, s.answer)
                except Exception as exc:  # noqa: BLE001 - a judge failure leaves the strict score standing
                    s.judge_reason = f"judge failed: {type(exc).__name__}"
            scores.append(s)
    return scores


# --------------------------------------------------------------------------- report


def _cell(rows: list[Score], attr: str) -> str:
    return f"{sum(bool(getattr(s, attr)) for s in rows)}/{len(rows)}"


def report_markdown(scores: list[Score], questions: list[Question], *, model: str = "", judge_model: str = "") -> str:
    systems = [s for s in SYSTEMS if any(x.system == s for x in scores)]
    by_sys: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        by_sys[s.system].append(s)
    judged = any(s.judged is not None for s in scores)
    workspaces = sorted({s.workspace for s in scores})
    categories = sorted({q.category for q in questions})
    lines = [
        "# Cairn evaluation",
        "",
        f"{len(questions)} questions with ground truth written from the raw data (see `eval/questions.jsonl`)"
        + (f"; answering model `{model}`" if model else "")
        + (f"; judge `{judge_model}`" if judge_model else "")
        + ".",
        "",
        "**Strict** = automatic checks (right source cited, key facts present, right people named, false premises declined). "
        + ("**Judged** = a different model compares the answer with the written ground truth." if judged else ""),
        "",
    ]
    head = "| System | Strict | " + ("Judged | " if judged else "") + " | ".join(f"{w} (strict)" for w in workspaces) + " | Errors | Avg s |"
    lines += [head, "|---|---|" + ("---|" if judged else "") + "---|" * len(workspaces) + "---|---|"]
    for name in systems:
        rows = by_sys[name]
        cells = [f"**{_cell(rows, 'passed')}**"] + ([f"**{_cell(rows, 'judged')}**"] if judged else [])
        cells += [_cell([s for s in rows if s.workspace == w], "passed") for w in workspaces]
        errors = sum(s.error is not None for s in rows)
        avg = sum(s.seconds for s in rows) / max(1, len(rows))
        lines.append(f"| **{name}** | " + " | ".join(cells) + f" | {errors} | {avg:.1f} |")
    lines += ["", "## By category (strict)", "", "| System | " + " | ".join(categories) + " |", "|---|" + "---|" * len(categories)]
    for name in systems:
        rows = by_sys[name]
        lines.append(f"| **{name}** | " + " | ".join(_cell([s for s in rows if s.category == c], "passed") for c in categories) + " |")
    lines += ["", "## Per question (strict / judged)", "", "| Question | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    index = {(s.id, s.system): s for s in scores}

    def mark(s: Score | None) -> str:
        if s is None:
            return "—"
        if s.error:
            return "⚠️ error"
        strict = "✅" if s.passed else "❌"
        return strict if s.judged is None else f"{strict} / {'✅' if s.judged else '❌'}"

    for q in questions:
        lines.append(f"| `{q.id}` {q.question} | " + " | ".join(mark(index.get((q.id, n))) for n in systems) + " |")
    lines += ["", "Systems:"] + [f"- **{n}**: {_SYSTEM_NOTES[n]}" for n in systems]
    return "\n".join(lines) + "\n"


def write_results(scores: list[Score], questions: list[Question], out_dir: Path, *, model: str = "", judge_model: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text("\n".join(json.dumps(asdict(s), ensure_ascii=False) for s in scores) + "\n", encoding="utf-8")
    md = out_dir / "results.md"
    md.write_text(report_markdown(scores, questions, model=model, judge_model=judge_model), encoding="utf-8")
    return md
