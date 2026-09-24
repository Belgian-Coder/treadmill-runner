---
id: DLV-04
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-04 — As the owner, updates install only when idle, after a verified backup, and are followed by

## Goal
As the owner, updates install only when idle, after a verified backup, and are followed by a health check reported in the web UI.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto]*: no install while a session is non-terminal or an unfetched H10 recording exists.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
