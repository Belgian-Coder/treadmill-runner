---
id: DLV-09
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-09 — As a developer, wireless ADB with scrcpy works from the start, and the debuggable internal

## Goal
As a developer, wireless ADB with scrcpy works from the start, and the debuggable internal variant has its own applicationId.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1: from the laptop, scrcpy shows and controls the full phone screen.
- [ ] AC2: after a reboot, the documented re-enable steps (on the phone or via USB) restore it.
- [ ] AC3: works alongside self-updating; neither depends on the other.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
