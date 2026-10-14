---
type: rfc
id: RFC-0009
title: "RFC-0009: Build a simple in-house feature flag service"
date: 2026-02-24
status: accepted
authors: [lena]
systems: [feature-flags]
---
## Motivation
- We only need boolean flags, percentage rollouts and per-tenant overrides.
- LaunchDarkly quoted $48k per year for our seat count.

## Decision
We will build a small in-house feature-flags service and revisit buying if we need experimentation.

## Alternatives considered
- **LaunchDarkly**: full-featured but $48k per year.
- **Unleash (self-hosted)**: another service to operate for features we don't need.
