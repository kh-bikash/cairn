---
type: postmortem
id: PM-2026-04-22
title: "Postmortem: driver app ANRs on low-end Android"
date: 2026-04-22
authors: [elena, chris]
systems: [driver-app]
challenges: [ADR-0005]
---
## Summary
The driver app's ANR rate reached 4.1%, almost entirely on 2 GB RAM Android devices.

## What we got wrong
- Performance was not acceptable on 2 GB RAM Android devices: the map screen dropped to about 15 fps with real routes of 150+ stops.
- The prototype we benchmarked only rendered 20 markers.

## Action items
- Move map rendering to a native module (ADR-0012).
