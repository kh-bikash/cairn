---
type: adr
id: ADR-0003
title: "ADR-0003: Use Kafka for the events pipeline"
date: 2025-12-04
status: superseded
authors: [priya, tom]
systems: [events-pipeline]
---
## Context
- We need to replay at least 7 days of events to rebuild projections after a bug.
- Priya and Lena have operated Kafka in production before, so we can run it ourselves.
- Expected peak is about 3k events/s, well within a 3-broker cluster.

## Decision
We will run a self-managed 3-broker Kafka cluster as the company event bus behind events-pipeline.

## Alternatives considered
- **Amazon SQS**: no replay; consumers can't re-read history after a bug.
- **Amazon Kinesis**: 7-day retention costs more at our volume and nobody on the team has run it.

## Consequences
Platform owns broker operations; SRE needs a runbook before payments events move onto the bus.
