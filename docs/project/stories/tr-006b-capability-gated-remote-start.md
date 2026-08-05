---
title: TR-006B Capability-Gated Remote Start
type: user-story
status: implemented-hardware-pending
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# TR-006B — Capability-gated remote Start

As the runner standing at the treadmill, I want one clear Start control in the web UI so that I do not need to arm the workout in the browser and then reach for the console when the enrolled treadmill can safely start through its verified protocol.

## UX

- A prominent **Hold to start** action combines session arming with an explicit belt-start intent.
- The action is shown only when the enrolled adapter and firmware have `CanStartRemotely=true`; otherwise the same preflight uses the physical-console fallback and explains why.
- A short visual/audio countdown gives the runner time to cancel. The gateway starts the workout clock only after fresh telemetry confirms belt movement.

## Safety and protocol acceptance

- Start is a separate model/firmware capability; FTMS service presence or a successful GATT write is insufficient.
- The exact command, response, treadmill state transition, safety-key behavior, minimum start speed, repeated-command behavior, timeout, disconnect, and power-cycle behavior are captured and independently verified.
- The intent is single-use, short-lived, bound to the active control lease and current connection generation, and consumed before the write.
- Start is never queued across reconnect, automatically retried, replayed, restored after process/browser restart, or used for automatic resume.
- A stale page, expired operation, observer, recovering gateway, active session, moving belt, missing/stale telemetry, or unconfirmed device state cannot start the belt.
- Physical Stop and the safety key remain authoritative; manual console Start remains available.
- Omega Z remains `CanStartRemotely=false` until its own gate passes. Run 500 and Challenge Run are assessed independently.

Implementation is tracked in `automations/user-story-workflow/runs/US-TR-006B`. The owner approved the software work on 2026-08-03. Read-only Stage 2 established model `OMEGA Z`, firmware `V10.23.17`, the `0.8–20.0 km/h` reported range, and fresh stopped FTMS telemetry; exact-model live motion remains a separate Stop-first hardware gate.

## Implemented software boundary

- FTMS Request Control `00`, Start/Resume `07`, and Stop `08 01` are independently encoded and their response codes parsed.
- A separate Windows command connection is available only to the serialized treadmill command coordinator; read-only callers cannot write.
- The four-second Start/Stop intent is bound to operation ID, session ID/version/state, lease/holder, and connection generation. Start is consumed before the one Start write.
- `Confirmed` requires the matching control-point response and fresh measured telemetry. A post-write timeout or malformed response is `Unknown`, is never retried, and remains visible in the session UI.
- The three-second Hold to start action is hidden/disabled without persisted exact-model `HardwareVerified` capability. Physical-console Start remains the fallback.

The owner reported `0.8 km/h` as the Omega Z minimum start speed, and read-only Stage 2 independently confirmed `0.8 km/h` as the device-reported FTMS range minimum. The software still requires motion-stage verification before enabling Start.

## Remaining hardware gate

Read-only identity/range/stopped-telemetry evidence is recorded in [Omega Z Stage 2 read-only FTMS evidence](../protocol-evidence/omega-z/2026-08-03-stage2-read-only-ftms.md). Next, verify unloaded Stop in its own approved window. Only after Stop passes may one empty-belt Start at the verified `0.8 km/h` minimum be followed immediately by Stop. The safety key must be fitted, physical Stop reachable, and a named observer/window recorded. No automatic retry or protocol fallback is permitted.

The standard FTMS control point defines a Start or Resume procedure, but this story requires real model-specific behavior evidence before exposure: [Bluetooth SIG FTMS](https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0-1/).
