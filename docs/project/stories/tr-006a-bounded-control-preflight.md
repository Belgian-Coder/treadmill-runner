---
title: TR-006A Bounded Control Preflight
type: user-story
status: planned
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# TR-006A — Bounded control preflight

As a runner, I want invalid workout targets caught before a run and every live target bounded again at the gateway so that an imported or edited workout cannot command an unsupported speed or incline.

## Acceptance

- Workout editor/import preview identifies every target below/above verified model, profile, or personal limits and requires correction or explicit bounded normalization before saving.
- Runtime validates again and never sends an out-of-range or off-increment value.
- The UI explains requested, accepted, normalized, rejected, and measured values separately.
- Command intents carry an operation ID, short expiry, expected session/device state, active lease, and connection generation.
- Unknown physical outcome is not retried. Success requires the protocol response where available plus fresh measured telemetry.
- Target behavior is implemented against generic capabilities; model adapters encode commands and confirmation rules.
