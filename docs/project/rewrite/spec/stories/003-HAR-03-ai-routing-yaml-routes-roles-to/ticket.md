---
id: HAR-03
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-03 — As the owner, `ai/routing.yaml` and the role cards in `ai/prompts/` implement 12.3: Opus 5

## Goal
As the owner, `ai/routing.yaml` and the role cards in `ai/prompts/` implement 12.3: Opus 5.5 medium orchestrates, Sonnet high implements, Opus 5.5 high validates UI and does the final review, and Haiku runs read-only parallel searches.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1: routing validates against a small schema; the searcher role has no write tools.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
