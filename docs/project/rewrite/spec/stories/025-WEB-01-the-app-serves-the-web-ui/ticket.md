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
<Planner: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Planner: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the planner
- Read the linked spec sections only; create `plan.md` from the template, with packets sized for a cheaper executor.
