# Cairn

**The company brain that remembers *why*.** Built on [FalkorDB](https://falkordb.com) for Graph Hacks, Track 03: Company Brain.

Cairn ingests an engineering organisation's GitHub activity into a FalkorDB graph of people, systems,
decisions and the evidence behind them. An agent then answers questions by walking that graph, and
every claim in an answer links to the PR, issue or comment that supports it.

- **Why is it like this?** Decisions with their premises, rejected alternatives and who decided, each backed by a verbatim quote.
- **Is that still true?** Drift detection flags decisions whose premises were contradicted by later work.
- **Who knows this?** Recency-weighted expertise per module, with the PRs that justify the ranking.
- **What breaks?** Transitive dependencies across repos (from real package manifests), contacts and in-flight work.
- **Where are we fragile?** Bus factor per module, bridge people (betweenness), inferred teams (label propagation).

The demo dataset is FalkorDB's own GitHub org (GraphRAG-SDK, QueryWeaver, FalkorDB-MCPServer, falkordb-py).

## Quick start

```bash
cp .env.example .env            # add GROQ_API_KEY and GEMINI_API_KEY (free tiers), and GITHUB_TOKEN
docker compose up -d --build    # FalkorDB :6379 (+ Browser :3000), API :8000, web :8080

docker compose run --rm api cairn fetch     # GitHub -> data/raw snapshots (needs GITHUB_TOKEN)
docker compose run --rm api cairn load      # snapshots -> graph + expertise (no LLM, ~2s)
docker compose run --rm api cairn extract   # LLM: decisions + premises with verified quotes
docker compose run --rm api cairn drift     # LLM: challenge premises against later evidence
```

Open http://localhost:8080. With no LLM keys configured, Cairn still works: answers come from a deterministic
graph router (the header shows "graph-only mode"), so a demo can't be broken by a quota running out.

## How it works

```
GitHub GraphQL ──► snapshots (data/raw, reproducible) ──► loader ──► FalkorDB graph
                                                             │            ▲
                                    expertise + collaboration │            │  Cypher + algo.* tools
                                           (pure graph math)  ▼            │
                      Groq (triage)  ─►  extraction ─► quote verification ─┘
                      Gemini (agent) ─►  tool loop ─► citation ledger ─► SSE ─► React + force graph
```

**Structure first, LLM second.** Authors, reviewers, merges, touched modules, closing references and
cross-repo dependencies come straight from GitHub data, so roughly 70% of the graph can't be hallucinated.
The LLM only does what needs judgement: finding decisions and their reasons.

**Grounding is enforced in code.** Each extracted premise must include a verbatim quote from a labelled
segment of the thread, and `extract/grounding.py` checks it against the source text. Claims that fail
are dropped. At answer time, every tool result is numbered in a citation ledger and citations the model
invents are stripped before the answer is shown. Each answer is written back as an `(:Answer)-[:USED]->(…)`
audit trail.

**Graph algorithms do real work.** `algo.betweenness` finds bridge people, `algo.labelPropagation` on a
people↔people `WORKS_WITH` projection infers teams, `shortestPath` explains how two things connect, and
variable-length `DEPENDS_ON*` traversals compute blast radius.

### Graph model

| Node | Key | Notes |
|---|---|---|
| `Person` | `handle` | bots are filtered out (`__typename` + known AI/CI agents) |
| `System` | `key` | `kind: repo \| code \| tests \| docs \| tooling \| assets` |
| `Source` | `uid` | `repo#n` for PRs/issues/discussions; `repo#n/issuecomment-id` for replies; `repo:path` for docs |
| `Decision` | `uid` | `status`, `drift`, `confidence` = verified claims / claimed |
| `Premise` | `uid` | a reason (`BECAUSE`) or a rejected option (`REJECTED`) |
| `Answer` | `uid` | the agent's own answers, linked to the nodes they used |

Key relationships: `AUTHORED`, `REVIEWED`, `MERGED`, `TOUCHES`, `IN_REPO`, `PART_OF`, `DEPENDS_ON {via}`,
`KNOWS_ABOUT {score, authored, reviewed, last_ts}`, `WORKS_WITH {weight}`, `DECIDED`, `DECIDED_IN`,
`BECAUSE`, `REJECTED`, `EVIDENCED_BY {quote}`, `CHALLENGES {quote, verdict}`, `AFFECTS`, `USED`.

Example: who should review a change to GraphRAG-SDK ingestion, and why?

```cypher
MATCH (p:Person)-[k:KNOWS_ABOUT]->(s:System {key: 'FalkorDB/GraphRAG-SDK:graphrag_sdk/ingestion'})
RETURN p.handle, k.score, k.authored, k.reviewed, k.last_ts
ORDER BY k.score DESC LIMIT 5
```

## Development

```bash
docker compose up -d falkordb
cd backend && uv sync
uv run cairn doctor                  # checks FalkorDB, GitHub token, LLM keys
uv run cairn fetch && uv run cairn load
uv run cairn ask "What breaks if falkordb-py changes?"
uv run cairn serve --reload          # API on :8000

cd ../web && npm install && npm run dev   # UI on :5173, proxies /api to :8000
```

Quality gates (all passing): `uv run pytest` (unit + FalkorDB integration; set `CAIRN_FALKORDB_PORT`
if FalkorDB isn't on 6379), `uv run ruff check`, `uv run mypy` (strict), and `npm run build` for the web app.

### Model configuration

Every LLM role is `provider:model` over an OpenAI-compatible endpoint (`groq`, `gemini`, `openai`,
`anthropic`, `typesafe`), so switching providers is an `.env` change. Responses at temperature 0 are cached
on disk under `data/llm-cache`, so re-running ingestion costs no quota.

| Role | Default | Used for |
|---|---|---|
| `CAIRN_MODEL_DECIDE` | `groq:llama-3.1-8b-instant` | bounded choices: thread triage, drift verdicts (JEV slot) |
| `CAIRN_MODEL_EXTRACT` | `groq:llama-3.3-70b-versatile` | decision extraction; escalation when `decide` is unsure |
| `CAIRN_MODEL_AGENT` | `gemini:gemini-2.5-flash` | tool-calling agent and final answers |

### FalkorDB notes

- The stock image sets a 1s query timeout; `docker-compose.yml` raises it (`TIMEOUT_DEFAULT 30000`).
- A filtered `OPTIONAL MATCH` (or pattern comprehension with `WHERE`) that follows an aggregating `WITH`
  can return no rows. Cairn avoids that query shape, and `tests/test_integration.py` asserts every tool
  returns data so a regression shows up immediately.
