---
id: WEB-02
epic: WEB
priority: P0
phase: 0b. Harness, foundation and delivery
tags: [ui]
---

# WEB-02 — As any user on the home Wi-Fi, I open the address and use the web UI without logging in. A

## Goal
As any user on the home Wi-Fi, I open the address and use the web UI without logging in. Admin actions ask for the admin passphrase set on the phone.

## Context
- Epic: WEB — Web interface
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#72-web-interface`
  - `docs/spec/00-plan.md#97-web-layout-guide`

## Acceptance criteria
- [ ] AC1 *[auto]*: admin routes return 401 without a valid admin session; a wrong passphrase is rate-limited; the session expires after 30 minutes.
- [ ] AC2 *[auto]*: architecture test: the web module cannot reach the treadmill command API.

## Mockups
<Orchestrator: add an ASCII wireframe per screen state, portrait and landscape, following plan sections 9 and 10.>

## Screens to capture during validation
<Orchestrator: list `<screen>-<state>` scenarios>

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
