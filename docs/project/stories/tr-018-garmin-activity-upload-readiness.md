---
title: TR-018 — Repair and complete Garmin activity upload setup
type: story
status: completed
owner: project
audience: operator-and-developer
updated: 2026-08-05
---

# TR-018 — Repair and complete Garmin activity upload setup

## Outcome

Profiles show the optional Garmin completed-activity sign-in independently from watch pairing and upload-job history. A signed release contains its own pinned offline Python 3.12 adapter runtime, reports safe readiness states, and never needs system Python or an install-time download.

## Acceptance

- An unpaired watch returns HTTP 204 and is a normal empty state.
- Activity status, upload jobs, and watch pairing load independently; failure of one optional resource cannot hide valid account status.
- Status reports `Ready`, `RuntimeMissing`, `DependencyMissing`, `AdapterInvalid`, or `Unavailable`, a safe operator message, and whether connection is allowed.
- Connection returns a safe HTTP 503 before accepting credentials when the adapter is not ready.
- Passwords, MFA values, protected tokens, internal commands, and sensitive paths never enter persistence, logs, diagnostics, API errors, or screenshots.
- Signed releases contain a hash-verified CPython 3.12 runtime, hash-locked adapter dependencies, Python/license notices, and a credential-free release probe.
- The exact disconnected + empty jobs + unpaired-watch browser condition displays the sign-in form; jobs and watch failure cases keep it visible with resource-specific recovery text.
- Release 1.5.10 is validated, signed, installed through the normal update mechanism, and checked live only as far as the ready sign-in form.

## Boundaries

The private Garmin consumer interface remains experimental, unsupported, and disabled for every profile by default. Credentials and MFA remain explicit runner actions. This story does not issue treadmill commands, connect a Garmin account, or upload an activity during automated or deployment acceptance.

## Evidence

The serial workflow run is `automations/user-story-workflow/runs/US-TR-018`. Release 1.5.10 passed the deterministic suite, all 45 browser tests, offline runtime/package probes, signed manual-bundle activation, service readiness, live `Ready` adapter status, and a live disconnected sign-in-form screenshot without credentials.
