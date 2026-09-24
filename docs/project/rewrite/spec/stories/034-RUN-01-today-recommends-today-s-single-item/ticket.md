---
id: RUN-01
epic: RUN
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui]
---

# RUN-01 — Today recommends: today's single item, then today's alternatives (explicit choice), then t

## Goal
Today recommends: today's single item, then today's alternatives (explicit choice), then the next plan item, then Manual.

## Context
- Epic: RUN — Live run and control
- Priority note: P0
- Spec:
- `docs/spec/05-sessions-and-recording.md`
- `docs/spec/09-safety-and-command-contract.md`
- `docs/spec/02-workouts.md`
- `docs/spec/00-plan.md#96-run-screen-phone-native-reference-411--914-dp-portrait`

## Acceptance criteria
- [ ] AC1 *[auto]*: order tests; exactly one primary button.

## Mockups
```
┌──────────────────────────────┐
│ Marc ▾            ● Omega ● H10│
│ TODAY                        │
│ ┌──────────────────────────┐ │
│ │ Week 12 · Tempo 30 min   │ │
│ │ 5×(3′ 9.5 / 2′ 7.0) 2%   │ │
│ │ [      Start setup     ] │ │
│ └──────────────────────────┘ │
│ Choose another ›             │
│ Last run: Tue 5.1 km 32:10   │
└──────────────────────────────┘
```
Refine against design guide (plan section 9).

## Screens to capture during validation
today-recommended, today-alternatives

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
