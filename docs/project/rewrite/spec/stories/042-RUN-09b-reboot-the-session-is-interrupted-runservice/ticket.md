---
id: RUN-09b
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety]
---

# RUN-09b — Reboot: the session is Interrupted; RunService does not auto-start; WebService does.

## Goal
Reboot: the session is Interrupted; RunService does not auto-start; WebService does.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
  - `docs/spec/05-sessions-and-recording.md`
  - `docs/spec/09-safety-and-command-contract.md`
  - `docs/spec/02-workouts.md`
  - `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
