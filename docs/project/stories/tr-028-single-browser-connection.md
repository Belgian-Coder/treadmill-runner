---
title: TR-028 — Single browser connection and live-run confidence
type: user-story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-07
---

# TR-028 — Single browser connection and live-run confidence

## Outcome

One browser session now owns one `GatewayConnectionSupervisor`. Run, Control, the layout, and the heart-rate indicator consume selective immutable state from that supervisor instead of creating independent SignalR connections and lease-heartbeat loops.

The supervisor reconnects indefinitely with capped jitter, verifies the client/server build, hydrates authoritative session and live state, detects service-instance changes, and reacquires the same browser holder's controller lease. Any interruption immediately clears manual authority and marks retained measurements stale. Recovery never sends or replays a treadmill command.

Run owns selection and preparation. A nonterminal gateway session routes to Control; a terminal or absent session routes back to Run. The gateway-owned session and timer may continue while browser controls are unavailable, and the UI says so explicitly.

## Safety boundary

The browser supervisor can use only live-state reads, the SignalR live hub, and controller lease acquire/heartbeat routes. It has no Start, speed, incline, pause, stop, or automation recovery path. Existing command serialization and unknown-outcome behavior remain unchanged.

## Validation

Focused tests cover indefinite retry and stale-state contracts. Browser tests verify responsive Run rendering and assert that Control plus its HR indicator never create more than one concurrent live WebSocket. The combined deterministic gate and bounded browser groups passed, and locally signed release 1.5.19 was activated through the installed updater without a treadmill command.
