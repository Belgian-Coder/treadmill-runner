---
id: PLN-03
epic: PLN
priority: P1
phase: 2. Depth
tags: [ui]
---

# PLN-03 — Move, skip, restore, repeat and change days, each with a preview.

## Goal
Move, skip, restore, repeat and change days, each with a preview.

## Context
- Epic: PLN — Plans and calendar
- Priority note: P1
- Spec:
  - `docs/spec/04-calendar-and-plans.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: occupied dates block moves; repeat collision warnings; atomic apply.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
