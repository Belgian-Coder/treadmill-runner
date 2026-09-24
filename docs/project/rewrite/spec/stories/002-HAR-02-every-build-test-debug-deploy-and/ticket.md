---
id: HAR-02
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-02 — As an agent, every build, test, debug, deploy and validation action is a deterministic Gra

## Goal
As an agent, every build, test, debug, deploy and validation action is a deterministic Gradle task that writes `build/ai/<task>.json` (12.2).

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1 *[auto]*: each task in 12.2 exists, is repeatable, and writes a JSON result with status and failures as file:line.
- [ ] AC2 *[auto]*: a deliberately failing test appears in `ciFast.json` with its file and line.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
