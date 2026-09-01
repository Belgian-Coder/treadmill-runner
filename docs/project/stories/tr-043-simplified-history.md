---
title: TR-043 Simplified History and Garmin activity presentation
type: story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-09-01
---

# TR-043 — Simplified History and Garmin activity presentation

## Outcome

History now focuses on stored runs and useful comparisons. The unrequested weekly/monthly target editor and deterministic "What next?" adviser are absent, including their client-side save and recommendation requests. Session-detail cards now share the same page width instead of rendering the debrief as a narrower, visually disconnected panel.

## Garmin activity presentation

The session page describes the two guarded recovery outcomes directly:

- **Keep one Garmin activity** retains the verified combined activity and removes only proven duplicates.
- **Restore two Garmin activities** restores the original watch activity beside the verified TreadmillRunner activity.

Both choices state that local TreadmillRunner History remains unchanged and retain the existing confirmation step. Raw provider status, operation phase, timestamps, evidence, and identifiers remain available under collapsed **Technical details**.

## Boundaries

This is a presentation and client-request change. It does not migrate or delete stored goal/recommendation data, remove backend contracts, alter Garmin matching/recovery semantics, change the guarded deletion phases, release or install software, contact live Garmin, or operate a treadmill/BLE device.

## Acceptance

Focused Playwright coverage verifies target/adviser absence, zero recommendation traffic during debrief saves, equal detail-card widths at 1180x820 and 390x844, responsive overflow, plain-language recovery actions at 1180x820 and 390x844, collapsed diagnostics, confirmation, exact guarded command payloads, and the existing local-History invariant.

## References

- [Garmin integration and recovery](../garmin-connect.md)
- [TR-043 workflow run](../../../automations/user-story-workflow/runs/US-TR-043/REPORT.md)
