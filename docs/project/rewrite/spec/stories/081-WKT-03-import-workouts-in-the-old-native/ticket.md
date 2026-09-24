---
id: WKT-03
epic: WKT
priority: P1
phase: 2. Depth
tags: [ui]
---

# WKT-03 — Import workouts in the old native JSON (P1), and optionally QDomyos XML, FIT workout and v4 bundles

## Goal
Import workouts in the old native JSON (P1), and optionally QDomyos XML, FIT workout and v4 bundles (P2). Each is **converted on import** into the primary format (native JSON schema v1), with explicit loss warnings, and never guesses (`docs/spec/03-import-export-formats.md` §6).

## Context
- Epic: WKT — Workouts
- Priority note: P1 (native JSON); P2 for QDomyos XML, FIT workout and v4 bundles
- Spec:
  - `docs/spec/02-workouts.md`
  - `docs/spec/03-import-export-formats.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: the native round trip is lossless; each lossy conversion emits its documented warning (for example `fit.incline-not-supported`).

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
