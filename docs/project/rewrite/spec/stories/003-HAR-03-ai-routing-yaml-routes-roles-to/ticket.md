---
id: HAR-03
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-03 — As the owner, `ai/routing.yaml` routes roles to Anthropic and OpenAI models (Opus 5.5, GPT

## Goal
As the owner, `ai/routing.yaml` routes roles to Anthropic and OpenAI models (Opus 5.5, GPT 6 Sol, GPT 6 Luna, with cheaper alternates), with role prompts in `ai/prompts/`.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
- `docs/spec/00-plan.md#12-ai-harness-and-story-workflow`

## Acceptance criteria
- [ ] AC1: the routing validates against a small schema (`./gradlew aiContext` checks it); planner and reviewer differ in provider for `safety` stories.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
