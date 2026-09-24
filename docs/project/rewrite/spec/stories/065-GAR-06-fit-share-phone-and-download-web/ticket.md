---
id: GAR-06
epic: GAR
priority: P0
phase: 1. Run MVP (offline)
tags: [ui]
---

# GAR-06 — FIT share (phone) and download (web) for every session.

## Goal
FIT share (phone) and download (web) for every session.

## Context
- Epic: GAR — Garmin
- Priority note: P0
- Spec:
  - `docs/spec/11-garmin.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: a valid FIT (FIT SDK validator).

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
