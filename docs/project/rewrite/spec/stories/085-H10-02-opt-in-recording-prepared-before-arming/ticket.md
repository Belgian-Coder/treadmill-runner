---
id: H10-02
epic: H10
priority: P1
phase: 2. Depth
tags: []
---

# H10-02 — Opt-in recording prepared before arming (6.2 rules).

## Goal
Opt-in recording prepared before arming (6.2 rules).

## Context
- Epic: H10 — Polar H10
- Priority note: P1
- Spec:
  - `docs/spec/10-polar-h10.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: an existing active recording is returned unchanged; replacement needs the same confirmed ID.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
