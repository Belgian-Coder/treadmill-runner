---
id: DLV-06
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-06 — As the owner, feature flags can be switched off from Admin or through a manifest, without 

## Goal
As the owner, feature flags can be switched off from Admin or through a manifest, without a new build.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto]*: a disabling flag takes effect within 5 s and suspends safely; other flag changes wait for the next Arm.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
