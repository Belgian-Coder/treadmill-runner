---
id: DLV-05
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: [hw]
---

# DLV-05 — As the owner, a crash loop after an update puts the app in safe mode (web server, Diagnost

## Goal
As the owner, a crash loop after an update puts the app in safe mode (web server, Diagnostics, Updates only), rejects that version, and lets me push a fix remotely.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto, phone .e2e]*: E2E with a deliberately crashing build: safe mode after 2 starts without healthy; Diagnostics reachable; the next pushed build restores normal mode with no tap.
- [ ] AC2 *[hw]*: HW-08.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
