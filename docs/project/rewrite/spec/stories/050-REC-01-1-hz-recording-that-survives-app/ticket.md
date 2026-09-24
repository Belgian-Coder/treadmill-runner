---
id: REC-01
epic: REC
priority: P0
phase: 1. Run MVP (offline)
tags: []
---

# REC-01 — 1 Hz recording that survives app death.

## Goal
1 Hz recording that survives app death.

## Context
- Epic: REC — Recording, history, analytics
- Priority note: P0
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/07-exports-and-backup.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: at most 1 s of samples lost after a random kill.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
