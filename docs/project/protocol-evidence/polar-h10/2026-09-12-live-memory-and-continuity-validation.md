---
title: Polar H10 live memory and continuity validation
type: protocol-evidence
status: active
owner: project
audience: agent-and-developer
updated: 2026-09-13
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
- Installed 1.5.87 probes were not reliable: an earlier request returned `memoryCapability: false` after approximately ten seconds, while the final worn-sensor recheck returned HTTP 500 after approximately three seconds. A fresh read-only scan in that same final window observed the H10 at strong signal advertising both standard heart-rate and Polar PFTP services, while a direct attempt through the persisted locator could not find PFTP. This established a stale/rotated Windows BLE locator as one memory failure mechanism.
- Polar's public implementation confirms the existing MTU request/response routing and published query identifiers. The proposed characteristic-channel change was rejected before implementation.
- Candidate 1.5.88 was signed, staged, and activated from the exact corrected commit after the gateway reached the required HTTP 204 idle state. The installed executable version and source commit matched, readiness passed, and retained profile data remained available.
- After the owner reattached the H10, installed 1.5.88 reached live-HR `Ready` with fresh valid notifications. Its service-hosted memory probe still failed, while the exact production PFTP client completed the same status request against a safe database copy under the interactive account. This isolated a second defect: the PFTP transport treated the supplemental Windows `GattSession` object as mandatory even though service-hosted GATT discovery/read/write can remain available when Windows refuses that object.
- Installed 1.5.89 confirmed that optional `GattSession` handling preserves live-HR access but did not complete the memory acceptance: service-hosted PFTP status intermittently timed out waiting for its response notification. Repeated safe status probes later reached the response path, where runtime tracing exposed a separate SQLite `DateTimeOffset` `ORDER BY` failure in the active-manual lookup.
- Corrected source now performs bounded unique locator resolution before every memory operation, preserves the stable enrolled identity in API results, requests Windows connection maintenance when a session object is available, and continues PFTP access when that supplemental session object is unavailable. Status reads reopen a fresh connection after a bounded transient timeout instead of retrying a mutating command, and active-manual ordering uses SQLite-native timestamp ordering. Caller cancellation remains fail-fast.
- Focused deterministic validation passed the prior 34-test Polar suite and complete 43-test reconnect/coordinator suite. The added optional-session regression run passed 20/20 selected Polar and Windows BLE policy tests with zero failures.
- Corrected source registers the MTU response callback before enabling either required notification descriptor and awaits both descriptors as the PFTP readiness barrier. A timed-out or otherwise incomplete exchange is permanently faulted so delayed frames cannot cross into a retry. Hosted status and project-authored synthetic GATT scenarios cover the non-hardware execution path.
- On 2026-09-13, installed 1.5.90 reached live-HR `Ready`, but a PFTP status request still took approximately 50 seconds and returned unavailable. An isolated production-code probe, run with the gateway stopped and automatically restored after an HTTP 204 idle-session gate, also timed out waiting for the first PFTP response. This ruled out concurrent live-HR ownership as the sole cause.
- Polar's current open-source Android client sends PFTP MTU request frames with GATT write-without-response. The Windows transport instead preferred write-with-response when the H10 exposed both capabilities. Candidate source now follows Polar's request-write behavior while retaining acknowledged writes as a fallback for characteristics that do not expose the preferred mode.
- The corrected candidate passed 15 focused PFTP and memory-client integration tests. A direct physical candidate status request then succeeded. One bounded owner-approved physical lifecycle also passed: idle status, start confirmation for the exact generated exercise, stop confirmation, exact remote listing, and download of 26 one-second heart-rate samples. The remote recording was retained and no treadmill command was issued.
- After an explicit read-only connection retry, live HR returned to `Ready`. A subsequent candidate PFTP status request completed while ten consecutive gateway observations remained fresh and `Ready` on one unchanged live-HR generation; the maximum observed sample age was 1.14 seconds.
- Separate continuity evidence remains: earlier live generation 2399 had a 5.16-second validity gap, generation 2400 recorded notification intervals as high as 6.48 seconds, and later ended with `AdapterUnavailable`. The candidate request-write correction fixes PFTP response behavior but does not by itself prove long-duration elimination of sensor or Windows-adapter gaps.

## Unsupported claims

Until a release containing the candidate is installed and the gateway endpoints repeat the lifecycle, installed PFTP availability and the complete installed workflow remain unsupported. Candidate physical one-second HR start/status/stop/list/download and concurrent live-HR status access are supported by the bounded observations above. Long-workout continuity, uninterrupted live HR throughout a complete PFTP lifecycle, RR recording, five-second recording, firmware portability, and remote deletion remain unsupported. `MaintainConnection` is best-effort mitigation and does not prove that physical disconnects are eliminated.
