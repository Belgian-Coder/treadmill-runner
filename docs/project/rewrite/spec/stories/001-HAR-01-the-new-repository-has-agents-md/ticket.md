---
id: HAR-01
epic: HAR
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# HAR-01 — As the owner, the new repository has `AGENTS.md` (no `CLAUDE.md`), `ai/project-context.md`

## Goal
As the owner, the new repository has `AGENTS.md` (no `CLAUDE.md`), `ai/project-context.md`, the spec pack in `docs/spec/`, and the central `docs/` folder with seed ADRs, `decisions.md`, and user-guide and dev-guide skeletons.

## Context
- Epic: HAR — AI harness and project setup (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#12-ai-harness-and-story-workflow` (12.1–12.7)

## Acceptance criteria
- [ ] AC1: an agent given only `AGENTS.md` finds the project context, maps, spec, ADRs and story folder in ≤ 3 reads.
- [ ] AC2: the seed ADRs 0001–0006 exist, and `docs/decisions.md` lists every decision in plan §16 with a link.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
