---
id: WEB-01
epic: WEB
priority: P0
phase: 0b. Harness, foundation and delivery
tags: [ui, hw]
---

# WEB-01 — As the owner, the app serves the web UI over plain HTTP on the Wi-Fi address and on localh

## Goal
As the owner, the app serves the web UI over plain HTTP on the Wi-Fi address and on localhost, from a `specialUse` foreground service that also starts at boot. There are no certificates and no per-device setup.

## Context
- Epic: WEB — Web interface
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#72-web-interface`
  - `docs/spec/00-plan.md#97-web-layout-guide`

## Acceptance criteria
- [ ] AC1 *[auto]*: the service restarts after process death; requests from outside loopback, Wi-Fi or an allow-listed VPN are rejected.
- [ ] AC2 *[hw]*: reachable within 60 s after an unattended reboot (screen lock None/Swipe).

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
