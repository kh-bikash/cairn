---
type: adr
id: ADR-0011
title: "ADR-0011: Move the events pipeline to Amazon MSK"
date: 2026-08-20
status: accepted
authors: [lena, tom]
systems: [events-pipeline]
supersedes: ADR-0003
---
## Context
- Since Priya left in June, nobody on the team has deep Kafka operations experience.
- The July 14 outage took about five hours to recover because broker recovery was unfamiliar.
- Peak traffic now reaches 11k events/s during promotions.

## Decision
We will move events-pipeline to Amazon MSK (managed Kafka) and keep the Kafka protocol so consumers don't change.

## Alternatives considered
- **Amazon Kinesis**: would require rewriting all 14 consumers.
- **Keep self-managed Kafka and hire**: hiring takes months and the risk is now.
