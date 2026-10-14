---
type: rfc
id: RFC-0007
title: "RFC-0007: Replace the greedy heuristic with OR-Tools"
date: 2026-02-10
status: accepted
authors: [marcus]
systems: [route-optimizer]
---
## Motivation
- About 40% of deliveries have hard time windows that the greedy heuristic ignores.
- 18% of routes currently violate at least one time window.
- OR-Tools solved our 500-stop benchmark in 4.2 seconds against a 30 second budget.

## Decision
We will rebuild route-optimizer on Google OR-Tools with time-window constraints.

## Alternatives considered
- **Keep the greedy heuristic**: cannot model hard time windows.
- **Gurobi**: faster, but the license is about $60k per year and we don't need the extra speed yet.
