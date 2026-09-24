---
id: FND-04
epic: FND
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# FND-04 — As a developer, `ui-design` has the tokens and components (Compose) and the generated CSS.

## Goal
As a developer, `ui-design` has the tokens and components (Compose) and the generated CSS.

## Context
- Epic: FND — Project foundation (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#3-technology-choices-all-kotlin`
  - `docs/spec/00-plan.md#4-architecture`
  - `docs/spec/00-plan.md#11-validation-strategy`

## Acceptance criteria
- [ ] AC1 *[auto]*: the token → CSS generation is checked in the build; component screenshots in all themes.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
