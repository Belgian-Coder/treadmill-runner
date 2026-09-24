---
id: DLV-02
epic: DLV
priority: P0
phase: 0b. Harness, foundation and delivery
tags: []
---

# DLV-02 — As the owner, the app updates itself without a tap on the phone after the one-time provisi

## Goal
As the owner, the app updates itself without a tap on the phone after the one-time provisioning.

## Context
- Epic: DLV — Delivery: updates and remote debugging (first)
- Priority note: P0
- Spec:
  - `docs/spec/00-plan.md#8-delivery-build-remote-updates-remote-debugging`

## Acceptance criteria
- [ ] AC1 *[auto, phone .e2e]*: build N installed, N+1 pushed, installed silently when idle, restarted via `MY_PACKAGE_REPLACED`.
- [ ] AC2: `STATUS_PENDING_USER_ACTION` shows "needs one tap" on the web Updates page.

## Mockups
n/a

## Screens to capture during validation
n/a

## Out of scope
- Anything not needed for the acceptance criteria above (private use, keep it simple).

## Notes for the orchestrator
- Run the automatic story pipeline (plan 12.7): parallel read-only searches, `plan.md` from the template, Sonnet packets, validation, docs (ADRs, `docs/decisions.md`, user guide, dev guide), `aiMaps`, final review.
- Read only the linked spec sections and the maps.
