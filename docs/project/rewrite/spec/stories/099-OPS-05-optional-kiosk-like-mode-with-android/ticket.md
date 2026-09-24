---
id: OPS-05
epic: OPS
priority: P2
phase: 3. Reach
tags: []
---

# OPS-05 — Optional kiosk-like mode with Android app pinning (screen pinning), without Device Owner

## Goal
Optional kiosk-like mode with Android app pinning (screen pinning). **No Device Owner** (plan §16, decision 9). Because the screen lock is None/Swipe (plan 2.1), unpinning needs no PIN; pinning only prevents accidental navigation away from the app.

## Context
- Epic: OPS — Operations
- Priority note: P2
- Spec:
  - `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`
  - `docs/spec/00-plan.md#16-decisions`

## Acceptance criteria
- [ ] AC1: <to be defined by the planner from the goal and the linked spec>

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
