---
title: TR-006H Household Heart-Rate Sensors
type: user-story
status: software-complete
owner: project
audience: agent-and-developer
updated: 2026-08-04
---

# TR-006H — Household heart-rate sensors

As one of two runners, I want to add our Polar strap and Garmin watch broadcasts once, assign them to the correct runner, and have TreadmillRunner reconnect and select the best fresh source automatically.

## Acceptance

- One treadmill and several Bluetooth Heart Rate Service sensors can be enrolled.
- Every HR sensor persists its strap/watch kind, Polar/Garmin/other family, runner assignments, preferred state, fallback priority, and auto-connect choice.
- Selection is evaluated for the chosen session profile. A private watch assigned to another runner is ineligible.
- A fresh preferred Polar wins by default; a fresh assigned Garmin broadcast is a fallback. Samples are never averaged or merged.
- Source changes increment a generation, reset controller timing, record a sanitized session event, and suspend active HR-speed automation until it is explicitly re-enabled.
- Devices and Control show disconnected, connecting, connected/waiting, stale, and fresh-pulse states truthfully. The diagonal heart slash means no sensor connection, not merely no current BPM sample.
- Enrollment/preference/forget mutations remain idle-only and HR connections stay structurally read-only.
- Existing H10 data migrates without changing treadmill capability evidence; a pre-migration backup is retained.

## Garmin and Apple boundary

Garmin watches use the standard BLE Heart Rate service while HR Broadcast is enabled. The gateway reconnects when broadcasting begins but cannot enable it remotely. Recording a native Treadmill activity on the watch is the preferred Garmin Connect path; app-generated FIT remains a manual fallback. Direct Garmin Connect upload requires an officially supported integration, and Apple Health delivery requires a HealthKit-capable iPhone companion.

## Evidence

- Workflow run: `automations/user-story-workflow/runs/US-TR-006H`
- Migration: `AddHouseholdHeartRateSensors`
- Local generated gallery: `screenshots/devices.png`, `screenshots/control.png` after running the browser suite; populated PNGs are not committed publicly.
- External hardware status: the saved Polar H10 was rediscovered after migration, but no fresh BPM notification was observed in the final read-only check.
