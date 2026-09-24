---
id: FND-06
epic: FND
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# FND-06 — As a developer, the local test rig of 11.0 exists: `ciFast` on the Windows VM, and `ciNigh

## Goal
As a developer, the local test rig of 11.0 exists: `ciFast` on the Windows VM, and `ciNightly` running device tests on the phone in the `.e2e` variant.

## Context
- Epic: FND — Project foundation (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#3-technology-choices-all-kotlin`
  - `docs/spec/00-plan.md#4-architecture`
  - `docs/spec/00-plan.md#11-validation-strategy`

## Acceptance criteria
- [ ] AC1: both commands run green with an example of each test level.
- [ ] AC2: `ciNightly` refuses to start while the real app has a non-terminal session.
- [ ] AC3: the `.e2e` variant's data and settings are isolated from the real app.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
