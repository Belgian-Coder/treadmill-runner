---
id: REC-07
epic: REC
priority: P0
phase: 1. Run MVP (offline)
tags: [ui, hw]
---

# REC-07 — Export FIT, TCX, CSV or JSON (share on the phone, download on the web).

## Goal
Export FIT, TCX, CSV or JSON (share on the phone, download on the web).

## Context
- Epic: REC — Recording, history, analytics
- Priority note: P0
- Spec:
- `docs/spec/05-sessions-and-recording.md`
- `docs/spec/07-exports-and-backup.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: decoded-record equality with the C# golden files; the FIT SDK validator passes.
- [ ] AC2 *[hw]*: a Garmin Connect import once per release.

## Mockups
<Planner: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Planner: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
