"""Evaluation: Cairn vs. a plain RAG baseline vs. graph-only routing, on questions with known answers.

Ground truth in eval/questions.jsonl was written from the raw GitHub data (PR text, file lists, manifests),
not from Cairn's output, so the evaluation isn't circular. Each answer is scored on:
- cited the right source (any expected uid among its citations, or named in the text),
- covered the key facts (expected terms present),
- named the right people (expected handles present),
- for unanswerable questions: declined instead of inventing a rationale.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cairn.agent.loop import Agent, Ledger, clean_citations
from cairn.agent.tools import Evidence
from cairn.config import Settings
from cairn.extract.drift import search_terms
from cairn.graph.client import GraphStore
from cairn.llm.client import LLM

SYSTEMS = ("cairn", "rag", "router")
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


def load_questions(path: Path) -> list[Question]:
    with path.open(encoding="utf-8") as fh:
        return [Question(**json.loads(line)) for line in fh if line.strip()]


def _mentions_uid(text: str, uid: str) -> bool:
    if uid in text:
        return True
    repo, _, number = uid.partition("#")
    name = repo.split("/")[-1].lower()
    return name in text.lower() and re.search(rf"#\s*{re.escape(number)}\b", text) is not None


def score(q: Question, system: str, answer: str, citations: list[str], seconds: float, error: str | None = None) -> Score:
    exp = q.expect
    uid_hit = term_recall = handle_hit = refused = None
    checks: list[bool] = []
    if error is not None:
        return Score(q.id, q.category, system, False, None, None, None, None, seconds, citations, answer, error)
    if exp.get("refuse"):
        refused = bool(_REFUSAL.search(answer))
        checks.append(refused)
    if any_uids := exp.get("any_uids"):
        uid_hit = any(u in citations or _mentions_uid(answer, u) for u in any_uids)
        checks.append(uid_hit)
    if all_terms := exp.get("all_terms"):
        term_recall = sum(t.lower() in answer.lower() for t in all_terms) / len(all_terms)
        checks.append(term_recall >= 0.5)
    if any_handles := exp.get("any_handles"):
        handle_hit = any(h.lower() in answer.lower() for h in any_handles)
        checks.append(handle_hit)
    return Score(q.id, q.category, system, all(checks) and bool(checks), uid_hit, term_recall, handle_hit, refused, seconds, citations, answer)


# --------------------------------------------------------------------------- systems

_RAG_PROMPT = """Answer the question using ONLY the numbered sources below. Cite claims with [n].
If the sources don't contain the answer, say you don't know. Be concise.

Sources:
{sources}

Question: {question}"""


async def rag_answer(store: GraphStore, llm: LLM, question: str, k: int = 8) -> tuple[str, list[str]]:
    """Baseline: keyword retrieval over the same documents, then one LLM call. No graph structure, no tools."""
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
    ledger = Ledger()
    blocks = []
    for h in hits:
        ref = ledger.add(Evidence(h["uid"], h["title"], h["url"], h["kind"], h["ts"]))
        blocks.append(f"[{ref}] {h['uid']} ({h['kind']}): {h['title']}\n{h['body']}")
    prompt = _RAG_PROMPT.format(sources="\n\n".join(blocks) or "(no sources found)", question=question)
    result = await llm.chat("agent", [{"role": "user", "content": prompt}], use_cache=True)
    text, cites = clean_citations(result.content, ledger)
    return text, [c["uid"] for c in cites]


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


async def run_eval(store: GraphStore, llm: LLM, settings: Settings, questions: list[Question], systems: list[str]) -> list[Score]:
    agent = Agent(store, llm, settings)
    agent.persist_answers = False
    scores: list[Score] = []
    for q in questions:
        for system in systems:
            started = time.perf_counter()
            try:
                if system == "rag":
                    text, cites = await rag_answer(store, llm, q.question)
                else:
                    text, cites, mode = await agent_answer(agent, q.question, use_llm=system == "cairn")
                    # The agent may finish with the graph router when it searched without answering; that is still
                    # Cairn's answer. Only a model that could not be reached at all voids the run.
                    if system == "cairn" and mode == "unavailable":
                        raise RuntimeError("agent LLM unavailable; answer came from the router")
                scores.append(score(q, system, text, cites, time.perf_counter() - started))
            except Exception as exc:  # noqa: BLE001 - record and continue; one failure must not void the run
                scores.append(score(q, system, "", [], time.perf_counter() - started, error=f"{type(exc).__name__}: {str(exc)[:200]}"))
    return scores


# --------------------------------------------------------------------------- report


def report_markdown(scores: list[Score], questions: list[Question]) -> str:
    systems = [s for s in SYSTEMS if any(x.system == s for x in scores)]
    by_sys: dict[str, list[Score]] = defaultdict(list)
    for s in scores:
        by_sys[s.system].append(s)
    categories = sorted({q.category for q in questions})
    lines = ["# Cairn evaluation", "", f"{len(questions)} questions with ground truth taken from raw GitHub data (see `eval/questions.jsonl`).", ""]
    lines += ["| System | Passed | " + " | ".join(categories) + " | Errors | Avg s |", "|---|---|" + "---|" * len(categories) + "---|---|"]
    for sysname in systems:
        rows = by_sys[sysname]
        ok = sum(s.passed for s in rows)
        cells = []
        for c in categories:
            cat = [s for s in rows if s.category == c]
            cells.append(f"{sum(s.passed for s in cat)}/{len(cat)}")
        errors = sum(s.error is not None for s in rows)
        avg = sum(s.seconds for s in rows) / max(1, len(rows))
        lines.append(f"| **{sysname}** | **{ok}/{len(rows)}** | " + " | ".join(cells) + f" | {errors} | {avg:.1f} |")
    lines += ["", "## Per question", "", "| Question | " + " | ".join(systems) + " |", "|---|" + "---|" * len(systems)]
    index = {(s.id, s.system): s for s in scores}
    for q in questions:
        marks = []
        for sysname in systems:
            hit = index.get((q.id, sysname))
            marks.append("—" if hit is None else ("✅" if hit.passed else ("⚠️ error" if hit.error else "❌")))
        lines.append(f"| `{q.id}` {q.question} | " + " | ".join(marks) + " |")
    lines += [
        "",
        "Systems: **cairn** = graph agent (LLM + graph tools + citation ledger); **rag** = keyword retrieval over the same "
        "documents + one LLM call (same model); **router** = Cairn's deterministic graph routing with no LLM.",
    ]
    return "\n".join(lines) + "\n"


def write_results(scores: list[Score], questions: list[Question], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text("\n".join(json.dumps(asdict(s), ensure_ascii=False) for s in scores) + "\n", encoding="utf-8")
    md = out_dir / "results.md"
    md.write_text(report_markdown(scores, questions), encoding="utf-8")
    return md
