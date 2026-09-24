---
id: RUN-03
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety]
---

# RUN-03 — A single press on Start starts the belt.

## Goal
A single press on Start starts the belt.

## Context
- Epic: RUN — Live run and control
- Priority note: P0, controls after DEV-08
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/09-safety-and-command-contract.md`
  - `docs/spec/02-workouts.md`
  - `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: one `07`; Running after 3 samples > 0.3 km/h; SetSpeed to plan.
- [ ] AC2 *[auto]*: a second press within 800 ms, a press while a Start intent is in flight, or simultaneous presses from the phone and the web (stale state version) send at most one `07`.
- [ ] AC3 *[auto]*: a console start while Armed reaches Running without commands.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
