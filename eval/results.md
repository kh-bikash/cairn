# Cairn evaluation

25 questions with ground truth taken from raw GitHub data (see `eval/questions.jsonl`).

| System | Passed | history | impact | multi-hop | risk | unanswerable | who | why | Errors | Avg s |
|---|---|---|---|---|---|---|---|---|---|---|
| **router** | **21/25** | 1/1 | 2/2 | 0/2 | 2/2 | 2/2 | 4/4 | 10/12 | 0 | 0.0 |

## Per question

| Question | router |
|---|---|
| `why-01` Why does falkordb-py's CI run tests against the latest FalkorDB image instead of edge? | ✅ |
| `why-02` Why does falkordb-py filter kwargs against the redis.Redis signature instead of dropping specific keys by name? | ✅ |
| `why-03` Why did falkordb-py start bounding its dependencies to tested versions in release 1.7.0? | ✅ |
| `why-04` Why is the hiredis extra in falkordb-py opt-in rather than a required dependency? | ✅ |
| `why-05` Why did QueryWeaver switch to installing falkordb with the hiredis extra? | ✅ |
| `why-06` Why does QueryWeaver verify sign-ups with a code instead of an emailed link? | ✅ |
| `why-07` Why do QueryWeaver's protected routes answer 503 instead of 401 when the auth backend is unavailable? | ❌ |
| `why-08` Why are GraphRAG-SDK's GitHub Actions pinned to full commit SHAs? | ✅ |
| `why-09` Why did GraphRAG-SDK make SentenceTokenCapChunking the default chunking strategy for ingestion? | ✅ |
| `why-10` What does the cache_unchanged_chunks option in GraphRAG-SDK's update() save, and why was it added? | ❌ |
| `why-11` Why did the FalkorDB MCP server Docker image replace FALKORDB_SAVE with a REDIS_ARGS pass-through? | ✅ |
| `why-12` Why are typescript 7 and openai 3 held back in QueryWeaver's dependency updates? | ✅ |
| `who-01` Who knows GraphRAG-SDK's ingestion module best? | ✅ |
| `who-02` Who should review a change to falkordb-py's asyncio client? | ✅ |
| `who-03` Who is the go-to person for GraphRAG-SDK's retrieval code? | ✅ |
| `who-04` Who works most on QueryWeaver's API routes? | ✅ |
| `impact-01` Which of our repositories depend on falkordb-py? | ✅ |
| `impact-02` If falkordb-py ships a breaking change, who should be warned in the downstream projects? | ✅ |
| `history-01` What changed in falkordb-py's asyncio code in August 2026? | ✅ |
| `multi-01` Which of our projects adopted the hiredis extra for faster reply parsing, and who drove it? | ❌ |
| `multi-02` Which earlier fix does falkordb-py's 1.7.0 dependency-bounding release refer to, and what broke? | ❌ |
| `risk-01` Is knowledge of GraphRAG-SDK's retrieval code concentrated in one person? | ✅ |
| `risk-02` What would we lose in GraphRAG-SDK if galshubeli left? | ✅ |
| `neg-01` Why did QueryWeaver migrate its storage from Postgres to MongoDB? | ✅ |
| `neg-02` Who owns the FalkorDB Kubernetes operator? | ✅ |

Systems: **cairn** = graph agent (LLM + graph tools + citation ledger); **rag** = keyword retrieval over the same documents + one LLM call (same model); **router** = Cairn's deterministic graph routing with no LLM.
