---
title: TR-005 Omega Z and Polar H10 Read-Only Enrollment and Telemetry
type: user-story
status: implemented-hardware-acceptance-pending
owner: project
audience: agent-and-developer
updated: 2026-08-03
workflow-run: US-TR-005
---

# TR-005 — Omega Z and Polar H10 read-only enrollment and telemetry

As the runner, I want the gateway to enroll my exact Horizon Omega Z and Polar H10 and use fresh measured telemetry so that workouts and history reflect the real equipment without granting discovery or enrollment code any treadmill command authority.

## Acceptance

- Exactly one active treadmill and one active primary heart-rate enrollment can be stored locally; enrollment changes are rejected while a session is active.
- Enrollment preserves the Windows device identifier locally, protocol, redacted identity fingerprint, labels, explicitly selected telemetry mode, reported ranges/features, evidence level, verification time, and concurrency version.
- The portable BLE connection used by discovery, diagnostics, and enrollment has read/subscribe operations and structurally has no characteristic-write method.
- The Windows adapter performs uncached reads and bounded notification subscriptions for FTMS Treadmill Data, the explicitly selected Omega vendor path, and Polar Heart Rate Measurement.
- FTMS, Omega, and Polar payloads are independently decoded with malformed/truncated and fragmented/coalesced coverage where applicable.
- Treadmill and heart-rate workers run simultaneously, expose a connection generation, use bounded reconnect delays, and publish persistent sanitized faults and telemetry freshness.
- Real telemetry feeds the authoritative session engine while the simulator remains available for development. Simulator mutation routes are development-only and cannot mutate a real enrolled session.
- Device status and preflight UI identify the enrolled equipment, protocol/mode, connection state, freshness, evidence, measured speed/incline/heart rate, and failure state.
- `CanStartRemotely` remains false. There is no Start route, command encoder, retry, replay, or fallback path in this story.

## Deterministic evidence

- Release build: zero warnings and zero errors.
- Core, protocol, persistence, endpoint, native-boundary, and scripted simultaneous-stream tests cover the implemented slice.
- The additive `AddDeviceEnrollments` EF Core migration is reviewed and schema-tested.
- The authoritative workflow evidence is under `automations/user-story-workflow/runs/US-TR-005`.

## External acceptance still required

These are hardware observations, not claims made by the deterministic suite:

- Approved Stage 1 passive scan and uncached GATT enumeration of the powered Omega Z and visible Polar H10, with a named observer and time window.
- Stop and hypothesis review if expected `1826`, `FFF0`, `2ACD`, `FFF4`, or Polar Heart Rate surfaces materially differ.
- Simultaneous read/notification operation during a bounded 5–10 minute observation and one successful treadmill power-cycle/reconnect check. No physical acceptance test exceeds 10 minutes; later reliability issues are handled from operational diagnostics.
- Sanitized retained evidence under `docs/project/protocol-evidence`; no motion-affecting command may be tested under this story.

## Boundaries

- The treadmill console, safety key, and physical Stop remain authoritative.
- The telemetry path is explicit (`Ftms` or `Vendor`) and never silently mixed or switched.
- Garmin and other treadmill adapters are outside v1.
- Motion control, command intents, HR-speed automation, deployment, update activation, and recovery/export belong to later stories.
