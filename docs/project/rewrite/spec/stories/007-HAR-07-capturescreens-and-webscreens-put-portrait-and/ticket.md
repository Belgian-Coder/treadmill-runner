---
id: HAR-07
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: [hw]
---

# HAR-07 — As the UX validator, `captureScreens` and `webScreens` put portrait and landscape phone sc

## Goal
As the UX validator, `captureScreens` and `webScreens` put portrait and landscape phone screenshots (and web screenshots) into the story's `validation/` folder, and I record the review in `validation.md`.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1 *[hw]*: for a pilot UI story, screenshots at the phone's native resolution and density exist for both orientations; the review lists issues against section 9; the user guide reuses the screenshots.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
