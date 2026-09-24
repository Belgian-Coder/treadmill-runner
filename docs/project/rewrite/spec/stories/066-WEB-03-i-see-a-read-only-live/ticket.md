---
id: WEB-03
epic: WEB
priority: P0
phase: 1. Run MVP (offline)
tags: [ui]
---

# WEB-03 — As a user on another device, I see a read-only live view of the run (metrics, chart, devic

## Goal
As a user on another device, I see a read-only live view of the run (metrics, chart, device state) updating in about 1 s.

## Context
- Epic: WEB — Web interface
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#72-web-interface`
  - `docs/spec/00-plan.md#97-web-layout-guide`

## Acceptance criteria
- [ ] AC1 *[auto]*: SSE delivers each engine state change; at most 8 clients; the run engine tick is unaffected (scenario with 8 clients).

## Mockups
```
Browser (read-only live view)
┌──────────────────────────────────────────────────────────┐
│ TreadmillRunner · LIVE          ● Omega ● H10  (read-only)│
│ 8.4 km/h   HR 142 Z3   2.0 %   3.21 km   24:10 / 40:00    │
│ [ live chart with plan overlay ...................... ]   │
│ Control the treadmill on the phone or the console.        │
└──────────────────────────────────────────────────────────┘
```
Refine against design guide (plan section 9).

## Screens to capture during validation
(web) live-412, live-1280

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
