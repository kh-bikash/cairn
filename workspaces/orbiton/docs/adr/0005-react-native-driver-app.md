---
type: adr
id: ADR-0005
title: "ADR-0005: Build the driver app in React Native"
date: 2025-12-15
status: accepted
authors: [jordan, elena]
systems: [driver-app]
---
## Context
- One mobile team of four can't maintain separate iOS and Android codebases.
- Performance is acceptable on our target devices, including 2 GB RAM Android phones.
- Drivers need the same features on both platforms on day one.

## Decision
We will build the driver app in React Native with a shared codebase for iOS and Android.

## Alternatives considered
- **Native iOS and Android**: doubles the work for a four-person team.
- **Flutter**: nobody on the team has shipped a Flutter app.
