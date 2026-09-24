---
id: HAR-05
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-05 — As the owner, `./gradlew newStory` scaffolds story folders, `./gradlew newAdr` creates ADR

## Goal
As the owner, `./gradlew newStory` scaffolds story folders, `./gradlew newAdr` creates ADRs, and `./gradlew storyCheck` enforces the stage requirements, including the documentation requirements for `done` (12.7).

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1 *[auto]*: storyCheck refuses "implementing" with blocking open questions, and refuses "done" without validation evidence, decision-register rows, or a user-guide update for a `ui` story.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
