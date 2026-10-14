# Cairn

**The company brain that remembers *why*.** Built on [FalkorDB](https://falkordb.com) for Graph Hacks, Track 03: Company Brain.

Cairn ingests an engineering organisation's GitHub activity into a FalkorDB graph of people, systems,
decisions and the evidence behind them. An agent answers questions by walking that graph, and every claim
in an answer links to the PR, issue or comment that supports it. It also does the follow-up work: routing
new issues to the right people and keeping a readable wiki current, with a human approving every write.

- **Why is it like this?** Decisions with their reasons, rejected alternatives and who decided, each backed by a verbatim quote.
- **Is that still true?** Drift detection flags decisions whose reasons were contradicted by later work.
- **Who knows this?** Recency-weighted expertise per module, with the PRs that justify the ranking.
- **What breaks?** Transitive dependencies across repos (from real package manifests), contacts and in-flight work.
- **Where are we fragile?** Bus factor per module, bridge people (betweenness), inferred teams (label propagation).
- **Do the work.** Route a new issue to its likely owners; publish the brain as an OKF wiki via pull request.
- **Use it anywhere.** The same graph tools are an MCP server for Claude Desktop, Cursor and other MCP clients.

The demo dataset is FalkorDB's own GitHub org (GraphRAG-SDK, QueryWeaver, FalkorDB-MCPServer, falkordb-py).
Live examples of the agent's work: [routed issues and the OKF wiki PR](https://github.com/kh-bikash/cairn-sandbox).

## Quick start (no keys needed)

```bash
docker compose up -d --build              # FalkorDB :6379 (+ Browser :3000), API :8000, web :8080
docker compose run --rm api cairn demo    # restore the demo snapshot, build the graph, replay extraction
```

Open http://localhost:8080. `cairn demo` rebuilds the exact demo graph from `demo/cairn-snapshot.tar.gz`:
raw GitHub data plus cached model responses, so extraction replays with no GitHub token and no LLM keys.
Without an LLM key the chat runs in "graph-only mode", where answers come from deterministic graph routing.

To work on fresh data, add keys to `.env` (copy `.env.example`; Gemini and Groq free tiers work):

```bash
docker compose run --rm api cairn fetch     # GitHub -> data/raw snapshots (needs GITHUB_TOKEN)
docker compose run --rm api cairn load      # snapshots -> graph + expertise (no LLM, ~2s)
docker compose run --rm api cairn extract   # LLM: decisions + reasons with verified quotes
docker compose run --rm api cairn drift     # LLM: challenge reasons against later evidence
```

## How it works

```
GitHub GraphQL ──► snapshots (data/raw) ──► loader ──► FalkorDB graph ◄── MCP server (read-only tools)
                                               │            ▲
                    expertise + collaboration  │            │  Cypher + algo.* tools
                           (pure graph math)   ▼            │
           fast model (triage) ─► extraction ─► quote verification ─┘
           agent model ─► tool loop ─► citation ledger ─► SSE ─► React + live force graph
                                     └─► proposed actions ─► human approval ─► GitHub (sandbox only)
```

**Structure first, LLM second.** Authors, reviewers, merges, touched modules, closing references and
cross-repo dependencies come straight from GitHub data, so most of the graph can't be hallucinated.
The LLM only does what needs judgement: finding decisions and their reasons.

**Grounding is enforced in code.** Each extracted reason must include a verbatim quote from a labelled
segment of the thread, and `extract/grounding.py` checks it against the source text; claims that fail are
dropped. At answer time every source gets a citation number, citations the model invents are stripped,
and the agent declines when no recorded decision covers the question's topic. Each answer is written back
as an `(:Answer)-[:USED]->(…)` audit trail.

**Graph algorithms do real work.** `algo.betweenness` finds bridge people, `algo.labelPropagation` on a
people↔people `WORKS_WITH` projection infers teams, `shortestPath` explains how two things connect, and
variable-length `DEPENDS_ON*` traversals compute blast radius. Issue routing goes from similar past work to
the modules it touched to the people who know those modules.

**Writes need a human.** The agent can only *propose* actions (a routing comment, a wiki PR). Each is stored
as a pending `(:Action)` with a full preview; nothing reaches GitHub until someone approves it in the UI or
CLI, and writes are refused outside `CAIRN_ACTION_REPOS`. Published text never @-mentions anyone and links
to other repos go through `redirect.github.com`, so Cairn never pings people or adds backlinks to their repos.

## Evaluation

`eval/questions.jsonl` holds 25 questions whose answers were written from the raw GitHub data (PR text,
file lists, manifests), not from Cairn's output. They cover why, who, impact, history, multi-hop, risk,
and two trick questions with false premises that should be declined.

```bash
cd backend && uv run cairn eval --systems cairn,rag,router   # writes eval/results.md
```

It compares **cairn** (graph agent), **rag** (keyword retrieval over the same documents plus one call to the
same model) and **router** (Cairn's graph routing with no LLM). See [`eval/results.md`](eval/results.md).

## MCP

```bash
cd backend && uv run cairn mcp                       # stdio
uv run cairn mcp --transport streamable-http         # HTTP
```

Claude Desktop (`claude_desktop_config.json`):

```json
{ "mcpServers": { "cairn": { "command": "uv", "args": ["--directory", "/path/to/company-brain/backend", "run", "cairn", "mcp"] } } }
```

Tools: `search`, `why`, `who_knows`, `impact`, `suggest_owners`, `details`, `person_profile`, `timeline`,
`bus_factor`, `drift_report`, `teams`, `connect`, `list_systems`. All are read-only and return their sources.

## OKF wiki

`cairn okf export` writes the brain as an [Open Knowledge Format](https://www.gitbook.com/blog/what-is-okf-open-knowledge-format)
bundle: cross-linked markdown with YAML frontmatter for systems, modules, decisions (reasons, quotes, drift
warnings), people and a decision log. The export is deterministic, so the agent's wiki PRs show only real changes.

### Graph model

| Node | Key | Notes |
|---|---|---|
| `Person` | `handle` | bots are filtered out (`__typename` + known AI/CI agents) |
| `System` | `key` | `kind: repo \| code \| tests \| docs \| tooling \| assets` |
| `Source` | `uid` | `repo#n` for PRs/issues/discussions; `repo#n/issuecomment-id` for replies; `repo:path` for docs |
| `Decision` | `uid` | `status`, `drift`, `confidence` = verified claims / claimed |
| `Premise` | `uid` | a reason (`BECAUSE`) or a rejected option (`REJECTED`) |
| `Action` | `uid` | a proposed write and its approval status |
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
uv run cairn demo                    # or: cairn fetch && cairn load && cairn extract
uv run cairn ask "What breaks if falkordb-py changes?"
uv run cairn serve --reload          # API on :8000

cd ../web && npm install && npm run dev   # UI on :5173, proxies /api to :8000
```

Quality gates (all passing): `uv run pytest` (unit + FalkorDB integration; set `CAIRN_FALKORDB_PORT`
if FalkorDB isn't on 6379), `uv run ruff check`, `uv run mypy` (strict), and `npm run build` for the web app.

### Model configuration

Every LLM role is a comma-separated fallback chain of `provider:model` refs over OpenAI-compatible endpoints
(`groq`, `gemini`, `openai`, `anthropic`, `typesafe`). If a model is overloaded Cairn moves to the next one;
if it reports its daily quota is used up, Cairn skips it for the rest of the session. Responses at
temperature 0 are cached under `data/llm-cache`, so re-running costs no quota and snapshots replay offline.

| Role | Used for |
|---|---|
| `CAIRN_MODEL_DECIDE` | bounded choices: thread triage, drift verdicts (the slot for TypeSafe JEV) |
| `CAIRN_MODEL_EXTRACT` | decision extraction; escalation when `decide` is unsure |
| `CAIRN_MODEL_AGENT` | tool-calling agent and final answers |

### FalkorDB notes

- The stock image sets a 1s query timeout; `docker-compose.yml` raises it (`TIMEOUT_DEFAULT 30000`).
- A filtered `OPTIONAL MATCH` (or pattern comprehension with `WHERE`) that follows an aggregating `WITH`
  can return no rows. Cairn avoids that query shape, and `tests/test_integration.py` asserts every tool
  returns data so a regression shows up immediately.
