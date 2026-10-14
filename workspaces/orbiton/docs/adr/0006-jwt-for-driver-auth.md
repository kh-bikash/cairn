---
type: adr
id: ADR-0006
title: "ADR-0006: JWT access and refresh tokens for driver auth"
date: 2026-01-20
status: accepted
authors: [lena]
systems: [auth-service, driver-app]
---
## Context
- Drivers lose connectivity for long stretches and must stay logged in for a full shift.
- Session cookies require a round trip to auth-service on every request.

## Decision
We will issue 15-minute JWT access tokens and 8-hour refresh tokens so a shift survives offline.

## Alternatives considered
- **Server-side sessions**: don't work offline.
- **Long-lived access tokens**: a stolen token would be valid for days.
