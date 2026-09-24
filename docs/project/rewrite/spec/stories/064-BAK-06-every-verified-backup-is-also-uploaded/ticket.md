---
id: BAK-06
epic: BAK
priority: P0
phase: 1. Run MVP (offline)
tags: [hw]
---

# BAK-06 — As the owner, every verified backup is also uploaded to my NAS share over SMB, and I can r

## Goal
As the owner, every verified backup is also uploaded to my NAS share over SMB, and I can restore from it.

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
- `docs/spec/07-exports-and-backup.md`
- `docs/spec/01-data-model.md`
- `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto, phone .e2e]*: an upload to the NAS `test/` folder (SMB3, signing and encryption required): atomic upload (tmp, read-back hash, rename without replace), retention of own files only, restore listing; wrong credentials give a clear error. It also catches the Android security-provider/MD4 issue.
- [ ] AC2 *[auto]*: no upload starts while a session is non-terminal; an upload in progress is cancelled on Arm.
- [ ] AC3 *[hw]*: HW-14.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
