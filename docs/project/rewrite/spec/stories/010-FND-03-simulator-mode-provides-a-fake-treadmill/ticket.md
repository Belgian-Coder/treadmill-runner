---
id: FND-03
epic: FND
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# FND-03 — As a developer, Simulator mode provides a fake treadmill and HR (deterministic, scriptable

## Goal
As a developer, Simulator mode provides a fake treadmill and HR (deterministic, scriptable), used by E2E tests and available in Diagnostics.

## Context
- Epic: FND — Project foundation (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#3-technology-choices-all-kotlin`
  - `docs/spec/00-plan.md#4-architecture`
  - `docs/spec/00-plan.md#11-validation-strategy`

## Acceptance criteria
- [ ] AC1 *[auto]*: simulated sessions are excluded from totals, progression, maintenance, plans and Garmin.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
