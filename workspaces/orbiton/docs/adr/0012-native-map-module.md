---
type: adr
id: ADR-0012
title: "ADR-0012: Render the driver map in a native module"
date: 2026-05-08
status: accepted
authors: [jordan, elena]
systems: [driver-app]
---
## Context
- The React Native map screen drops to about 15 fps with 150+ stop markers on 2 GB Android devices.
- The ANR rate on those devices reached 4.1%.

## Decision
We will render the map in a native module on Android and iOS and keep the rest of the app in React Native.

## Alternatives considered
- **Rewrite the whole app natively**: too much work for a four-person team.
- **Cluster markers in JavaScript**: helped, but not enough on the slowest devices.
