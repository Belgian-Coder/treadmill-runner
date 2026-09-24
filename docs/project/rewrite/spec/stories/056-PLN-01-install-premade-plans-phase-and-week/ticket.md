---
id: PLN-01
epic: PLN
priority: P0
phase: 1. Run MVP (offline)
tags: []
---

# PLN-01 — Install premade plans; phase and week grouping.

## Goal
Install premade plans; phase and week grouping.

## Context
- Epic: PLN — Plans and calendar
- Priority note: P0
- Spec:
  - `docs/spec/04-calendar-and-plans.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: idempotent; 16 templates, including WalkingPad (174 slots, 260 variants).

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
