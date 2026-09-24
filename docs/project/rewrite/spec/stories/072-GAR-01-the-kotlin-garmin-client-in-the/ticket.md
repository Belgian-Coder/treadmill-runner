---
id: GAR-01
epic: GAR
priority: P1
phase: 2. Depth
tags: []
---

# GAR-01 — The Kotlin Garmin client **in the phone app** uploads or matches completed Hardware sessio

## Goal
The Kotlin Garmin client **in the phone app** uploads or matches completed Hardware sessions from the phone (feature-flagged).

## Context
- Epic: GAR — Garmin
- Priority note: P1
- Spec:
  - `docs/spec/11-garmin.md`

## Acceptance criteria
- [ ] AC1 *[auto]*: ported matcher and worker tests, plus the contract examples in `docs/spec/data/garmin/`. `PreferWatch` default, `MergeAndReplace`, the enable watermark, the 5-minute wait, no automatic retry of Unknown or ReviewRequired.
- [ ] AC2 *[auto]*: tokens encrypted with a Keystore key.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
