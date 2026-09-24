---
id: DLV-07
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-07 — As the owner, the web Diagnostics console shows live logs (filterable, level changes at ru

## Goal
As the owner, the web Diagnostics console shows live logs (filterable, level changes at runtime), crash and ANR reports with `ApplicationExitInfo`, state inspectors, app-window screenshot and live view, and actions (reconnect, self-test, simulator run, diagnostics ZIP).

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto]*: a Ktor test per inspector.
- [ ] AC2 *[auto]*: SSE log stream delivers a new log line within 1 s.
- [ ] AC3 *[auto]*: a crash in a test build appears in the list after restart.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
