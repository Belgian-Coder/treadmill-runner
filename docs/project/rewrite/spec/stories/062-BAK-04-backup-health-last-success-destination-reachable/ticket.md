---
id: BAK-04
epic: BAK
priority: P0
phase: 1. Run MVP (offline)
tags: []
---

# BAK-04 — Backup health (last success, destination reachable, free space) is a persistent state.

## Goal
Backup health (last success, destination reachable, free space) is a persistent state.

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
  - `docs/spec/07-exports-and-backup.md`
  - `docs/spec/01-data-model.md`
  - `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto]*: the state is raised per destination when it is missing or unreachable, or the last success is older than 48 h.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
