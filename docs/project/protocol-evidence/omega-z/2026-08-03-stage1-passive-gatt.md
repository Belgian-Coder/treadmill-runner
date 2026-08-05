---
title: Omega Z Stage 1 Passive Scan and GATT Enumeration
type: protocol-evidence
status: observed-with-follow-ups
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# Omega Z Stage 1 passive scan and GATT enumeration

## Authorization and boundaries

- Owner authorization: “Let's start with the treadmill. Can you search for it or explain how to setup bluetooth?” in the interactive Codex session on 2026-08-03.
- Observer: project owner in the interactive session; personal name was not supplied and is not inferred from local machine metadata.
- Window: 2026-08-03 22:39–22:41 Europe/Brussels.
- Allowed: bounded passive scan and uncached GATT metadata enumeration.
- Not performed: pairing, characteristic value reads, subscriptions, writes, commands, belt movement, or remote Start.

## Environment

- Windows Bluetooth service: running.
- Microsoft Bluetooth LE Enumerator and RZ616 Bluetooth adapter: healthy.
- Gateway: interactive Development process, stopped after enumeration.
- Candidate identity: retained locally; shared evidence uses suffix `…2117` only.

## Passive scan

- Duration: 10 seconds.
- Advertised name: none.
- Strongest RSSI: approximately `-60 dBm`.
- Advertised services: Cycling Speed and Cadence `1816`; Fitness Machine Service `1826`.
- Vendor `FFF0` was not advertised but appeared in uncached GATT enumeration.

## Uncached GATT observation

| Service | Characteristic | Properties | Interpretation |
|---|---|---|---|
| `1826` | `2ACC` | read | Fitness Machine Feature |
| `1826` | `2ACD` | notify | FTMS Treadmill Data |
| `1826` | `2AD4` | read | Supported Speed Range |
| `1826` | `2AD5` | read | Supported Inclination Range |
| `1826` | `2AD9` | write + notify | Control Point metadata only; untouched |
| `1826` | `2ADA` | notify | Fitness Machine Status |
| `FFF0` | `FFF1` | read + write | Vendor metadata only; untouched |
| `FFF0` | `FFF3` | write | Vendor metadata only; untouched |
| `FFF0` | `FFF4` | notify | Expected vendor status surface |
| `180A` | `2A24` | read | Model value not read in Stage 1 |
| `180A` | `2A26` | read | Firmware value not read in Stage 1 |

Other standard services included Generic Access, Generic Attribute, Device Information, Cycling Speed and Cadence, and User Data. A separate custom service was observed but is not required for the read-only telemetry hypothesis.

## Disposition

- Expected `1826`, `2ACD`, `FFF0`, and `FFF4` surfaces match the adapter hypothesis.
- FTMS is the preferred Stage 2 telemetry trial. Vendor `FFF4` remains a separately selected alternative and must not be mixed automatically.
- Polar visibility was outside this treadmill-only window.
- Stage 2 should read Device Information and FTMS feature/range values, then subscribe only to `2ACD` under separate authorization.
- Telemetry values, reconnect, commands, power cycles, soak, and Session 0 remain unverified.
