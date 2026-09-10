---
title: Polar H10 PFTP memory protocol provenance
type: protocol-evidence
status: active
owner: project
audience: agent-and-developer
updated: 2026-09-10
---

# Polar H10 PFTP memory protocol provenance

## Evidence question

Can TreadmillRunner independently implement the bounded Polar H10 operations needed to query recording status, start or stop one non-motion sensor recording, enumerate recordings, fetch the exercise sample file, and remove a specifically identified remote recording without widening treadmill command authority?

## Source and authorship boundary

- Capture identifier: `polar-h10-pftp-public-contract-2026-09-10`.
- Source type: Polar's public [H10 product documentation](https://github.com/polarofficial/polar-ble-sdk/blob/master/documentation/products/PolarH10.md), [H10 offline-exercise API contract](https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/sdk/java/com/polar/sdk/api/PolarH10OfflineExerciseApi.kt), and public protobuf declarations for [PFTP requests](https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/sdk/proto/pftp_request.proto), [PFTP responses](https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/sdk/proto/pftp_response.proto), [exercise samples](https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/sdk/proto/exercise_samples.proto), and [shared types](https://github.com/polarofficial/polar-ble-sdk/blob/master/sources/Android/android-communications/library/src/sdk/proto/types.proto), plus the owner-approved feature plan.
- Collection date: 2026-09-10.
- Device/firmware: no physical device or firmware observation is claimed by this record.
- Collection method: read-only review performed before implementation. No live Bluetooth access, packet capture, account access, pairing, or device mutation was used.
- Author: TreadmillRunner project implementation, written independently in C#.
- License boundary: Polar SDK source text and generated protobuf code are not copied, translated, or structurally ported. The implementation uses only the minimal published wire facts named below and project-authored codecs and tests. Polar's repository license remains attributable in documentation; no Polar source file is vendored.

## Minimal protocol facts allowed for implementation

- The Polar PFTP service uses UUID `0000feee-0000-1000-8000-00805f9b34fb` and the published MTU, device-to-host, and host-to-device characteristic UUIDs.
- H10 recording exposes one active exercise at a time and accepts an exercise identifier bounded to 1 through 64 characters.
- The public API contract identifies heart-rate and RR sample types; heart-rate supports 1- and 5-second intervals, while RR ignores the interval.
- The required high-level operations are status, start, stop, recursive list, GET of `SAMPLES.BPB`, and REMOVE of the exact recording path.
- Frames carry bounded sequence/continuation/status metadata and protobuf-compatible request/response payloads. Project code must reject malformed, oversized, out-of-order, incomplete, or unknown-status data.

These facts are a protocol contract, not an authorization to contact a device. Any live connection or write requires a separate stage-specific owner approval naming the H10, command class, observer, and time window.

## Sanitization

There is no captured personal telemetry. Golden fixtures must contain synthetic exercise identifiers, synthetic HR/RR values, fixed non-personal timestamps, and no Bluetooth address, device name, pairing material, account identifier, location, or owner data. Raw live captures must not be added under this record.

## Planned deterministic evidence

- Project-authored golden request/response fixtures for start, status, stop, list, HR samples, RR samples, and remove.
- Negative fixtures for sequence mismatch, continuation mismatch, error status, invalid paths, oversized payloads, traversal limits, timeout, and cancellation.
- Explicit expected bytes for each frame header and protobuf field used by the codec; decoder-only assertions are insufficient.
- Focused protocol and Windows transport contract tests recorded in the story validation evidence.

## Current conclusion and unsupported claims

Offline implementation can proceed within the bounded Polar-only transport seam. Physical availability, first-sample timing, Windows service access, pairing behavior, continued recording after disconnect, RR/5-second firmware support, and real-device removal remain unsupported until separately approved and observed. The feature stays disabled by default outside tests until the physical 1-second heart-rate recovery gate passes.
