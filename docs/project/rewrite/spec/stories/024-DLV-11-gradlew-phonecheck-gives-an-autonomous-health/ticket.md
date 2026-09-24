---
id: DLV-11
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-11 — As the owner or an automated agent, `./gradlew phoneCheck` gives an autonomous health repo

## Goal
As the owner or an automated agent, `./gradlew phoneCheck` gives an autonomous health report with screenshots.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1: the report contains the app screenshot (web API) and, when ADB is available, a full-screen screenshot.
- [ ] AC2: it includes the version, health, state inspectors, crashes since the last check, backup status, permissions, services and thermal state.
- [ ] AC3: it exits non-zero on any problem, and runs automatically after `deployToPhone`.
- [ ] AC4: without ADB it still completes using only the web API, and says so.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
