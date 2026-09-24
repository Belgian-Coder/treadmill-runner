---
id: HAR-01
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-01 — As the owner, the new repository has `AGENTS.md`, `CLAUDE.md`, `ai/project-context.md` and

## Goal
As the owner, the new repository has `AGENTS.md`, `CLAUDE.md`, `ai/project-context.md` and the spec pack in `docs/spec/`, so any agent starts with the same context.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow`

## Acceptance criteria
- [ ] AC1: an agent given only `AGENTS.md` can find the project context, the spec index and the story index in ≤ 3 reads.
- [ ] AC2: the project context states the non-negotiable rules (safety contract, phone-only control, runs-only compatibility, simplicity).

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
