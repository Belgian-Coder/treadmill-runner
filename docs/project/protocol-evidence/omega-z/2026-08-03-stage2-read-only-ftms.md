---
title: Omega Z Stage 2 Read-Only FTMS Identity and Telemetry
type: protocol-evidence
status: observed-with-follow-ups
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# Omega Z Stage 2 read-only FTMS identity and telemetry

## Authorization and boundaries

- Owner authorization: “okay go ahead with the next steps” in the interactive Codex session on 2026-08-03, following the explicit request for a read-only identity/range check.
- Observer: project owner in the interactive session; a personal name was not supplied and is not inferred from machine metadata.
- Window: 2026-08-03 23:43–23:47 Europe/Brussels.
- Allowed: passive scan, uncached GATT enumeration, local FTMS enrollment, characteristic reads, and a `2ACD` notification subscription.
- Not performed: control-point access, characteristic-value writes, pairing, commands, Stop, Start, or belt movement.

## Observed identity

- Candidate identity: retained locally; shared evidence uses suffix `…2117`.
- Advertisement name: absent in this window.
- Strongest observed RSSI: approximately `-51 dBm`.
- Advertised services: Cycling Speed and Cadence `1816`; Fitness Machine Service `1826`.
- Device Information model `2A24`: `OMEGA Z`.
- Device Information firmware `2A26`: `V10.23.17`.
- Selected telemetry mode: FTMS only.

## Reported FTMS capabilities and ranges

| Surface | Observation | Evidence level |
|---|---|---|
| Fitness Machine Feature `2ACC` | Reports speed-target, incline-target, and standard Start/Resume support | protocol-reported |
| Supported Speed Range `2AD4` | `0.8–20.0 km/h`, `0.1 km/h` increment | protocol-reported |
| Supported Inclination Range `2AD5` | `0–12%`, `0.1%` increment | protocol-reported |
| Treadmill Data `2ACD` | Fresh stopped sample: `0.0 km/h`, `0.0%` incline | observed notification |

The device-reported speed minimum independently matches the owner's earlier `0.8 km/h` observation. It is now acceptable as the candidate minimum for a later dedicated motion trial; it does not by itself authorize Start or any other control.

## Application state

- Local enrollment protocol: `horizon-omega-z`.
- Enrollment evidence advanced from `Unknown` to `PassivelyObserved` after the read-only values were persisted.
- Connection reached the ready state on generation 2 with repeated fresh stopped telemetry and no persistent fault.
- `CanSetSpeedRemotely`, `CanSetInclineRemotely`, `CanPauseRemotely`, `CanStopRemotely`, and `CanStartRemotely` all remained `false`.

## Enrollment finding

The diagnostic scan observed the exact candidate, but the product scan returned no supported candidate because the treadmill omitted its advertised name and `OmegaZCompatibilityProfile` currently requires the `JFTMOmega Z` prefix. The owner-confirmed device was therefore enrolled through the existing local API using its observed device identifier and service list. The actual model and firmware came from subsequent characteristic reads, not from the supplied display label.

Before daily-use enrollment is considered complete, the product UI must support this anonymous-advertisement case without broadly classifying every FTMS device as an Omega Z.

## Disposition

- Exact identity, firmware, speed range, incline range, and stopped FTMS telemetry are established for the current unit.
- The next motion-affecting gate is unloaded remote Stop in a separately approved window.
- Start remains blocked until unloaded Stop passes and a later empty-belt Start/Stop window is explicitly approved with a named observer, fitted safety key, and reachable physical Stop.
- No control capability was promoted by this evidence.
