---
id: WKT-02
epic: WKT
priority: P1
phase: 2. Depth
tags: [ui]
---

# WKT-02 — Editor (web); each save creates a revision.

## Goal
Editor (web); each save creates a revision.

## Context
- Epic: WKT — Workouts
- Priority note: P1
- Spec:
- `docs/spec/02-workouts.md`
- `docs/spec/03-import-export-formats.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: a revision hash is stable (sorted-key JSON, SHA-256); an unchanged save doesn't create a revision.
- [ ] AC2 *[auto]*: limits enforced (10,000 steps, depth 32, 12 h).

## Mockups
<Planner: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Planner: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
