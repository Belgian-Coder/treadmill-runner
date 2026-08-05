---
title: TR-005A FTMS Capability Discovery
type: user-story
status: completed
owner: project
audience: agent-and-developer
updated: 2026-08-03
---

# TR-005A — Generic FTMS capability discovery

As the device gateway, I want to parse standard FTMS features and operating ranges without model-specific assumptions so that every treadmill adapter can expose honest limits while reported support remains distinct from hardware-verified control.

## Acceptance

- Parse the eight-octet Fitness Machine Feature characteristic and preserve its raw feature fields.
- Parse Supported Speed Range in 0.01 km/h units and Supported Inclination Range in signed 0.1% units.
- Reject truncated, oversized, inverted, and zero-increment values.
- Represent speed and incline ranges in portable Core contracts with provenance/evidence.
- A reported speed, incline, control-point, or Start/Resume feature never sets a hardware-verified `Can*Remotely` flag.
- Omega Z, Run 500, and Challenge Run may consume this shared parser through separate adapters; no names or vendor quirks enter the parser.
- No BLE write, command encoder, or enabled UI control is added.

Primary standard: [Bluetooth SIG Fitness Machine Service 1.0.1](https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0-1/).
