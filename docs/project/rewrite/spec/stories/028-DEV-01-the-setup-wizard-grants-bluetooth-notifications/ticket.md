---
id: DEV-01
epic: DEV
priority: P0
phase: 1. Run MVP (offline)
tags: [safety, ui, hw]
---

# DEV-01 — As a runner, the setup wizard grants Bluetooth, notifications, battery exemption, CDM and 

## Goal
As a runner, the setup wizard grants Bluetooth, notifications, battery exemption, CDM and a backup folder, and explains each.

## Context
- Epic: DEV — Devices
- Priority note: P0
- Spec:
  - `docs/spec/08-ftms-and-treadmill.md`
  - `docs/spec/10-polar-h10.md`
  - `docs/spec/00-plan.md#6-device-integration`

## Acceptance criteria
- [ ] AC1 *[auto]*: Finish is blocked until BLE permissions and a backup folder are set.
- [ ] AC2 *[hw]*: 60 min with the screen off under `dumpsys deviceidle force-idle`: no sample gap over 2 s.
- [ ] AC3 *[hw]*: restart recovery works with only the battery exemption, and with only CDM.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
