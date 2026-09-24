---
id: FND-02
epic: FND
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# FND-02 — As a developer, the test harness exists at every level with one example test each: unit, p

## Goal
As a developer, the test harness exists at every level with one example test each: unit, property, scenario, Robolectric, Ktor route, Compose E2E on the phone (`.e2e` variant), Playwright web E2E, Roborazzi screenshot.

## Context
- Epic: FND — Project foundation (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#3-technology-choices-all-kotlin`
  - `docs/spec/00-plan.md#4-architecture`
  - `docs/spec/00-plan.md#11-validation-strategy`

## Acceptance criteria
- [ ] AC1 *[auto]*: `./gradlew ciFast` (pre-push hook) and `./gradlew ciNightly` (before release) run the right sets locally and publish HTML reports; `release` refuses without a passing `ciNightly` for the commit.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
