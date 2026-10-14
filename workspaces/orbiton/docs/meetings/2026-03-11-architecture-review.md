---
type: meeting
title: "Architecture review 2026-03-11"
date: 2026-03-11
authors: [tom]
systems: [warehouse-db, analytics-warehouse]
---
## Attendees
Tom, Maya, Noah, Ines, Rachel

## Discussion
- The orders table in warehouse-db has 400M rows and vacuum takes hours.
- Analytics queries mostly filter by month.

## Decisions
- Partition the orders table in warehouse-db by month, because almost every query filters by order month and vacuum on the single table takes hours. Owner: Noah.
