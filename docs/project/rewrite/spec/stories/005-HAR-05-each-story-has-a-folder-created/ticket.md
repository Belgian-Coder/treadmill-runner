---
id: HAR-05
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-05 — As the owner, each story has a folder created by `./gradlew newStory` from the templates, 

## Goal
As the owner, each story has a folder created by `./gradlew newStory` from the templates, and `./gradlew storyCheck` enforces the stages (12.4).

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow`

## Acceptance criteria
- [ ] AC1 *[auto]*: storyCheck refuses "implementing" with blocking open questions; refuses "done" without validation evidence.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
