---
id: DLV-01
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-01 — As the owner, `./gradlew release` builds and signs locally, and uploads the APK and manife

## Goal
As the owner, `./gradlew release` builds and signs locally, and uploads the APK and manifest to a GitHub Release and/or the phone.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto]*: the manifest verifies with the public key; tampering with any byte fails verification.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
