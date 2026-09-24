---
id: BAK-05
epic: BAK
priority: P0
phase: 1. Run MVP (offline)
tags: [hw]
---

# BAK-05 — As the owner, I import my old runs (session JSON exports or the `.trb` backup's runs) and 

## Goal
As the owner, I import my old runs (session JSON exports or the `.trb` backup's runs) and can export and re-import runs in the same structure.

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
  - `docs/spec/07-exports-and-backup.md`
  - `docs/spec/01-data-model.md`
  - `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto]*: the golden session exports in `docs/spec/data/exports/` import without loss (samples, events, debrief, IDs); re-exporting gives an equivalent document per `docs/spec/07-exports-and-backup.md`.
- [ ] AC2 *[hw]*: the owner's real history imports with matching session count and totals.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
