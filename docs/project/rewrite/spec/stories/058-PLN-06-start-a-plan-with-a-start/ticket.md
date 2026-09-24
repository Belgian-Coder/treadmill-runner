---
id: PLN-06
epic: PLN
priority: P0
phase: 1. Run MVP (offline)
tags: []
---

# PLN-06 — Start a plan with a start date and weekdays; clear upcoming items.

## Goal
Start a plan with a start date and weekdays; clear upcoming items.

## Context
- Epic: PLN — Plans and calendar
- Priority note: P0
- Spec:
  - `docs/spec/04-calendar-and-plans.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: the plan projection of `docs/spec/04-calendar-and-plans.md` §5.3 (tests P14–P19) and clear upcoming (S9–S10).

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
