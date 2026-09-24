---
id: REC-05
epic: REC
priority: P1
phase: 2. Depth
tags: []
---

# REC-05 — Compare sessions of the same revision.

## Goal
Compare sessions of the same revision.

## Context
- Epic: REC — Recording, history, analytics
- Priority note: P1
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/07-exports-and-backup.md`

## Acceptance criteria
- [ ] AC1: <to be defined by the planner from the goal and the linked spec>

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
