---
title: Horizon Omega Z protocol findings
type: protocol-evidence
status: active
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# Horizon Omega Z protocol findings

## Evidence baseline

These findings were taken from the locally cloned qdomyos-zwift repository at
commit `99f27b2cb5360ce925c19c40d5a4ddff29ef4057` dated 2026-08-01, together
with upstream issues 841, 3137, and 3809 and pull request 4698.

The findings are implementation evidence, not a vendor protocol contract.
Firmware differences must be expected.

## Discovery

The issue evidence reports the advertised device name `JFTMOmega Z`. QZ's
detection is not Omega-specific: it matches the case-insensitive `JFTM` prefix
and creates its shared `horizontreadmill` implementation.

Observed services:

| Service | Purpose |
|---|---|
| `180A` | Device Information |
| `1826` | Standard Fitness Machine Service (FTMS) |
| `FEE7` | Vendor service; purpose not required yet |
| `FFF0` | Proprietary Horizon communication service |

Important characteristics:

| Service | Characteristic | Use |
|---|---|---|
| `FFF0` | `FFF3` | Proprietary writes |
| `FFF0` | `FFF4` | Proprietary notifications |
| `1826` | `2ACD` | FTMS Treadmill Data |
| `1826` | `2AD9` | FTMS Fitness Machine Control Point |

The Windows gateway hardware spike must discover `FFF0` and `1826` explicitly
and prove that the service-hosted BLE transport can subscribe to both before
login.

Treadmill-only Stage 1 on 2026-08-03 observed `1826/2ACD` and `FFF0/FFF4` on the owner’s exact unit. See [the sanitized passive/GATT evidence](protocol-evidence/omega-z/2026-08-03-stage1-passive-gatt.md). Read-only Stage 2 then established model `OMEGA Z`, Bluetooth firmware `V10.23.17`, a reported `0.8–20.0 km/h` speed range with `0.1 km/h` increments, and fresh stopped `2ACD` telemetry; see [the sanitized Stage 2 evidence](protocol-evidence/omega-z/2026-08-03-stage2-read-only-ftms.md). On 2026-08-04 the owner separately observed console software `S3.02` during power-on; it is distinct from the Bluetooth firmware. No control-point write or command was performed during Stage 2.

## Upstream settings evidence

The issue history describes two related but distinct compatibility choices:

- In issue 3137, the Omega Z connected but produced no speed or metrics. The
  maintainer instructed the user to enable Horizon `Force FTMS`; the user then
  confirmed that it worked.
- In issue 3809, another Omega Z user reported that speed/incline control began
  working after enabling both `Paragon X` and `Horizon 7.8 start issue`.
  Pull request 4698 subsequently added an `Omega Z` toggle that applies those
  two behaviors together.

The upstream `Omega Z` toggle does **not** also enable `Force FTMS`. Treating
all three switches as one known-good combination would therefore overstate the
evidence. They represent an FTMS telemetry path and a Paragon-compatible vendor
control candidate that must be reconciled on the exact treadmill firmware.

The current Omega Z setting is a compatibility alias for both:

- Horizon Paragon X protocol behavior; and
- the Horizon 7.8 start/autostart workaround.

This was made explicit in pull request 4698. It means the current primary path
for Omega initialization, speed, and incline is proprietary when that Horizon
adapter is selected. `Force FTMS` instead changes device selection to the FTMS
path.

## TreadmillRunner default mapping

`OmegaZCompatibilityProfile.Default` preserves the evidence without exposing
QDomyos-specific switches in our UI:

| TreadmillRunner choice | Upstream evidence | Initial state |
|---|---|---|
| Prefer FTMS telemetry | Issue 3137 confirmed `Force FTMS` fixed absent metrics | Enabled, pending exact-device confirmation |
| Paragon-compatible vendor protocol candidate | Issue 3809 and pull request 4698 | Recorded as a candidate, not auto-selected for control |
| Require measured physical belt movement before Running | Horizon 7.8 startup behavior plus the project safety boundary | Enabled |
| Remote speed, incline, pause, stop, and Start capabilities | No project-owned hardware evidence yet | Disabled |

The profile implements the portable `ITreadmillProtocol` contract and is
resolved through `TreadmillProtocolRegistry`. A future Domyos treadmill adds a
new protocol implementation with its own identity matcher and verified
capabilities; the BLE transport, session engine, persistence, and UI remain
unchanged.

## Proprietary initialization

The Omega alias uses the Paragon-style initialization:

1. Write `55 AA 00 00 02 20 00 00 00 00 0D 0A` to `FFF3`.
2. Wait for the expected notification sequence.
3. Write the command header beginning
   `55 AA 00 00 03 02 0E 00 42 EF`.
4. Write the payload `01 00 00 00 00 00 0D 0A`.
5. Do not declare the connection ready until the response/timeout policy has
   completed.

QZ serializes writes and uses notification waits. The browser implementation
must do the same; it must not issue concurrent GATT operations.

## Notification reassembly

`FFF4` messages may be fragmented across multiple BLE notifications. When a
chunk begins with `55`, QZ reads the little-endian payload length from bytes 6
and 7 and expects a complete frame of `payloadLength + 10` bytes. It appends
subsequent chunks until that length is reached.

One captured Omega frame arrived as several chunks rather than one
notification. Parser tests must include fragmented and coalesced delivery,
short first chunks, disconnect mid-frame, extra bytes, and a new header before
the previous frame completes.

For a reassembled frame whose byte 5 is `17`, QZ interprets:

- speed as little-endian bytes 24-25 divided by 100 in mph, then multiplied by
  `1.60934` to produce km/h;
- incline as byte 30 divided by 10.

Distance, calories, and estimated watts in this path are derived locally and
must not be presented as direct machine measurements unless later captures
prove otherwise.

## Proprietary controls

The current Omega/Paragon branch encodes:

- speed with command `05`, payload `targetSpeedTimes10` as little-endian 16-bit
  plus `01`, and CRC over the three payload bytes;
- incline with command `06`, payload `targetInclineTimes10` low byte plus `00`,
  and CRC over the two payload bytes;
- stop as `55 AA 00 00 02 14 00 00 00 00 0D 0A`;
- pause as `55 AA 00 00 03 03 00 00 00 00 0D 0A`.

The CRC is CRC-CCITT with initial value `FFFF`. Port the algorithm into pure C#
and prove it with golden vectors before writing to the treadmill.

The current start frame contains fixed values and the upstream source includes
a checksum-related TODO. Treat Start as a separate research item. The MVP
should initially require the user to start from the physical console.

## FTMS fallback

Standard FTMS provides a valuable fallback and later Domyos extension:

| Operation | Control point payload |
|---|---|
| Request control | `00` |
| Set target speed | `02` + unsigned 16-bit value in 0.01 km/h |
| Set target inclination | `03` + signed 16-bit value in 0.1% |
| Start/resume | `07` |
| Stop/pause | `08` + parameter |

The implementation must listen for control-point indications and treat support
as a discovered capability, not an assumption.

TR-006B now has independently authored FTMS golden payloads for Request Control
`00`, Start/Resume `07`, and Stop `08 01`, plus response-code parsing. The
software consumes a Start intent before the one Start write and requires a
matching response plus fresh measured telemetry. These codecs do not mark the
Omega Z capability as verified; the owner reports `0.8 km/h` as its slowest
start speed, pending an exact range read and the dedicated unloaded trial.

## Critical protocol uncertainty

Historical issue 841 contains an upstream comment that the Omega uses FTMS to
change inclination. Current QZ code with the Omega toggle routes inclination
through the proprietary Paragon branch whenever `FFF3` is present. Issue 3809
also documents a real user's failed incline control before the later toggle was
added.

Therefore the hardware spike must test both modes on the exact treadmill:

1. proprietary initialization plus proprietary speed/incline;
2. FTMS request-control plus FTMS speed/incline;
3. mixed mode only if captures demonstrate it is required.

Do not hide this behind automatic fallback until behavior and duplicate-command
risk are understood. Expose the selected protocol in diagnostics.

Issue 3137 increases confidence in FTMS for telemetry, but it does not establish
that FTMS control or a mixed FTMS/vendor session is safe.

## Source locations in qdomyos-zwift

- Device detection and factory selection: `src/devices/bluetooth.cpp:1642`.
- Omega setting and compatibility aliases:
  `src/devices/horizontreadmill/horizontreadmill.cpp:108`.
- Proprietary initialization: the same file at line 117 and line 194.
- Proprietary speed: line 1212.
- Proprietary incline: line 1298.
- `FFF4` reassembly and parsing: line 1545.
- FTMS parsing: line 1854.
- characteristic/service selection: line 2351.
- CRC implementation: line 2871.
- Omega settings UI: `src/settings.qml:11218`.
- Legacy Domyos UUIDs: `src/devices/domyostreadmill/domyostreadmill.cpp:46`.

## License boundary

qdomyos-zwift is licensed under GPL version 3. Directly copying or porting its
implementation into browser-delivered WebAssembly can create GPL distribution
obligations. Keep provenance for every imported fragment. If this project must
be closed source, obtain legal guidance or permission before using QZ code;
protocol observations and an independently written implementation should be
handled deliberately rather than assumed to remove all licensing questions.
