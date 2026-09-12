---
title: Polar H10 live memory and continuity validation
type: protocol-evidence
status: active
owner: project
audience: agent-and-developer
updated: 2026-09-12
---

# Polar H10 live memory and continuity validation

## Evidence question

Can installed and corrected TreadmillRunner code query the exact enrolled owner-worn Polar H10, start and stop one bounded one-second heart-rate memory recording, list the resulting recording, and preserve the ordinary live-HR stream without avoidable connection contention?

## Provenance and approval

- Capture identifier: `polar-h10-live-memory-continuity-2026-09-12`.
- Source type: owner-provided screenshots, retained TreadmillRunner Bluetooth journal, installed local API responses, project-authored tests, and direct local observation.
- Date and window: 2026-09-12, current Europe/Brussels owner-supervised window.
- Device: the exact actively enrolled Polar H10; Bluetooth identifiers are deliberately omitted.
- Firmware: to be recorded only if returned by an approved read and needed for the conclusion.
- Collection method: Stage 2 connection/service/status/list reads, followed only after the safety preflight by Stage 3 project-owned Polar H10 memory start/stop. No treadmill command is permitted.
- Owner approval: the owner stated that the Polar H10 is worn on the chest and asked Codex to check, fix, validate real operation, and prevent disconnects.
- Independent author: TreadmillRunner project implementation and validation; no third-party implementation code is used as an oracle.

## Sanitization

Do not retain Bluetooth addresses, device identifiers, enrollment identifiers, pairing material, profile identifiers, personal heart-rate values, or raw unsanitized payloads. Evidence records only operation stage, bounded result, timestamps rounded to seconds when useful, connection generation, failure class, counts, and non-reversible hashes when required.

## Safety and stop conditions

- No remote treadmill Start, speed, incline, Stop, Pause, or unknown characteristic write.
- Before the H10 recording write, confirm no live workout and owner control of the empty treadmill safety key/emergency stop.
- Start only one named project-owned one-second H10 heart-rate recording, confirm status, stop that same recording, and list it. Do not remove it remotely.
- Stop immediately on treadmill movement, uncertain device identity/state, unexpected response, loss of the owner-supervised window, or any command outside the exact H10 memory operation.

## Baseline and result

- The affected workout was the 2026-09-12 17:30:44-18:03:15 Europe/Brussels session. Its retained journal records two mid-run native H10 disconnect windows and the sample diagnostics record 19 missing one-second observations. All 1,676 submitted sample writes were accepted, so the gaps are not storage loss.
- Installed 1.5.87 probes were not reliable: an earlier request returned `memoryCapability: false` after approximately ten seconds, while the final worn-sensor recheck returned HTTP 500 after approximately three seconds. A fresh read-only scan in that same final window observed the H10 at strong signal advertising both standard heart-rate and Polar PFTP services, while a direct attempt through the persisted locator could not find PFTP. This supports a stale/rotated Windows BLE locator as the memory failure mechanism, but installed corrected-code proof remains pending.
- Polar's public implementation confirms the existing MTU request/response routing and published query identifiers. The proposed characteristic-channel change was rejected before implementation.
- Corrected source now performs bounded unique locator resolution before every memory operation, preserves the stable enrolled identity in API results, and requests Windows connection maintenance for active live-HR and PFTP notification sessions.
- Focused deterministic validation passed 34 tests across the Polar protocol, memory client/store, locator disambiguation, and Windows address/session policy suites with zero build warnings. The complete reconnect/coordinator suite separately passed 43/43 with a three-minute harness timeout. A larger combined run hit the focused one-minute watchdog after reporting only passing tests; it is not counted as completed validation.
- No treadmill command or H10 memory write was issued. Installed corrected-code and physical start/status/stop/list validation remain pending a separately authorized install/restart and the explicit safety preflight.

## Unsupported claims

Until the remaining steps above pass, corrected installed PFTP availability, memory start/stop, continued live-HR notifications during PFTP access, long-workout continuity, RR recording, five-second recording, firmware portability, and remote deletion remain unsupported. `MaintainConnection` is best-effort mitigation and does not prove that physical disconnects are eliminated.
