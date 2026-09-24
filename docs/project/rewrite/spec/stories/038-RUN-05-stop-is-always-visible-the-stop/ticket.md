---
id: RUN-05
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui]
---

# RUN-05 — STOP is always visible; the Stop sheet choices.

## Goal
STOP is always visible; the Stop sheet choices.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
- `docs/spec/05-sessions-and-recording.md`
- `docs/spec/09-safety-and-command-contract.md`
- `docs/spec/02-workouts.md`
- `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: End only after a confirmed stop, or the no-telemetry path.
- [ ] AC2 *[auto]*: Discard persists an H10 cleanup job first; Reset never starts motion.

## Mockups
<Planner: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Planner: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
