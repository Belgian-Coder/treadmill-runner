---
id: REC-06
epic: REC
priority: P1
phase: 2. Depth
tags: [ui]
---

# REC-06 — Delete with preview.

## Goal
Delete with preview.

## Context
- Epic: REC — Recording, history, analytics
- Priority note: P1
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/07-exports-and-backup.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: refused while a Garmin job is pending, in flight or unknown; plan recompute.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
