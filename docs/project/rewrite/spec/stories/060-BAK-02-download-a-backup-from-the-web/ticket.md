---
id: BAK-02
epic: BAK
priority: P0
phase: 1. Run MVP (offline)
tags: [ui]
---

# BAK-02 — Download a backup from the web UI (admin), encrypted by default.

## Goal
Download a backup from the web UI (admin), encrypted by default.

## Context
- Epic: BAK — Backup, restore, importing old runs
- Priority note: P0
- Spec:
- `docs/spec/07-exports-and-backup.md`
- `docs/spec/01-data-model.md`
- `docs/spec/00-plan.md#73-backup-strategy-file-based-external-device`

## Acceptance criteria
- [ ] AC1 *[auto]*: the downloaded `.trb2` verifies (manifest hashes); an encrypted bundle fails with the wrong passphrase.

## Mockups
<Planner: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Planner: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
