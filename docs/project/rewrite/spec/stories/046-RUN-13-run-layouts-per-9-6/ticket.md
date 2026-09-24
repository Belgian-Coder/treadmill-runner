---
id: RUN-13
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui]
---

# RUN-13 — Run layouts per 9.6.

## Goal
Run layouts per 9.6.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
- `docs/spec/05-sessions-and-recording.md`
- `docs/spec/09-safety-and-command-contract.md`
- `docs/spec/02-workouts.md`
- `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: Roborazzi at 411×914 and 914×411 with gesture and 3-button insets; ATF; STOP ≥ 72 dp and above the gesture area.

## Mockups
```
Portrait (~411 × 914 dp)                    Landscape (~914 × 411 dp)
┌───────────────────────────────┐          ┌──────────────────────────┬──────────────────┐
│ [banner slot 56dp]            │          │ LIVE CHART (58%)         │ HERO 8.4 km/h    │
│         8.4 km/h   ← hero     │          │  speed/incline/HR + plan │ HR 142 · Z3      │
│      target 8.5 ▁▁▁▁          │          │  overlay, cursor         │ [−] 8.4  [+]     │
│ HR 142 Z3 │ 2.0 %             │          │                          │ [−] 2.0% [+]     │
│ 3.21 km   │ 24:10 / 40:00     │          │                          ├────────┬─────────┤
│ Step 4/9 → next 9.0 in 1:20   │          │                          │  STOP  │ Pause   │
│ [ − ]  8.4 km/h (req 8.5) [ + ]│          └──────────────────────────┴────────┴─────────┘
│ [ − ]  2.0 %              [ + ]│
│ [   STOP   ] [ Pause (stops) ] │
└───────────────────────────────┘
```
Refine against design guide (plan section 9).

## Screens to capture during validation
run-armed, run-running, run-paused, run-link-lost, run-stop-sheet

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
