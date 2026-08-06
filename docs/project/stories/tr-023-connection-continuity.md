---
title: TR-023 Daily-use Connection Continuity
type: user-story
status: implemented
owner: project
audience: agent-and-developer
updated: 2026-08-06
---

# TR-023 — Daily-use connection continuity

As a runner, I want an active workout to remain gateway-owned through browser and bounded Bluetooth interruptions so that the timer and plan stay authoritative without unsafe command replay.

## Guarantees

- Closing, sleeping, or disconnecting the browser removes only its manual-control authority. The gateway continues the workout timeline, persistence, planned transitions, and eligible HR control.
- SignalR connection attempts continue with capped jittered backoff. A recovered browser reloads version, service instance, active session, device state, and lease state before enabling controls.
- A treadmill telemetry gap records disconnect/reconnect events and advances elapsed workout time without inventing speed, incline, HR, or distance samples.
- Automatic BLE reconciliation requires the same enrolled treadmill, a running session, two fresh stable samples, a moving belt, no unknown command result, and no change beyond one verified treadmill increment.
- Recovery expires old-generation intents and creates only fresh generation-bound speed or incline commands. It never creates Start and never retries an uncertain command.
- A possible physical-console change requires the runner to select **Resume planned controls**. A gateway restart always requires this explicit action even after fresh movement is confirmed.
- The restart checkpoint is bounded and stores only the active session position, last confirmed values, selected automation mode, connection generation, and redacted enrollment evidence already present in the session configuration.
- If the same treadmill and fresh movement cannot be confirmed within 30 seconds after restart, the session becomes Interrupted.
- Pre-run checks are expanded by default; detailed target normalization stays nested unless it needs attention.

## Deliberate limits

- Bluetooth loss is not Stop or Pause. The physical console, safety key, and physical Stop remain authoritative.
- The application cannot reconstruct distance or telemetry that the treadmill did not report during an outage.
- Service restart recovery restores tracking, not control. The user must inspect the physical treadmill and explicitly resume planned controls.
- This story issues no treadmill commands during automated validation and performs no deployment, soak, power-cycle, tag, or release work.

## Acceptance evidence

- Core policy tests cover automatic recovery, wrong identity, stale telemetry, stopped belt, unknown outcome, console intervention, and restart behavior.
- Integration tests cover durable checkpoint round-trip, same-holder lease recovery, and HR automation continuing after browser lease expiry.
- Browser tests verify open pre-run checks and responsive reconnect-safe control rendering.
- The reviewed EF Core migration adds bounded checkpoint storage without changing terminal-session history behavior.
