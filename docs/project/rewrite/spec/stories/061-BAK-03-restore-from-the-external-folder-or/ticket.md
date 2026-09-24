---
id: BAK-03
epic: BAK
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui, hw]
---

# BAK-03 — Restore from the external folder or a web upload, with a preview and a safety backup first

## Goal
Restore from the external folder or a web upload, with a preview and a safety backup first.

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
- `docs/spec/07-exports-and-backup.md`
- `docs/spec/01-data-model.md`
- `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto]*: a newer schema is refused; an older schema migrates; row counts match after restore.
- [ ] AC2 *[hw]*: HW-13.

## Mockups
```
┌───────────────────────────────┐
│ Restore backup                │
│ File: trb2-2026-09-24.trb2    │
│ Created 24 Sep 07:12 · v1.3.0 │
│ Runs 412 · 2023-01 → 2026-09  │
│ Replaces all current data     │
│ A safety backup is made first │
│ [Cancel]        [Restore]     │
└───────────────────────────────┘
```
Refine against design guide (plan section 9).

## Screens to capture during validation
restore-preview

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
