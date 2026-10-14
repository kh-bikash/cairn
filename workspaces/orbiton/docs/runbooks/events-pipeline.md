---
type: runbook
title: "Runbook: events-pipeline"
date: 2025-12-11
authors: [priya]
systems: [events-pipeline]
---
## Consumer lag alert
1. Check broker health in the Kafka dashboard.
2. If a broker is out of ISR, check disk and network first.
3. Restart the broker only after confirming the controller is healthy.
6. Re-elect partition leaders for the affected topics.
