---
id: HAR-06
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-06 — As the owner, the automatic story pipeline (12.7) runs end to end: parallel Haiku searches

## Goal
As the owner, the automatic story pipeline (12.7) runs end to end: parallel Haiku searches, Opus plan, Sonnet packets with a continuously updated execution log, validation, docs (ADRs, decisions, user guide), map refresh, and the Opus final review.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1: the pilot story FND-01 completes the whole pipeline without manual steps other than answering blocking questions, and its folder passes `storyCheck`.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
