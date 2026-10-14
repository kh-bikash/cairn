---
type: meeting
title: "Platform sync 2026-07-22"
date: 2026-07-22
authors: [tom]
systems: [events-pipeline, auth-service]
---
## Discussion
- MSK evaluation in progress (PLAT-212). Kinesis would mean rewriting 14 consumers.
- Yuki is shadowing Lena on events-pipeline on-call.

## Decisions
- Every platform on-call engineer completes a broker recovery drill before the MSK cutover, because the July outage showed only one person knew the procedure.
