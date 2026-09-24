---
id: BAK-01
epic: BAK
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# BAK-01 — Automatic verified backups (after each session, daily, before updates and restores), reten

## Goal
Automatic verified backups (after each session, daily, before updates and restores), retention 2–60 (default 14), copied to the external folder (microSD or USB).

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
  - `docs/spec/07-exports-and-backup.md`
  - `docs/spec/01-data-model.md`
  - `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto]*: `VACUUM INTO` plus integrity check plus receipt; the external copy exists; retention enforced.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
