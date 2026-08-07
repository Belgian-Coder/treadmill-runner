---
title: TR-013 interactive Garmin simulator report
type: validation-evidence
status: accepted-local
owner: project
audience: developer-and-operator
updated: 2026-08-07
---

# TR-013 interactive Garmin simulator report

The rebuilt PRGs were exercised on two representative Connect IQ SDK 9.2.0 Simulator layouts. Final screenshots were rendered from the Simulator window and inspected at original resolution.

| Device | Simulator | Explicit Select start | Back protected | Stop + save | Standalone | Layout |
|---|---:|---:|---:|---:|---:|---|
| Fenix 8 47 mm | 6.0.2 | pass | pass | pass | pass | pass |
| Vivoactive 5 | 5.2.0 | pass | pass | pass | pass | pass |

The second Select returned each device to Ready, providing simulator evidence that stop/save completed. Final review confirmed readable contrast, separated runner/session headers, concise action hints, safe round-edge clearance, and optical Ready/timer center differences of 2 px on Fenix and 1.5 px on Vivoactive. Paired HTTPS, invalid/revoked-token behavior, physical-watch operation, phone sync, and IQ Store review were not exercised.

Approved source captures are indexed in `connectiq/TreadmillRunnerCompanion/store/screenshots/README.md`. Garmin's current asset rules must still be checked before IQ Store upload.
