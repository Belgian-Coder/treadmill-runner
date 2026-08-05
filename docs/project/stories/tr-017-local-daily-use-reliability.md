---
title: TR-017 Local daily-use reliability and workout reuse
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-05
---

# TR-017: Local daily-use reliability and workout reuse

## Story

As one of the two household runners, I want the local app to reuse and import training plans quickly, remain visible during a run, reconnect predictably, and protect its history so daily use does not require technical intervention.

## Acceptance criteria

- Selecting a runner shows recent completed structured workouts as one-tap **Run again** choices; manual runs are excluded and the exact immutable revision is reused.
- A treadmill-workout v4 ZIP can be previewed and atomically imported as a training plan only after bounded archive, manifest, hash, profile, XML, and exactly-one-variant validation.
- The live dashboard requests a browser Screen Wake Lock while a run is prepared or active, exposes its real state, reacquires it after returning to the foreground, and releases it after the run or navigation.
- Operations generates a QR code locally from a configured server name or private-LAN listener address. It never derives a shared URL from the request Host header or calls an external QR service.
- BLE telemetry uses bounded reconnect backoff, reacts to native disconnects and silent telemetry, always creates a new connection generation, and never retries or resumes treadmill commands.
- Devices exposes a sanitized, local 1–90 day outage/recovery report and optional HR battery percentage. Missing battery support never faults heart-rate telemetry.
- The database receives startup and periodic idle integrity checks, bounded safe maintenance, and verified last-known-good backups. Unresolved corruption is visible and never triggers destructive automatic repair.
- Populated desktop and iPhone screenshots and deterministic tests cover the added flows without treadmill motion or external service calls.

## Boundaries

- `Y:\Backups\AI\Skills\treadmill-workout` is read-only provenance. TreadmillRunner neither edits, copies, nor executes the skill; it imports the skill's v4 output.
- Automatic database fixes mean only passive WAL checkpointing, `PRAGMA optimize`, stale application-temp cleanup, and verified backup rotation. No `.recover`, row deletion, record rewriting, or database replacement occurs automatically.
- HR battery is best-effort Bluetooth Battery Service telemetry. A sensor may legitimately report no value.
- No treadmill command or hardware commissioning is part of this story.

## Operator reference

See [Local reliability, access, and generated workout sets](../local-daily-use-reliability.md).
