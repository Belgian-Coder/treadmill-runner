---
id: RUN-14
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui, hw]
---

# RUN-14 — Keep screen on for non-terminal sessions.

## Goal
Keep screen on for non-terminal sessions.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/09-safety-and-command-contract.md`
  - `docs/spec/02-workouts.md`
  - `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: the flag lifecycle.
- [ ] AC2 *[hw]*: 60 min with no dimming.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
