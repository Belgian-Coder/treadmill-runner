---
id: WEB-06
epic: WEB
priority: P0
phase: 1. Run MVP (offline)
tags: [ui]
---

# WEB-06 — As any user, the web UI meets the 9.7 layout guide and budgets.

## Goal
As any user, the web UI meets the 9.7 layout guide and budgets.

## Context
- Epic: WEB — Web interface
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#72-web-interface`
  - `docs/spec/00-plan.md#97-web-layout-guide`

## Acceptance criteria
- [ ] AC1 *[auto]*: Playwright at 375, 768 and 1280 px; axe clean; first paint < 1 s; JS ≤ 100 KB.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
