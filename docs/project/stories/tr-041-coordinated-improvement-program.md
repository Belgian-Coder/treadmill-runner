---
title: US-TR-041 Coordinated TreadmillRunner improvement program
type: story
status: software-implemented
owner: project
audience: agent-and-developer
updated: 2026-08-23
---

# US-TR-041 — Coordinated TreadmillRunner improvement program

## Outcome

Carry the device-correctness, session durability, Metric-only product, export, Garmin evidence, reusable UI, provenance, and iPhone installation improvements through the repository while preserving the completed Lighthouse shell/startup/performance work. The gateway remains the sole session, persistence, Bluetooth, and command authority.

## Implemented boundaries

- Heart-rate telemetry carries profile-scoped source, contact, quality, observation time, and freshness. Invalid, stale, unsupported, or no-contact readings are unavailable to automation and persistence. Speed and incline age independently; omitted fields do not refresh a prior value, and implausible telemetry is a protocol/device fault.
- Omega framing is bounded and declared-length aware. Generic FTMS exposes standard read-only telemetry only. Hardware commands remain on the existing serialized, high-priority path and require the relevant fresh field.
- Session transitions release locks before immutable, generation-guarded persistence and fan-out effects. One-second sample/checkpoint work uses a serialized writer, SignalR delivery is latest-only and bounded, terminal operation receipts are session-scoped with 90-day retention, and reconnect/readiness/integrity policies remain bounded and explicit.
- Terminal sessions accept an optional RPE from 1 through 10 and a note up to 1,000 characters, both editable later. Profiles expose display style, primary metrics, cue toggles, volume, and preview. History exposes weekly/monthly distance, duration, and run-count goals with progress and remaining values.
- The public product is Metric-only. `UnitSystem` remains in contracts only as the constant `Metric`; profile writes accept no other value and the reviewed migration normalizes existing rows before enforcing the constraint.
- History exports immutable TCX Activity and versioned full-resolution native JSON. Immutable workout revisions export FIT Workout alongside existing CSV/FIT Activity downloads.
- Garmin History surfaces bounded reconciliation evidence and review-required states. Official Training API publishing remains disabled until an approved adapter, contract, and credentials are configured; proprietary payloads are never guessed.
- WalkingPad source/hash/regeneration provenance is documented. Affected pages incrementally reuse typed API, dialog, weekday-selector, and chart-projection components. The Apple touch icon is a complete opaque 180×180 TreadmillRunner asset.

## Acceptance and evidence

Feature-focused suites cover telemetry validity, command freshness, framing, session effects, persistence/recovery, receipts, migration, Metric-only contracts, debrief/preferences/goals, exports, Garmin review/setup, components, charts, and icon delivery. Focused browser acceptance passed for all six goal cards, profile-owned display/cue persistence and preview, and saved/edited debriefs with recommendation refresh.

The post-handoff Home audit retained 100 accessibility, best-practices, and SEO scores. Mobile retained the 60 performance baseline (FCP 1.18 s, LCP 1.75 s, TBT 10.59 s, TTI 25.06 s); desktop measured 64 versus the prior 63 (FCP 0.32 s, LCP 0.42 s, TBT 1.61 s, TTI 4.95 s). The valid HTML/JSON reports are under `artifacts/performance/lighthouse-20260823-roadmap`; the Lighthouse CLI reported a Windows temporary-directory cleanup error only after each report had been written.

The repository-required full gate ran exactly once. Its Release build completed with 0 warnings and 0 errors, and deterministic validation passed 339/339. The browser phase stopped on one `/control` screenshot-gallery assertion because the simulator fixture allowed its initial heart-rate observation to become stale before the phone assertion. The production freshness rule was retained: stale heart rate becomes unavailable and cannot drive automation or persistence. The fixture now submits a fresh simulated observation at the point it needs to render the ready state.

After that correction, the focused Core contract check passed 1/1, the focused stale-heart-rate integration check passed 1/1, and the exact `/control` Playwright case passed 1/1 (`artifacts/test-results/browser-20260823-152409-focused.trx`). The full gate was not run a second time, so its original browser result remains the authoritative broad-gate disposition. This source story records no release, deployment, installation, backup-target configuration, physical-equipment movement, or physical acceptance.

## Out of scope

Sensitive-data boundary work, active-session runner-identity locking, new live workout or calendar-completion guidance, alternate unit modes or conversions, optional watch/health ecosystems, backup-destination configuration, guessed Garmin payloads, and release/deployment/installation remain out of scope.

## References

- [Live-session authority and recovery](../live-session.md)
- [Garmin integrations and setup states](../garmin-connect.md)
- [WalkingPad provenance and regeneration](../walkingpad-plan-provenance.md)
- [US-TR-041 workflow run](../../../automations/user-story-workflow/runs/US-TR-041/REPORT.md)
