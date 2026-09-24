---
id: DLV-03
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: [hw]
---

# DLV-03 — As the owner, I push an update from my laptop with `./gradlew deployToPhone` or the web Up

## Goal
As the owner, I push an update from my laptop with `./gradlew deployToPhone` or the web Updates page.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto]*: refused on a bad signature, SHA-256 mismatch, different certificate, lower `versionCode`, reused `sequence`, yanked or rejected version, or a schema outside the window.
- [ ] AC2 *[hw]*: HW-07.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
