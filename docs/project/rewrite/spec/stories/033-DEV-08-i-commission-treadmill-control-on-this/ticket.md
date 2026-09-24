---
id: DEV-08
epic: DEV
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, hw]
---

# DEV-08 — As the owner, I commission treadmill control on this phone in approved stages, with saniti

## Goal
As the owner, I commission treadmill control on this phone in approved stages, with sanitized evidence.

## Context
- Epic: DEV — Devices
- Priority note: P0, gate for control
- Spec:
- `docs/spec/08-ftms-and-treadmill.md`
- `docs/spec/10-polar-h10.md`
- `docs/spec/00-plan.md#6-device-integration`

## Acceptance criteria
- [ ] AC1 *[auto]*: controls stay disabled until all stages are approved; approval is stored per model, firmware and host stack.
- [ ] AC2 *[hw]*: HW-01.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
