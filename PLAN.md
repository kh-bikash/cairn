# Cairn — the company brain that remembers *why*

Graph Hacks (WeMakeDevs × FalkorDB) · Track 03: Company Brain · Oct 15–18, 2026 (deadline Oct 18, 11:59 PM IST)

> Most "company brain" entries will be RAG over Slack and docs with a graph drawn next to it.
> Cairn answers **why** things are the way they are, warns you when that reason **no longer holds**,
> and keeps the company wiki current by **opening PRs against it**.

---

## 1. The pitch (30 seconds)

Every company loses the *reasons* behind its decisions. The ADR says "we chose X", the Slack thread says why,
the PR shows what changed, and the people who knew have moved teams. Six months later someone asks
"why do we do it this way?" and nobody can answer, or worse, the answer is out of date.

**Cairn** ingests GitHub, chat, tickets and docs into a FalkorDB graph of people, systems, decisions and
the evidence behind each one. Its agent:

1. **Traces every answer to its sources**: the reply is a path through the graph, with a quote and URL at each step.
2. **Detects decision drift**: when a new fact contradicts the premise of an old decision, Cairn flags it.
3. **Knows who knows what**: expert routing and bus-factor risk, computed from graph algorithms rather than guessed by an LLM.
4. **Does the work**: routes a new issue to the right person, writes handoff packs, and opens wiki PRs (as OKF) when knowledge goes stale.

## 2. Why it should win (mapped to the Track 03 brief)

| Track 03 asks for | What Cairn shows |
|---|---|
| Knowledge from docs, tickets and chats in one graph | GitHub issues/PRs/CODEOWNERS + Discord/Slack export + markdown docs → one schema |
| Who owns what, and who knows what | `OWNS` (explicit) vs `KNOWS_ABOUT` (derived from activity, recency-weighted) + Personalized PageRank |
| Tracing decisions back to their source | `Decision -[:BECAUSE]-> Premise -[:EVIDENCED_BY]-> Source{url, quote}` |
| Keeping knowledge current as things change | Bi-temporal facts (`valid_from`/`valid_to`), contradiction detection, decision drift alerts, auto wiki PRs |
| Answers with clear sources | Every answer returns the traversal path, drawn as a highlighted subgraph |
| "Gets work done" | Actions: assign/comment on an issue, generate a handoff pack, open a PR to the OKF wiki |

Things judges will notice:
- **Demo data is FalkorDB's own org.** We ingest the public FalkorDB GitHub org (FalkorDB, GraphRAG-SDK,
  QueryWeaver, falkordb-browser…) and ask questions about FalkorDB itself. The judges already know the answers, so they can check ours.
- **Graph algorithms do real work**: PageRank, centrality, community detection and shortest path decide what the agent does. The graph isn't just storage.
- **The agent's own reasoning goes into the graph**: each answer is saved as an `Answer` node linked to the nodes it used, so you can audit the agent later.
- **Interop story**: OKF in and out (Google's new Open Knowledge Format), and the graph is exposed over MCP so it also works in Claude Desktop and Cursor.

## 3. Where JEV and OKF fit

Both suggestions fit. Each takes a different layer, so they don't overlap.

### OKF (Open Knowledge Format): the *portable, human-readable* face of the brain
- OKF bundle = folder of markdown files with YAML frontmatter (`type` required; `title`, `description`, `tags`, `timestamp`, `resource`…) + markdown cross-links, `index.md` per dir, optional `log.md`.
- **Import**: any OKF bundle (or plain docs folder) → `Document`/`Concept` nodes; markdown links → `LINKS_TO` edges.
- **Export / write-back**: Cairn keeps a living wiki in a git repo as an OKF bundle:
  `systems/<name>.md`, `decisions/<id>.md`, `people/<handle>.md` (expertise, not HR data), `log.md`.
  When drift is detected, the agent regenerates the affected concept pages **with citations** and **opens a PR**.
- Pitch line: *"FalkorDB is the brain, OKF is the memory you can read, diff, and take with you."*

### JEV (TypeSafe AI decision model): the *fast, cheap judgment* layer at ingestion
- JEV only returns one of the answers you list, with a probability. It costs about $0.04 per 1M input tokens and claims to be 100× faster than an LLM.
- We use it for every bounded decision during ingestion, where an LLM would be slow and expensive:
  - message triage: `decision | ownership_claim | question | answer | incident | noise`
  - entity resolution: "which of these 5 candidate `System` nodes does this mention refer to?"
  - drift check: new fact vs existing premise → `supports | contradicts | supersedes | unrelated`
  - PII / sensitivity gate before anything is stored
- Confidence < 0.7 escalates to the bigger model (Gemini Flash). A **cost/latency panel** shows e.g. "38,000 events ingested, 94% decided by JEV, total cost $0.61". That gives judges a production-cost story.
- ⚠️ **Risk:** JEV is early access with a waitlist (typesafe.ai → console invite). **Have the partner join the waitlist today.**
  Build a `Decider` interface that uses JEV when we have a key and otherwise falls back to Groq (Llama 3.1 8B) with enum-constrained JSON output, so the build never blocks on it.

## 4. Graph model (FalkorDB, one graph per company/tenant)

```
(:Person {handle, name})            (:Team {name})
(:System {name, kind})              // repo, service, module, dataset
(:Source {kind, url, ts, author})   // PR, issue, comment, message, doc, commit  (the "episode")
(:Decision {id, title, ts, status}) (:Premise {text, valid_from, valid_to})
(:Fact {text, valid_from, valid_to})
(:Ticket {id, state})               (:Concept {okf_path, type})
(:Answer {question, ts})            // the agent's own answers, saved for audit

(Person)-[:MEMBER_OF]->(Team)
(Person)-[:OWNS]->(System)                       // CODEOWNERS / explicit
(Person)-[:KNOWS_ABOUT {score, last_seen}]->(System)   // derived from activity
(Person)-[:AUTHORED|REVIEWED|COMMENTED]->(Source)
(Source)-[:MENTIONS]->(System|Person|Decision)
(Decision)-[:BECAUSE]->(Premise)-[:EVIDENCED_BY {quote}]->(Source)
(Decision)-[:AFFECTS]->(System)
(Decision)-[:SUPERSEDES]->(Decision)
(Fact)-[:CONTRADICTS|SUPPORTS]->(Premise)
(System)-[:DEPENDS_ON]->(System)
(Answer)-[:USED]->(any)                          // explainability trail
(Concept)-[:LINKS_TO]->(Concept)                 // OKF cross-links
```

## 5. Agent tools (exposed to the LLM *and* over MCP)

| Tool | What it does (Cypher / algorithm under the hood) |
|---|---|
| `why(decision_or_topic)` | Decision → premises → evidence, plus supersession chain |
| `who_knows(system, k)` | Personalized PageRank from System over KNOWS_ABOUT/AUTHORED, recency-weighted |
| `impact(system)` | `DEPENDS_ON*` traversal + owners + open tickets: "what breaks if this goes down" |
| `bus_factor(person or system)` | Systems where removing a person drops expert count to 0; centrality |
| `drift()` | Premises with contradicting facts newer than the decision |
| `as_of(date, query)` | Time-travel: filter by `valid_from/valid_to` |
| `path(a, b)` | Shortest path, for "how is X connected to Y" |
| `route_issue(issue)` | who_knows + workload, then **comments/assigns** on GitHub |
| `handoff_pack(person)` | Generates OKF pages for everything only this person knows |
| `update_wiki(concepts)` | Regenerates OKF pages with citations, **opens PR** |

## 6. Demo script (3 min video, this is what we build toward)

1. **"Why does GraphRAG-SDK do X?"** The answer streams in while the subgraph lights up hop by hop:
   Decision ← PR #… ← Issue #… ← discussion, with quotes and links. Click any node to open the source.
2. **Drift alert:** "⚠️ This decision assumed `<premise>`. PR #… (3 months later) contradicts it."
   → click **Update wiki** → a real PR opens on the OKF wiki repo with the diff and citations.
3. **"Who should look at this new issue?"** → ranked experts with *why* (paths), agent posts the comment.
4. **Bus-factor radar:** "If @maintainer leaves, what do we lose?" → red systems + one-click handoff pack.
5. **Time slider:** "What did the team believe on June 1?" → graph re-renders as of that date.
6. Closing shot: same brain inside Claude Desktop via MCP, plus the JEV cost panel.

## 7. Architecture

```
 GitHub API ─┐                         ┌─ Gemini Flash (agent loop, synthesis)
 Discord/Slack export ─┤  Ingest worker │  JEV (bounded decisions) ──fallback──► Groq Llama
 OKF bundle / docs ────┘  (Python)  ────┼─► FalkorDB (graph per tenant)  ◄── FalkorDB MCP server
                                        │
                FastAPI  ◄──────────────┘── agent tools (Cypher + algorithms)
                   │
     Web UI (Vite + React + react-force-graph / sigma.js): chat · path view · drift · bus-factor · time slider
                   │
             GitHub (comments, wiki PRs as OKF)
```

LLM (free tier, behind one OpenAI-compatible `llm.py` layer so the provider is a config change):
- **Groq** (`llama-3.1-8b-instant`, about 14.4k requests/day): bulk triage and extraction at ingestion. JEV replaces this once we have a key.
- **Gemini Flash** (Google AI Studio free tier): agent reasoning and final answers.
- Batch about 10 items per extraction call and cache every response on disk, so a re-run costs no quota.

Stack: Python 3.12, `falkordb` client, FastAPI, `openai` client (Groq + Gemini OpenAI-compatible endpoints), Docker Compose
(`falkordb/falkordb:latest` + api + web). Frontend: Vite + React + Tailwind.

## 8. 4-day plan (2 people)

| Day | Person A (backend / graph) | Person B (frontend / integrations) |
|---|---|---|
| **Oct 15** | Docker Compose, schema, GitHub ingester (issues, PRs, comments, CODEOWNERS) for the FalkorDB org | Join JEV waitlist; UI shell: chat + graph canvas; OKF importer/exporter |
| **Oct 16** | Decision/premise extraction, `Decider` (JEV/Groq), entity resolution, `KNOWS_ABOUT` scoring | Agent loop + tools `why`, `who_knows`, `impact`, `path`; streamed path highlighting |
| **Oct 17** | Bi-temporal facts, `drift()`, `bus_factor()`, `as_of()` | Drift panel, bus-factor radar, time slider, GitHub actions (comment, wiki PR) |
| **Oct 18** | Run eval (Cairn vs RAG baseline), MCP exposure, freeze demo snapshot | Cost panel, polish, **record video by 6 PM**, README, blog post (side prize) |

Golden-set questions are written by a human (whoever has a spare hour on Oct 16–17). They can't be generated by the system they test.

**Cut list if behind:** time slider → handoff pack → MCP. **Never cut:** sourced path answers, drift alert, wiki PR.

## 8b. What makes it actually work well (quality > features)

1. **Get structure from the data before using any LLM.** GitHub's API already gives us authors, reviewers,
   files touched (mapped to Systems), `fixes #N` links, CODEOWNERS, labels and timestamps. That's about 70% of the graph,
   and none of it can be hallucinated. The LLM only does the hard part: pulling decisions and premises out of PR
   descriptions and discussion threads, with a required `quote` that must match the source text exactly (otherwise we drop it).
2. **Keep the data narrow and deep.** Use 2–3 FalkorDB repos and the last 12 months, fully ingested. That beats
   20 repos ingested shallowly.
3. **Evaluate against a baseline. This is the proof.** Write a 40–50 question golden set about the FalkorDB org
   (why / who knows / what breaks / what changed), with expected answers and sources. Run **Cairn** and a
   **plain vector-RAG baseline** over the same data. Score answer correctness and citation correctness.
   Put the table in the README and the video: *"Cairn 44/50 vs RAG 27/50."* Few teams will show numbers.
4. **Make the demo deterministic.** Freeze one ingested snapshot, cache LLM calls, and keep a recorded backup video.
5. **Keep a human in the loop for actions.** The agent drafts comments and wiki PRs, and a human clicks **Approve**.
   All writes go to our own sandbox repo or fork, **never FalkorDB's real repos**.
6. **Every answer must be grounded.** If a claim has no evidence path in the graph, the agent says "I don't know"
   and shows the closest connected nodes it found.

## 9. Submission checklist
- [ ] Public repo, `docker compose up` works from a clean clone
- [ ] README with architecture diagram, graph schema, example Cypher
- [ ] 3-min demo video following §6
- [ ] Blog post (dev.to / Hashnode) for the AirPods side prize
- [ ] Note on which parts used coding assistants (check the rules page)

## 10. Open questions
- Confirm with the partner that "JEV" means TypeSafe's Jev decision model.
- Discord/Slack data: is there a public FalkorDB Discord export we're allowed to use? If not, GitHub Discussions + a small synthetic chat set.
- Hackathon rules on pre-existing code and deployment requirement (check the FAQ).
