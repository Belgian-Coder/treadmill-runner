---
id: FND-01
epic: FND
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# FND-01 — As a developer, the Gradle project has the modules of 4.1, convention plugins, a version c

## Goal
As a developer, the Gradle project has the modules of 4.1, convention plugins, a version catalog, detekt, ktlint and Lint.

## Context
- Epic: FND — Project foundation (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#3-technology-choices-all-kotlin`
  - `docs/spec/00-plan.md#4-architecture`
  - `docs/spec/00-plan.md#11-validation-strategy`

## Acceptance criteria
- [ ] AC1 *[auto]*: `./gradlew check` runs lint, unit, property, scenario, Robolectric and Ktor tests.
- [ ] AC2 *[auto]*: architecture tests fail on forbidden dependencies or on a command path outside the coordinator API.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
