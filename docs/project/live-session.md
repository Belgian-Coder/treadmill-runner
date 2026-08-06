---
title: Authoritative Live Session
type: feature-guide
status: implemented-with-pending-hardware-evidence
owner: project
audience: agent-and-developer
updated: 2026-08-04
---

# Authoritative live session

TR-004 provides the authoritative simulator-backed daily running flow. TR-005 adds an explicitly selected read-only Omega Z telemetry source and simultaneous Polar H10 source through the same gateway-owned session coordinator. The gateway—not the browser—owns session state, workout progression, lease, sample cadence, events, and completion. Offline tests do not prove Windows Service BLE or real treadmill control.

[![Simulated live session diagram](diagrams/live-session-simulated-live-session.svg)](diagrams/live-session-simulated-live-session.svg)

Source: [Mermaid](diagrams/live-session-simulated-live-session.mmd)

[![Capability-gated command flow](diagrams/live-session-command-flow.svg)](diagrams/live-session-command-flow.svg)

Source: [Mermaid](diagrams/live-session-command-flow.mmd)

## Runner flow

1. Select a local profile and immutable workout revision.
2. Review gateway, database, simulated treadmill, and heart-rate readiness. HR is required only for an HR-target workout.
3. Take the single controller lease and arm the workout.
4. If the exact enrollment has hardware-verified FTMS Start capability, press and hold for three seconds to send one short-lived Start intent at the verified minimum speed (the owner reports `0.8 km/h`, pending range verification). Otherwise start from the physical console. In either case, three fresh moving treadmill samples advance the session; Development uses its simulator-only motion fixture.
5. Follow live measured, requested, and planned values. Speed and incline both use 10 as their default top tick (10 km/h and 10%). Either axis expands immediately, in whole-unit increments, only when the workout plan, requested target, or measured telemetry exceeds 10. Each retains its highest observed scale for the remainder of the run to avoid visual jumping. For timed workouts, cursor position is elapsed time divided by the immutable workout-plan duration. Distance and other open-ended runs use an expanding five-minute timeline window with one minute of lead time, so the cursor advances without being permanently pinned to the right edge. The browser plots bounded transient chart points; SQLite stores one sample per second.
6. Manual speed and incline overrides, Pause, Stop, and Start/Resume require a current lease and unique operation ID. Targets are normalized against the workout, profile, and verified hardware bounds; requested, accepted, and measured values remain distinct.
7. Complete the physical session, then optionally save RPE 1–10 and a note.
8. Review data-derived planned/requested/measured history, HR-zone duration, adherence, event counts, and current-week completed totals.

## Authority and recovery

- A lease heartbeat runs every five seconds and expires after fifteen seconds. It gates manual browser actions only.
- Browser loss never stops the gateway-owned session. Planned progression and eligible HR control continue at the gateway; reloading with the same browser holder ID idempotently reacquires its controller view.
- BLE loss preserves elapsed time and records an unobserved telemetry gap without fabricating distance or measurements. Two fresh stable samples from the same moving treadmill can reconcile the current elapsed target unless a console change, stale HR, protocol fault, or unknown result blocks it.
- Gateway startup restores tracking only from a bounded checkpoint for the same enrolled treadmill. Fresh movement must be confirmed within 30 seconds; all commands remain suspended until **Resume planned controls**. Invalid/unavailable recovery becomes `Interrupted` and never sends Start.
- Every treadmill request includes operation ID, lease/holder, and expected session version. The gateway adds session state, short expiry, and connection generation, serializes writes, consumes the operation before writing, and returns `Rejected`, `Confirmed`, or `Unknown`.
- `Confirmed` requires the matching FTMS response plus fresh measured telemetry. `Unknown` is persistent, suspends automation, instructs physical inspection, and is never blindly retried.
- Repeated manual operation IDs return the current result without creating another event.
- The exact selected workout revision, profile name and ID, profile HR-zone/controller snapshot, metric algorithm version, requested/measured values, samples, and events are persisted.

## Current limits

- The Development simulator reports a fixed HR value. An enrolled Polar H10 supplies real HR; Garmin is outside v1. The HR controller supports Shadow, Decrease only, Full, and Off, with configurable steps/cooldowns. Browser lease loss removes manual authority but does not suspend gateway-owned control; stale telemetry, unsafe reconnect, write uncertainty, pause, protocol fault, or manual speed override still suspends it.
- FTMS Start/Resume, Stop, Pause, speed, and incline software paths are implemented but each stays blocked unless the persisted exact-model evidence is `HardwareVerified` for that capability. The complete Omega Z sequence is prepared and remains physically unvalidated.
- Accelerated four-hour cadence and bounded chart-memory tests pass. An explicit soak persists and reads 14,400 one-second SQLite samples. In-process controller acceptance stays below 100 ms p95 and loopback browser telemetry stays below 500 ms p95. Formal household-Wi-Fi p95 measurement is not a deployment acceptance check; retained timestamps are diagnostic evidence if normal use feels delayed.
- Signed update check/stage/activate and helper rollback are implemented and deterministically tested. Real A→B activation and broken-C rollback on the Windows VM remain deployment evidence; publishing the Playwright host is not update evidence.
- Screen Wake Lock is not promised over the selected private-LAN HTTP deployment. Configure device Auto-Lock if a continuously visible display is required.

## Validation

- `eng/validate.ps1 -Configuration Release` runs locked restore, formatting/analyzers, architecture gates, build, and non-browser tests.
- `eng/playwright.ps1 -Configuration Release` exercises preflight, live movement, override markers, browser reclaim, debrief, history analytics, and phone/tablet/desktop screenshots.
- `eng/soak.ps1 -Configuration Release -Build` runs the explicit four-hour/14,400-sample SQLite proof outside the fast default suite.
- Accepted screenshots are generated locally under the ignored `validation/playwright/accepted` evidence directory.
