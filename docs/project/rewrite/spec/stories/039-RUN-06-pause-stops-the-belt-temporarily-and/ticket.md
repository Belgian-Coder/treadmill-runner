---
id: RUN-06
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, hw]
---

# RUN-06 — Pause stops the belt temporarily and keeps progress; Resume (single press) continues where

## Goal
Pause stops the belt temporarily and keeps progress; Resume (single press) continues where I paused.

## Context
- Epic: RUN — Live run and control
- Priority note: P0, after DEV-08
- Spec:
- `docs/spec/05-sessions-and-recording.md`
- `docs/spec/09-safety-and-command-contract.md`
- `docs/spec/02-workouts.md`
- `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: state `PausedWaitingForPhysicalResume`; cursor and plan position unchanged; the paused interval is not moving time.
- [ ] AC1b *[auto]*: Resume is a fresh Start, then the current segment's target is re-applied.
- [ ] AC1c *[hw]*: HW-15.
- [ ] AC2 *[auto]*: the engine-level 800 ms lockout; in the Paused dock, Resume sits where STOP was and End… where Pause was; STOP is never locked out.
- [ ] AC3 *[auto]*: Paused is entered only after stopped telemetry; an Unknown pause Stop shows "Couldn't confirm" with no Resume; after process death a Paused session recovers as Paused.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
