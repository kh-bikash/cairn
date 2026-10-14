---
type: runbook
title: "Runbook: route-optimizer"
date: 2026-04-02
authors: [marcus]
systems: [route-optimizer]
---
## Solver timeouts
The solver budget is 60 seconds per depot. If it times out, the dispatcher falls back to yesterday's routes.
Solver parameters live in config/solver.yaml; ask Marcus before changing them.
