---
type: postmortem
id: PM-2026-07-14
title: "Postmortem: events pipeline outage on 2026-07-14"
date: 2026-07-15
authors: [rachel, lena]
systems: [events-pipeline, payments-service, dispatch-api]
challenges: [ADR-0003]
---
## Summary
Two Kafka brokers dropped out of ISR during the summer promotion. Consumer lag delayed payments and dispatch events for about five hours.

## Contradicted assumptions
- After Priya left in June, nobody on call had operated Kafka in production, so recovery took five hours instead of minutes.
- Peak traffic reached 11k events/s during the promotion, more than three times the 3k events/s the cluster was sized for.

## Action items
- Evaluate managed Kafka (PLAT-212).
- Pair every on-call engineer on a broker recovery drill.
