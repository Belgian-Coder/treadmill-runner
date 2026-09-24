---
id: DEV-04
epic: DEV
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui]
---

# DEV-04 — As a runner, I see live device state, signal and battery (phone and web).

## Goal
As a runner, I see live device state, signal and battery (phone and web).

## Context
- Epic: DEV — Devices
- Priority note: P0
- Spec:
  - `docs/spec/08-ftms-and-treadmill.md`
  - `docs/spec/10-polar-h10.md`
  - `docs/spec/00-plan.md#6-device-integration`

## Acceptance criteria
- [ ] AC1 *[auto]*: chips update within 1 s.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
