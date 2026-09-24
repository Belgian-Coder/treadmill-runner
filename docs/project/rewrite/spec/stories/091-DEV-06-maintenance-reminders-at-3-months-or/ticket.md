---
id: DEV-06
epic: DEV
priority: P1
phase: 2. Depth
tags: [safety]
---

# DEV-06 — Maintenance reminders at 3 months or 241 km after a baseline (Simulator and SystemTest exc

## Goal
Maintenance reminders at 3 months or 241 km after a baseline (Simulator and SystemTest excluded).

## Context
- Epic: DEV — Devices
- Priority note: P1
- Spec:
  - `docs/spec/08-ftms-and-treadmill.md`
  - `docs/spec/10-polar-h10.md`
  - `docs/spec/00-plan.md#6-device-integration`

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
