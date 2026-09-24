---
id: GAR-00
epic: GAR
priority: P1
phase: 2. Depth
tags: [hw]
---

# GAR-00 — As a developer, I prove Garmin login, MFA, token refresh and one upload from the phone (Kt

## Goal
As a developer, I prove Garmin login, MFA, token refresh and one upload from the phone (Ktor on OkHttp), porting a pinned `garminconnect` version.

## Context
- Epic: GAR — Garmin
- Priority note: P1, spike
- Spec:
  - `docs/spec/11-garmin.md`

## Acceptance criteria
- [ ] AC1 *[hw]*: works against a test account; a failed login is never retried automatically.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
