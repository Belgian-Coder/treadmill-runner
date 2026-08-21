---
title: TreadmillRunner Safety Guidelines
type: safety-policy
status: active
owner: project
audience: agent-developer-and-runner
updated: 2026-08-21
---

# Treadmill safety guidelines

## Authority

- The safety key, physical Stop button, console, and treadmill firmware are authoritative.
- TreadmillRunner is neither an emergency-stop system nor a medical device.
- BLE loss may leave the belt moving. Never call disconnect a fail-safe stop.
- No treadmill command endpoint may be reachable from the public internet.
- Remote belt Start is disabled unless the enrolled adapter/firmware has passed the dedicated TR-006B gate. Standard-service discovery, another model's evidence, or a successful write cannot enable it.

## Browser and control lease

- The gateway owns one treadmill connection and one authoritative session. Browsers do not own BLE.
- One browser may hold the manual-control lease; all others observe.
- Browser loss expires that lease but does not terminate the gateway-owned workout.
- A new browser may reclaim manual control after expiry. It cannot pre-empt a live lease.
- Reload, reconnect, service restart, update, or restore may never start the belt or replay a command. Browser-only loss does not suspend healthy gateway-owned automation. BLE recovery may resume only after same-device moving telemetry is stable and no console intervention or unknown outcome is present; restart recovery always requires explicit planned-control resume.
- A verified Start intent is explicit, single-use, short-lived, bound to the active lease and connection generation, and consumed before its one allowed write. It is never automatically retried.

## Command rules

- Only the serialized device coordinator may write GATT.
- Bind commands to operation ID, expected state, control lease, short expiry, and connection generation.
- Treat every recovered generation as new: expire old intents, compare fresh physical values with the pre-gap values, and send only newly confirmed current-position targets.
- Clamp finite values to verified machine, profile, workout, and personal limits.
- Rate-limit increases; coalesce only commands proven safe to supersede.
- A successful BLE write is not success. Require measured telemetry confirmation.
- Correlate each control-point indication to the opcode written by that serialized exchange. Ignore a late or unrelated acknowledgement inside the bounded wait; it must not confirm, reject, or trigger a replay of the current command.
- Do not blindly retry when the physical outcome is unknown.
- Reaching the final workout step is not physical completion. For hardware sessions, keep the run live and out of completed History until fresh stopped telemetry is observed. Send at most one verified completion Stop; a rejected, stale, disconnected, or unknown outcome requires the physical Stop control and must not be finalized or retried blindly.
- Stale treadmill or HR telemetry suspends automation and blocks increases.
- Reconnect returns to `Ready` and requires explicit re-arming.

## Hardware progression

1. Unit tests and simulator.
2. Windows BLE scanning and service feasibility.
3. Read-only treadmill/HR telemetry.
4. Stop unloaded.
5. Pause at minimum speed.
6. Small incline change while off the belt.
7. Small speed change at minimum belt speed.
8. Planned transitions and manual overrides.
9. Conservative HR-speed automation.
10. Remote Start only as a separate final model/firmware gate, using the physical-console workflow as the fallback.

Each gate needs sanitized raw evidence, a golden fixture, observed telemetry, timeout/disconnect results, a focused automated test, and explicit owner approval. Never jump directly from protocol code to a running-speed human test.

## Failure presentation

Show device identity, mode, freshness, armed state, requested and measured values, and command confirmation. Use persistent, accessible states for adapter unavailable, device absent, connecting, telemetry stale, write unknown, protocol invalid, automation suspended, and control unavailable. Do not hide faults behind a toast or automatic reconnect.

## Diagnostics

Differentiate raw measurements, requested targets, confirmed results, and derived metrics. Bound retention. Redact Bluetooth addresses and user identity from shared bundles. Never log signing keys, credentials, or unredacted personal captures.
