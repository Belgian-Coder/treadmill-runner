---
id: RUN-05
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui]
---

# RUN-05 — STOP is always visible; the Stop sheet choices.

## Goal
STOP is always visible; the Stop sheet choices.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/09-safety-and-command-contract.md`
  - `docs/spec/02-workouts.md`
  - `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: End only after a confirmed stop, or the no-telemetry path.
- [ ] AC2 *[auto]*: Discard persists an H10 cleanup job first; Reset never starts motion.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
