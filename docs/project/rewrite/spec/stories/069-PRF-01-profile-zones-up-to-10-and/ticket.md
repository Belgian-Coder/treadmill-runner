---
id: PRF-01
epic: PRF
priority: P0
phase: 1. Run MVP (offline)
tags: []
---

# PRF-01 — Profile, zones (up to 10) and HR controller settings within bounds (1.2).

## Goal
Profile, zones (up to 10) and HR controller settings within bounds (1.2).

## Context
- Epic: PRF — Profiles and settings
- Priority note: P0
- Spec:
  - `docs/spec/06-profiles-and-heart-rate.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: bounds.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
