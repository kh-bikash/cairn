# Cairn evaluation

40 questions with ground truth written from the raw data (see `eval/questions.jsonl`); answering model `nvidia:nvidia/nemotron-3.5-super-vl-120b-a12b`; judge `cloudflare:@cf/openai/gpt-oss-120b`.

**Strict** = automatic checks (right source cited, key facts present, right people named, false premises declined). **Judged** = a different model compares the answer with the written ground truth.

| System | Strict | Judged | cairn (strict) | cairn_orbiton (strict) | Errors | Avg s |
|---|---|---|---|---|---|---|
| **cairn** | **34/40** | **31/40** | 21/25 | 13/15 | 0 | 4.6 |
| **router** | **32/40** | **26/40** | 20/25 | 12/15 | 0 | 0.1 |
| **vector** | **22/40** | **19/40** | 13/25 | 9/15 | 0 | 0.5 |
| **rag** | **9/40** | **5/40** | 4/25 | 5/15 | 0 | 0.0 |

## By category (strict)

| System | drift | history | impact | incident | multi-hop | risk | unanswerable | who | why |
|---|---|---|---|---|---|---|---|---|---|
| **cairn** | 1/1 | 2/2 | 3/3 | 1/1 | 1/4 | 3/3 | 4/4 | 5/5 | 14/17 |
| **router** | 1/1 | 2/2 | 3/3 | 0/1 | 0/4 | 3/3 | 4/4 | 5/5 | 14/17 |
| **vector** | 1/1 | 0/2 | 0/3 | 1/1 | 1/4 | 1/3 | 4/4 | 0/5 | 14/17 |
| **rag** | 0/1 | 0/2 | 0/3 | 0/1 | 1/4 | 0/3 | 4/4 | 1/5 | 3/17 |

## Per question (strict / judged)

| Question | cairn | router | vector | rag |
|---|---|---|---|---|
| `why-01` Why does falkordb-py's CI run tests against the latest FalkorDB image instead of edge? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `why-02` Why does falkordb-py filter kwargs against the redis.Redis signature instead of dropping specific keys by name? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `why-03` Why did falkordb-py start bounding its dependencies to tested versions in release 1.7.0? | ✅ / ✅ | ✅ / ❌ | ✅ / ✅ | ❌ / ❌ |
| `why-04` Why is the hiredis extra in falkordb-py opt-in rather than a required dependency? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| `why-05` Why did QueryWeaver switch to installing falkordb with the hiredis extra? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `why-06` Why does QueryWeaver verify sign-ups with a code instead of an emailed link? | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `why-07` Why do QueryWeaver's protected routes answer 503 instead of 401 when the auth backend is unavailable? | ❌ / ❌ | ❌ / ❌ | ❌ / ✅ | ❌ / ❌ |
| `why-08` Why are GraphRAG-SDK's GitHub Actions pinned to full commit SHAs? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `why-09` Why did GraphRAG-SDK make SentenceTokenCapChunking the default chunking strategy for ingestion? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `why-10` What does the cache_unchanged_chunks option in GraphRAG-SDK's update() save, and why was it added? | ❌ / ✅ | ❌ / ❌ | ✅ / ✅ | ❌ / ❌ |
| `why-11` Why did the FalkorDB MCP server Docker image replace FALKORDB_SAVE with a REDIS_ARGS pass-through? | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ | ❌ / ❌ |
| `why-12` Why are typescript 7 and openai 3 held back in QueryWeaver's dependency updates? | ✅ / ❌ | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `who-01` Who knows GraphRAG-SDK's ingestion module best? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ✅ / ✅ |
| `who-02` Who should review a change to falkordb-py's asyncio client? | ✅ / ❌ | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `who-03` Who is the go-to person for GraphRAG-SDK's retrieval code? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| `who-04` Who works most on QueryWeaver's API routes? | ✅ / ❌ | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `impact-01` Which of our repositories depend on falkordb-py? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| `impact-02` If falkordb-py ships a breaking change, who should be warned in the downstream projects? | ✅ / ✅ | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `history-01` What changed in falkordb-py's asyncio code in August 2026? | ✅ / ✅ | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `multi-01` Which of our projects adopted the hiredis extra for faster reply parsing, and who drove it? | ✅ / ❌ | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `multi-02` Which earlier fix does falkordb-py's 1.7.0 dependency-bounding release refer to, and what broke? | ❌ / ❌ | ❌ / ❌ | ✅ / ✅ | ❌ / ❌ |
| `risk-01` Is knowledge of GraphRAG-SDK's retrieval code concentrated in one person? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| `risk-02` What would we lose in GraphRAG-SDK if galshubeli left? | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ | ❌ / ❌ |
| `neg-01` Why did QueryWeaver migrate its storage from Postgres to MongoDB? | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| `neg-02` Who owns the FalkorDB Kubernetes operator? | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| `orb-why-01` Why did we move the events pipeline to Amazon MSK? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `orb-why-02` Why did we originally choose self-managed Kafka over Amazon SQS for the event bus? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `orb-why-03` Why is the driver app built in React Native instead of native iOS and Android? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| `orb-why-04` Why do drivers get 8-hour refresh tokens? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ |
| `orb-why-05` Why did we choose OR-Tools rather than Gurobi for route optimization? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `orb-drift-01` Do the reasons behind our original Kafka decision still hold? | ✅ / ✅ | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `orb-who-01` Who should I talk to about the payments service? | ✅ / ✅ | ✅ / ✅ | ❌ / ✅ | ❌ / ❌ |
| `orb-risk-01` What knowledge did we lose when Priya left? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| `orb-impact-01` What depends on the events pipeline? | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| `orb-incident-01` A customer was charged twice after Stripe resent a webhook. Has this happened before, and what fixed it? | ✅ / ✅ | ❌ / ✅ | ✅ / ✅ | ❌ / ❌ |
| `orb-history-01` Why did we extend the webhook idempotency key TTL? | ✅ / ✅ | ✅ / ✅ | ❌ / ✅ | ❌ / ❌ |
| `orb-runbook-01` Which runbook is out of date, and why? | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ | ✅ / ✅ |
| `orb-multi-01` Which decisions did people who have since left make, and what still depends on them? | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ | ❌ / ❌ |
| `orb-neg-01` Why did we choose MongoDB for the ledger? | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| `orb-neg-02` Why did we move customer SMS to Amazon SNS? | ✅ / ✅ | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |

Systems:
- **cairn**: graph agent (LLM + graph tools + citation ledger)
- **router**: Cairn's deterministic graph routing, no LLM
- **vector**: dense RAG: chunked sources, NVIDIA embeddings, FalkorDB vector index, one LLM call (same model)
- **rag**: keyword RAG: full-text search over the same sources, one LLM call (same model)
