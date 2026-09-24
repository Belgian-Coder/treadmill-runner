---
title: "08 — FTMS and treadmill integration (Horizon Omega Z)"
type: specification
status: draft-for-rewrite
audience: android-team
updated: 2026-09-24
---

# 08 — FTMS and treadmill integration (Horizon Omega Z)

This chapter specifies everything the Android app needs to find, identify, connect to, read, and
(after commissioning) command the household treadmill. It carries the complete byte layouts,
constants, timings and hardware evidence from the previous .NET/Windows implementation. The new
team does not have access to that code base, so this chapter is the source of truth.

The companion chapter **09 — Safety and command contract** defines *when* a command may be sent and
how its outcome is judged. This chapter defines *what* goes over the air and how the device behaves.

Golden vectors for every codec live in `data/ftms/` next to this file (see §16).

## 0. Conventions

| Item | Rule |
|---|---|
| UUIDs | 16-bit SIG UUIDs expand to `0000xxxx-0000-1000-8000-00805f9b34fb`. Vendor `FFF0`/`FFF1`/`FFF3`/`FFF4` use the same base. |
| Byte order | Every multi-byte FTMS and Omega field is **little-endian**. Hex in this document is written in wire order. |
| Units | Speed is km/h. Incline is percent grade. Distance is metres on the wire and kilometres in the app. Time is seconds. |
| Rounding | Encoders round half away from zero (`round(x * scale)`, where 0.5 goes up in magnitude). |
| Evidence words | *Observed* means seen on the owner's exact unit. *Reported* means the device said so over FTMS. *Upstream* means a public third-party report (QDomyos-Zwift project). *Assumption* means not proven and must be verified. |
| Clocks | Freshness and timeouts use a **monotonic** clock (`SystemClock.elapsedRealtimeNanos`). Wall-clock time is only for display and persistence. |

## 1. Verified device profile

This is the only treadmill model/firmware that has any hardware-verified control. The evidence was
gathered on the Windows host stack. Capabilities are keyed per model, firmware **and host stack**,
so the Android app starts read-only. It must re-commission on the phone (§11) before it enables any
control.

| Property | Value | Provenance |
|---|---|---|
| Product | Horizon Omega Z treadmill | owner |
| Protocol id (enrollment) | `horizon-omega-z` | app constant |
| DIS Model Number `2A24` | `OMEGA Z` (upper case, exact) | observed, Stage 2 |
| DIS Firmware Revision `2A26` | `V10.23.17` | observed, Stage 2 (Bluetooth module firmware) |
| Console software | `S3.02` | owner-observed on the console at power-on, 2026-08-04. This is a separate component from the BLE firmware; it is **not** readable over BLE. |
| Advertised local name | often **absent**. Upstream reports `JFTMOmega Z`. | observed, Stage 1/2 |
| Advertised services | `1816` Cycling Speed and Cadence + `1826` FTMS | observed, Stage 1/2 |
| Typical RSSI at the treadmill | about −51 to −60 dBm | observed |
| Speed range (`2AD4`) | 0.8–20.0 km/h, increment 0.1 | reported, Stage 2 |
| Incline range (`2AD5`) | 0–12 %, increment 0.1 | reported, Stage 2 |
| Minimum start speed | 0.8 km/h. FTMS Start (`07`) always starts the belt at 0.8 km/h. | observed, Stage 3 |
| Feature `2ACC` | reports speed-target and incline-target support. Standard Start/Resume is inferred because the control point is present. | reported, Stage 2 |
| Telemetry mode | **FTMS** (`2ACD`) | selected and verified |
| Verified commands (Windows) | Request Control `00` (accepted, never answered), Start/Resume `07`, Stop `08 01`, Set Target Speed `02`, Set Target Inclination `03` | observed, Stage 3 |
| Speeds physically exercised | 0.8, 1.0, 1.2, 1.5 km/h | observed |
| Inclines physically exercised | 0.5, 1.0 % (plus 0.0 after Stop) | observed |
| Never verified | raw FTMS Pause `08 02`; any vendor `FFF3` write; any speed above 1.5 km/h; any incline above 1.0 %; power-cycle/reconnect under control | — |

The legacy "accepted control profile" rule held that an enrollment with role Treadmill, telemetry
mode FTMS, protocol `horizon-omega-z`, model `OMEGA Z` and firmware `V10.23.17` (case-insensitive
compare) could have its verified controls turned on or off by the owner while idle. Turning them
on set `canStart`, `canStop`, `canSetSpeed` and `canSetIncline` to true, kept `canPause=false`,
and marked the ranges `HardwareVerified` (defaults 0.8–20/0.1 and 0–12/0.1 when none were
reported). On Android the same toggle exists, but it is available only after the phone-side
commissioning record exists for `(OMEGA Z, V10.23.17, android host stack)`.

## 2. Discovery and advertisement signatures

### 2.1 Matching rule (protocol `horizon-omega-z`)

A scanned advertisement is an Omega Z **enrollment candidate** only if one of these holds:

1. The advertised local name starts with `JFTMOmega Z` (case-insensitive).
2. The local name is absent or blank **and** the advertised service list contains **both** `1816`
   and `1826`.

A named device that is not an Omega (for example "Future Treadmill" advertising `1816`+`1826`) is
**not** matched. `1826` alone is not matched either. Matching only makes a device a candidate. It
never grants a capability. The exact model and firmware always come from the DIS reads after
connecting.

The raw advertised name is stored apart from the user-facing display label. A generated label
such as "Unnamed Bluetooth device" must never become identity evidence.

The golden cases are in `data/ftms/omega-z-advertisement-matcher.json`.

Generic FTMS devices (any other `1826` advertiser) may later get a generic read-only adapter. Any
control for them stays disabled regardless of the feature bits they report.

### 2.2 Scan policy (Android)

- Scan for treadmills **only during enrollment**. Use a bounded active scan (legacy used 5 s; the
  diagnostic scan allowed 1–30 s) with a ScanFilter on service `1826`. Keep the raw advertisement
  so the anonymous signature can be checked (the filter alone is not enough).
- Merge split advertisement and scan-response packets per address. Keep the strongest RSSI and
  union the service UUIDs.
- A truncated or overflowed scan result is reported as unavailable. Do not guess from it.
- **No treadmill scans during a run.** Reconnect goes directly by stored address. Android allows
  about 5 scan starts per 30 s per app, and the Polar SDK shares that budget.
- On Windows, the OS sometimes could not open an unpaired address from its cache. The legacy worker
  then ran a cancellable 5-second active scan and retried immediately only if **that exact stored
  address** appeared. Android may reuse this idea when `connectGatt` by address keeps failing
  (status 133). Treadmill identity is never rebound to a different address automatically.

## 3. GATT surface of the owner's unit (Stage 1, uncached enumeration)

| Service | Characteristic | Properties | Use in the app |
|---|---|---|---|
| `1826` FTMS | `2ACC` Fitness Machine Feature | read | read at every connect |
| `1826` | `2ACD` Treadmill Data | notify | **primary telemetry** (FTMS mode) |
| `1826` | `2AD4` Supported Speed Range | read | read at every connect |
| `1826` | `2AD5` Supported Inclination Range | read | read at every connect |
| `1826` | `2AD9` Fitness Machine Control Point | write + notify (as enumerated by Windows; the FTMS specification mandates indicate) | commands, only when a control capability is verified |
| `1826` | `2ADA` Fitness Machine Status | notify | not used by the legacy app (see §4.5) |
| `FFF0` vendor | `FFF1` | read + write | **never touched** |
| `FFF0` | `FFF3` | write | **never written** (vendor command path is not ported) |
| `FFF0` | `FFF4` | notify | alternative read-only telemetry (Vendor mode, §6) |
| `180A` DIS | `2A24` Model Number String | read | identity |
| `180A` | `2A26` Firmware Revision String | read | identity |
| Others seen | Generic Access, Generic Attribute, Cycling Speed and Cadence (`1816`), User Data, one extra custom service; upstream also reports `FEE7` | — | not used |

`FFF0` is **not advertised**. It only appears after GATT discovery. The treadmill exposes no
Battery Service. Battery `180F`/`2A19` applies to heart-rate sensors only (parser in §5.2).

## 4. Fitness Machine Service (`1826`)

### 4.1 `2ACC` Fitness Machine Feature (read, exactly 8 bytes)

| Bytes | Field | Type |
|---|---|---|
| 0–3 | Fitness Machine Features | uint32 LE |
| 4–7 | Target Setting Features | uint32 LE |

Any other length is rejected, and a rejected `2ACC` fails the connection attempt. The app reads
only two bits, both in Target Setting Features:

| Target bit | Meaning | App field |
|---|---|---|
| 0 | Speed target setting supported | `reportsSpeedTargetSupport` |
| 1 | Inclination target setting supported | `reportsInclineTargetSupport` |

`reportsStandardStartResume` is set when the service has a `2AD9` characteristic. Reported support
**never** sets any `can*Remotely` flag.

For reference, the SIG Fitness Machine Features bits are: 0 average speed, 1 cadence, 2 total
distance, 3 inclination, 4 elevation gain, 5 pace, 6 step count, 7 resistance level, 8 stride
count, 9 expended energy, 10 heart-rate measurement, 11 metabolic equivalent, 12 elapsed time,
13 remaining time, 14 power measurement, 15 force on belt and power output, 16 user data
retention. The SIG Target Setting bits are: 0 speed, 1 inclination, 2 resistance, 3 power,
4 heart rate, 5 targeted expended energy, 6 step number, 7 stride number, 8 distance,
9 training time, 10–12 time in 2/3/5 HR zones, 13 indoor-bike simulation, 14 wheel
circumference, 15 spin down, 16 cadence. This list comes from the SIG specification, not from a
device capture. Store the raw 32-bit values for diagnostics.

If `2ACC` is missing or not readable, both feature words are 0.

### 4.2 `2AD4` / `2AD5` supported ranges (read, exactly 6 bytes each)

| Characteristic | Bytes 0–1 | Bytes 2–3 | Bytes 4–5 | Scale |
|---|---|---|---|---|
| `2AD4` speed | minimum uint16 | maximum uint16 | increment uint16 | ×0.01 km/h |
| `2AD5` inclination | minimum **sint16** | maximum **sint16** | increment uint16 | ×0.1 % |

A range is valid when `maximum >= minimum` and `increment > 0`. A wrong length or an invalid range
fails the connection attempt. A missing or unreadable characteristic means the range is unknown
(null). Parsed ranges carry evidence `ProtocolReported`. Store the values as exact decimals, not
binary floats; they drive step alignment in 09.

The Omega Z reported values, encoded (derived, not a raw capture):

- `2AD4` = `50 00 D0 07 0A 00`, which is 0.8 / 20.0 / 0.1.
- `2AD5` = `00 00 78 00 01 00`, which is 0 / 12.0 / 0.1.

### 4.3 `2ACD` Treadmill Data (notify)

Bytes 0–1 hold the flags word (uint16 LE). Fields follow **in the order below**, and each is
present only when its condition holds:

| Order | Flag bit | Field | Type | Size | Scale / unit | App field |
|---|---|---|---|---|---|---|
| 1 | bit 0 **clear** (bit 0 = "More Data") | Instantaneous Speed | uint16 | 2 | ×0.01 km/h | `speedKph` |
| 2 | 1 | Average Speed | uint16 | 2 | ×0.01 km/h | `averageSpeedKph` |
| 3 | 2 | Total Distance | uint24 | 3 | 1 m | `totalDistanceMeters` |
| 4 | 3 | Inclination, then Ramp Angle Setting | sint16, sint16 | 2+2 | ×0.1 %, ×0.1 ° | `inclinePercent`, `rampAngleDegrees` |
| 5 | 4 | Positive Elevation Gain, Negative Elevation Gain | uint16, uint16 | 2+2 | ×0.1 m | `elevationGainPositiveM`, `elevationGainNegativeM` |
| 6 | 5 | Instantaneous Pace | uint16 (legacy layout, see note) | 2 | raw | `instantaneousPaceRaw` |
| 7 | 6 | Average Pace | uint16 (legacy layout, see note) | 2 | raw | `averagePaceRaw` |
| 8 | 7 | Total Energy, Energy per Hour, Energy per Minute | uint16, uint16, uint8 | 2+2+1 | kcal, kcal/h, kcal/min | `totalEnergyKcal`, `energyPerHourKcal`, `energyPerMinuteKcal` |
| 9 | 8 | Heart Rate | uint8 | 1 | bpm | `heartRateBpm` |
| 10 | 9 | Metabolic Equivalent | uint8 | 1 | ×0.1 | `met` |
| 11 | 10 | Elapsed Time | uint16 | 2 | s | `elapsedSeconds` |
| 12 | 11 | Remaining Time | uint16 | 2 | s | `remainingSeconds` |
| 13 | 12 | Force on Belt, Power Output | sint16, sint16 | 2+2 | N, W | `forceNewtons`, `powerWatts` |
| — | 13–15 | reserved | — | — | — | any set bit means **reject** |

Parser rules. Each is a golden test.

1. A payload shorter than 2 bytes is rejected.
2. Any reserved bit (13, 14, 15) set means the payload is rejected.
3. Every flag-indicated field must fit in the remaining bytes, or the payload is rejected. A later
   field must never be decoded at a wrong offset.
4. After the last field, the offset must equal the payload length exactly. Trailing bytes mean the
   payload is rejected.
5. An absent field is `null`, never zero.
6. A payload with no fields at all (for example `01 00`) parses successfully, but ingestion must
   **ignore** it. It refreshes no timestamp.

**Pace note.** The legacy parser reads each pace field as 2 bytes and labels them "seconds per
500 m". SIG texts have described the treadmill pace fields differently over time. The owner's
Omega Z has not been observed to set bit 5 or 6. The Android parser keeps the legacy layout, so
the golden vectors stay valid. Pace is never used for control or confirmation. If a capture ever
shows bit 5 or 6 set, verify the width against the current Bluetooth GATT Specification
Supplement before displaying pace.

**Which fields the Omega Z sends.** The sanitized evidence records only the decoded values:
instantaneous speed (0.0, 0.8, 1.0, 1.2, 1.5 km/h) and inclination (0.0, 0.5, 1.0 %). The raw
flags word was not recorded. The notification cadence was not recorded either; the Stage 3
latencies suggest about one sample per second. Android commissioning Stage A (§11) must capture
and record the flags word, the set of present fields and the notification interval. Until then
the app must accept any valid flag combination.

Golden vectors are in `data/ftms/ftms-treadmill-data-parser.json`.

**Ingestion into the telemetry model.** Details are in 09 §8.

- Take the receive time from the monotonic clock when the BLE callback fires.
- If `speedKph` is present, it replaces the speed and sets `speedObservedAt`. If absent, the
  previous speed and its **previous** timestamp are kept.
- The same rule applies to `inclinePercent` / `inclineObservedAt`.
- The auxiliary fields are replaced wholesale with the values in this notification. They are
  informational only.
- A notification counts as primary telemetry (it feeds the silence watchdog and Ready) when it
  carries speed or inclination.
- Plausibility check before accepting, using the reported ranges when known:
  - speed must be finite, ≥ 0 and ≤ the speed-range maximum (or ≤ 100 km/h when no range is
    known);
  - incline must be finite and within the incline range (or |incline| ≤ 90 when no range is
    known).
  An implausible payload **drops the telemetry** (`telemetry = null`), sets the treadmill
  connection state to Faulted with "The treadmill sent implausible telemetry.", and is never
  clamped into a usable value.
- A notification is ignored if its connection generation is no longer current.

### 4.4 `2AD9` Fitness Machine Control Point

#### Opcodes

| Opcode | Name | Parameter | Used by the app |
|---|---|---|---|
| `00` | Request Control | none | yes. Sent once per command connection before the first motion command. |
| `01` | Reset | none | **never** |
| `02` | Set Target Speed | uint16 LE, ×0.01 km/h | yes, after `canSetSpeed` is verified |
| `03` | Set Target Inclination | sint16 LE, ×0.1 % | yes, after `canSetIncline` is verified |
| `04`–`06` | Set Target Resistance / Power / Heart Rate | — | never |
| `07` | Start or Resume | none. There is no speed parameter; the Omega Z starts at 0.8 km/h. | yes, after `canStart` is verified |
| `08` | Stop or Pause | uint8: `01` = Stop, `02` = Pause | Stop `08 01` only. **`08 02` is never sent.** App "Pause" = Stop `08 01`. |
| `09`+ | other FTMS procedures | — | never |
| `80` | Response Code (device → app) | request opcode, result code | parsed |

#### Encoder rules

- Speed: value = `round(kph × 100)`. The input must be finite and in 0 ≤ kph ≤ 655.35, otherwise
  it is a programming error (throw).
- Inclination: value = `round(percent × 10)` as sint16. The input must be finite and in
  −3276.8 ≤ percent ≤ 3276.7.
- Examples (golden): 1.0 km/h is `02 64 00`, 0.8 is `02 50 00`, 1.2 is `02 78 00`, 1.5 is
  `02 96 00`, 20.0 is `02 D0 07`. 2.5 % is `03 19 00`, 0.1 % is `03 01 00`, 0.5 % is `03 05 00`,
  1.0 % is `03 0A 00`, −1.5 % is `03 F1 FF`.
- A target command is **never** combined with Start. Upstream (QDomyos) sends Start before some
  targets; this app deliberately does not, so a target change can never restart a stopped belt.

#### Response parsing

A response is valid only when **all** of these hold:

- it is exactly 3 bytes, and byte 0 is `80`;
- byte 1 is an opcode the app knows (`00`, `02`, `03`, `07`, `08`) and is not `80`;
- byte 2 is a defined result code.

| Result | Name |
|---|---|
| `01` | Success |
| `02` | Op Code Not Supported |
| `03` | Invalid Parameter |
| `04` | Operation Failed |
| `05` | Control Not Permitted |

Anything else is unparseable. Golden vectors are in `data/ftms/ftms-control-point-codec.json`.

#### Exchange rules (one command at a time)

1. All control-point traffic goes through the single serialized command writer (09 §4). At most
   one exchange is outstanding.
2. Enable the `2AD9` CCCD once per connection: **Indicate** if the characteristic offers it,
   otherwise **Notify**. The Omega enumerated as write+notify on Windows. Enable it at link setup,
   before the first write. Remember the configured mode per connection and do not rewrite it per
   command.
3. Write the request **with response** (`WRITE_TYPE_DEFAULT`).
4. Wait for a response whose request opcode equals the opcode just written. Silently **ignore**
   any response for a different opcode (a late acknowledgement of an earlier command). An ignored
   response must never confirm, reject or trigger a replay.
5. Response windows:
   - Request Control `00`: **300 ms**. A timeout here is a *typed* "no response" and is accepted
     as control granted (Omega quirk, §12). A parsed non-success, or a response for opcode `00`
     that is not Success, is a pre-motion rejection. Tear down the command state; no motion was
     sent.
   - Motion opcodes (`02`, `03`, `07`, `08`): **2 s**. A timeout, write error, disconnect or
     cancellation after the write was issued makes the outcome **Unknown** (09).
   - Hard upper bound accepted by the transport: 10 s.
6. Control ownership (`controlOwned`) belongs to the connection generation. It resets on every
   disconnect, reconnect and teardown. On a retained connection, consecutive commands send Request
   Control only once (this optimization is §10.3's latency fix).
7. The command path may only address `1826/2AD9`. Any other characteristic is refused by
   construction.

### 4.5 `2ADA` Fitness Machine Status (notify)

The status characteristic is present, but the legacy app did not subscribe to it. The Android app
**may** subscribe for diagnostics (journal only). It must never use `2ADA` to confirm or reject a
command; confirmation is always measured `2ACD` telemetry (09 §5). For reference, the SIG status
opcodes are:

| Opcode | Meaning |
|---|---|
| `01` | Reset |
| `02` | Stopped or paused by user (+ uint8 `01` stop / `02` pause) |
| `03` | Stopped by safety key |
| `04` | Started or resumed by user |
| `05` | Target speed changed (+ uint16 ×0.01 km/h) |
| `06` | Target incline changed (+ sint16 ×0.1 %) |
| `07`–`0x0E` | other target changes, not treadmill-relevant |
| `FF` | Control permission lost |

These are unverified on the Omega Z. If a later capture shows reliable `03` (safety key) or `FF`
(control lost), a follow-up story may surface them as warnings only.

## 5. Device Information and Battery

### 5.1 DIS `180A`

- `2A24` Model Number String and `2A26` Firmware Revision String are UTF-8. Trim surrounding
  whitespace. Empty or whitespace-only counts as absent.
- **Before any control capability exists** (evidence below HardwareVerified), DIS reads are
  *optional* and run **after** the first valid telemetry. Their failure never blocks or delays
  Ready. The values are persisted asynchronously as evidence (§8.4).
- **When the enrollment is HardwareVerified**, DIS model **and** firmware must both be read
  (bounded 15 s each) **before** subscribing and before Ready. Missing either value fails the
  attempt: "A hardware-verified treadmill did not provide a complete model and firmware
  identity." The identity compare is exact after trimming. Legacy used ordinal comparison for this
  check; case-insensitive comparison was used only when matching the accepted profile. On a
  mismatch, see §8.5 (downgrade).

### 5.2 Battery `180F` / `2A19` (heart-rate sensors only)

Exactly 1 byte in 0..100. Any other length or a value above 100 is rejected. Golden vectors are in
`data/ftms/battery-level-parser.json`.

## 6. Omega Z vendor telemetry `FFF0` / `FFF4` (read-only)

Vendor mode is a **read-only** alternative telemetry path. The app never writes `FFF1` or `FFF3`.
The legacy vendor command encoder is documented as reference only
(`data/ftms/omega-command-encoder.reference.json`), so its CRC placement is understood. It is
**not** ported.

### 6.1 Frame format

```
offset: 0    1    2    3    4    5     6      7      8..       ...   n-2  n-1
        55   AA   ??   ??   ??   cmd   lenLo  lenHi  (header/payload)   0D   0A
total frame length n = (lenLo | lenHi<<8) + 10
```

- A frame starts with `55 AA` and ends with `0D 0A`.
- The declared payload length is uint16 LE at bytes 6–7. The frame length is declared + 10.
- The status frame has command byte (offset 5) `0x17`.
- Frames may arrive split across several notifications (a 74-byte frame arrived as 20+20+20+14),
  or several frames may be coalesced into one notification.

### 6.2 Reassembler (byte-at-a-time state machine)

The state is a candidate buffer plus the expected length (unknown until 8 bytes are buffered).
The hard bound on the declared payload is **4096**.

| Buffered | Incoming byte | Action |
|---|---|---|
| 0 | `55` | start candidate `[55]` |
| 0 | other | discard |
| 1 | `AA` | append |
| 1 | `55` | keep `[55]` (repeated `55`) |
| 1 | other | discard candidate |
| ≥2, length unknown, last byte `55`, incoming `AA` | — | emit diagnostic `TruncatedFrame(expected=null, received=buffered−1)`, restart candidate as `[55, AA]` |
| ≥2 | any other case | append. At 8 buffered, read the declared length. If it is > 4096, emit `LengthOutOfRange(expected=declared+10, received=8)` and discard the candidate; otherwise expected = declared + 10. |
| reaches expected | — | if the last 2 bytes are `0D 0A`, **emit the frame**. Otherwise emit `InvalidTerminator(expected, received)`. If the bad tail is `55 AA`, seed the next candidate `[55, AA]`; else if the last byte is `55`, seed `[55]`. Then clear. |

- After the length is known, payload bytes are opaque. `55 AA` inside the payload is data.
- `reset(reportTruncatedFrame = true)` emits `TruncatedFrame(expected, buffered)` when anything is
  buffered, then clears. Call it on every disconnect or new generation.
- Diagnostics are collected for the diagnostics journal. They are never user errors.

Golden vectors are in `data/ftms/omega-frame-reassembler.json`. These include the upstream-inspired
fragmented, coalesced, short-first-chunk, mid-frame-disconnect, oversized, bad-terminator and
header-in-terminator cases.

### 6.3 Status decoder

Accept a reassembled frame only if all of these hold:

- length ≥ 33;
- it starts `55 AA` and byte 5 = `0x17`;
- it ends `0D 0A`;
- declared length ≤ 4096 and declared + 10 = frame length.

Then:

- `speedKph = (byte24 | byte25 << 8) / 100.0 × 1.60934`. The wire value is hundredths of mph.
  Example: `6D 02` = 621, which is 6.21 mph = 9.9940014 km/h.
- `inclinePercent = byte30 / 10.0` (unsigned). Example: `0x35` = 53, which is 5.3 %.

Distance, calories and power are **not** decoded from vendor frames. Any such metric is derived
locally and labelled derived. The decoded status feeds the same ingestion path as FTMS, with speed
and incline both present, so both timestamps refresh. Golden vectors are in
`data/ftms/omega-status-decoder.json`.

The vendor decoding was reverse-engineered upstream and the scale was checked only against a
synthetic frame. Vendor mode has **not** been exercised on the owner's unit. It stays a selectable
read-only mode with no control, ever.

### 6.4 CRC (reference)

CRC-16/CCITT-FALSE: polynomial `0x1021`, initial value `0xFFFF`, MSB-first, no reflection, no
final XOR. The check value for ASCII `123456789` is `0x29B1`. Omega command frames place the CRC
low byte first at offsets 8–9 and compute it over the payload only. Vectors are in
`data/ftms/crc-ccitt.json`. The app needs this only if a future read-only frame validation uses
it; status frames are not CRC-checked by the legacy decoder.

### 6.5 Vendor initialization and control (never sent)

For provenance only. Upstream Paragon-style initialization writes `55 AA 00 00 02 20 00 00 00 00
0D 0A` to `FFF3`, waits for notifications, then writes a `55 AA 00 00 03 02 0E 00 42 EF` header
plus `01 00 00 00 00 00 0D 0A`. Upstream vendor control frames: speed command `05`, incline
command `06`, stop `55 AA 00 00 02 14 00 00 00 00 0D 0A`, pause
`55 AA 00 00 03 03 00 00 00 00 0D 0A`. Upstream Start contains a checksum TODO. **None of these
are sent by TreadmillRunner.** Upstream field reports conflict: one says the Omega uses FTMS for
incline, another says a vendor-mode toggle fixed control. FTMS control is the path verified on
this unit.

Upstream is GPL-3.0. Only protocol facts are recorded here. No upstream code may be copied.

## 7. Telemetry modes

| Mode | Characteristic | Control possible | Default |
|---|---|---|---|
| `Ftms` | `1826/2ACD` | yes, per verified capability | **yes** (verified) |
| `Vendor` | `FFF0/FFF4` + reassembler + decoder | **never** | no |

- The mode is chosen **explicitly** at enrollment. It is never switched or combined silently, and
  there is no automatic fallback. Changing mode means forgetting and re-enrolling.
- Commands require mode `Ftms`. In Vendor mode every command is rejected with "FTMS control is not
  explicitly selected for the enrolled treadmill."
- Diagnostics show the selected mode.

## 8. Enrollment

### 8.1 Rules

- **Exactly one active treadmill enrollment.** A second enrollment for role Treadmill is rejected
  (HTTP 409 in legacy) until the first is forgotten.
- Heart-rate enrollments are separate (see the HR chapter). A treadmill enrollment has no HR
  metadata, and an HR enrollment has no treadmill mode or capabilities.
- Enrollment, forget, mode change and control toggles are **rejected while a session is armed,
  running or paused**. Bluetooth disconnect is not a stop mechanism.
- Enrollment requires a supported protocol match (§2.1) and an explicit telemetry mode.
- A new enrollment starts with evidence `Unknown` and **all control flags false**.
- Enrollment is idempotent by operation id. The same id with the same request replays the stored
  result; the same id with a different request is a conflict.
- Creating a treadmill enrollment also creates its maintenance policy (§13).
- Forgetting archives the enrollment, cancels its connection worker and closes any command
  connection **without sending a command**.

### 8.2 Stored fields

| Field | Constraint |
|---|---|
| `id` | UUID, not empty |
| `role` | `Treadmill` |
| `deviceAddress` | the BLE address (legacy "device id"). Bounded text, at most 256 characters. Stored locally. **Redacted in shared diagnostics** (last 4 characters only, for example `…EEFF`). |
| `protocolId` | `horizon-omega-z` (at most 100 characters) |
| `identityFingerprint` | 64 lowercase hex characters (SHA-256) |
| `displayName` | at most 100 characters, user editable without touching identity |
| `advertisedName` | raw, nullable, used only in the fingerprint |
| `modelNumber`, `firmwareRevision` | nullable, trimmed, at most 100 characters |
| `telemetryMode` | `Ftms` or `Vendor` |
| `capabilities` | see §8.3 |
| `evidence` | `Unknown`, `ProtocolReported`, `PassivelyObserved` or `HardwareVerified` |
| `lastVerifiedAt` | timestamp or null |
| `version` | optimistic concurrency counter. Every evidence/capability update increments it. |

**Identity fingerprint.** Legacy computed it as SHA-256 (lowercase hex) over UTF-8 JSON with
camelCase keys and this property order:
`{"role":<enum number>,"deviceId":<address>,"protocolId":…,"advertisedName":…,"displayName":…,"services":[sorted UUIDs]}`.
Android defines its own canonical form. Proposal: the same fields, keys sorted, no whitespace,
role as the string `"Treadmill"`, UUIDs lowercase and sorted. Keep one rule for all time: the
fingerprint is computed **once at enrollment** and never recomputed from later metadata. It binds
sessions to "the same treadmill" (restart recovery compares it) and appears in diagnostics
**instead of** the address.

**Worker-restart rule** (this fixed a real defect, §10.3). A connection worker restarts **only**
if one of these changes: role, device address, protocol id, identity fingerprint, or telemetry
mode. Evidence, capability, name or version changes must **not** restart the BLE connection or
bump the connection generation. Legacy polled the enrollment every 2 s. On Android, observe the
repository flow and apply the same predicate.

### 8.3 Capability record (JSON shape)

Legacy persisted camelCase JSON. The range `evidence` was stored as an enum **number**
(0 Unknown, 1 ProtocolReported, 2 PassivelyObserved, 3 HardwareVerified).

```json
{
  "canSetSpeedRemotely": true,
  "canSetInclineRemotely": true,
  "canPauseRemotely": false,
  "canStopRemotely": true,
  "canStartRemotely": true,
  "reportsSpeedTargetSupport": true,
  "reportsInclineTargetSupport": true,
  "reportsStandardStartResume": true,
  "speedRange":   { "minimum": 0.8, "maximum": 20.0, "increment": 0.1, "evidence": 3 },
  "inclineRange": { "minimum": 0.0, "maximum": 12.0, "increment": 0.1, "evidence": 3 }
}
```

The Android record adds the host-stack key and keeps names readable. Proposal:

```json
{
  "schema": 1,
  "model": "OMEGA Z",
  "firmware": "V10.23.17",
  "hostStack": "android:<manufacturer>/<model>:api<sdk>",
  "telemetryMode": "Ftms",
  "evidence": "HardwareVerified",
  "reported": { "speedTarget": true, "inclineTarget": true, "startResume": true,
                "featureWords": { "machine": "0x........", "targetSetting": "0x........" } },
  "speedRange":   { "min": "0.8", "max": "20.0", "increment": "0.1", "evidence": "HardwareVerified" },
  "inclineRange": { "min": "0.0", "max": "12.0", "increment": "0.1", "evidence": "HardwareVerified" },
  "verified": { "start": true, "stop": true, "setSpeed": true, "setIncline": true, "rawPause": false },
  "commissioning": [ { "stage": "B-stop", "operationId": "…", "confirmedAt": "…", "approvedBy": "owner" } ]
}
```

Ranges are decimal strings so that step alignment is exact. `rawPause` is always false: the app has
no raw-Pause command.

### 8.4 Evidence levels and transitions

| Level | Meaning | Reached by |
|---|---|---|
| `Unknown` | enrolled, nothing read yet | enrollment |
| `ProtocolReported` | a value parsed from FTMS characteristics | the parser's range objects (per range) |
| `PassivelyObserved` | valid telemetry was received and DIS/feature/range values were persisted | the first valid telemetry of a non-verified enrollment, followed by an asynchronous evidence write |
| `HardwareVerified` | at least one command stage was physically confirmed for this exact model/firmware (and, on Android, host stack) | commissioning (§11) or the accepted-profile toggle |

- Passive evidence writes are asynchronous and deduplicated per `(enrollment, generation)`. They
  never delay Ready. A failed write is retried later (legacy used a bounded queue) and does not
  change the connection state.
- When persisting passive evidence for an already HardwareVerified enrollment, keep
  HardwareVerified and the verified `can*` flags. Merge the freshly *reported* flags and ranges
  (verified ranges win).
- Only a **Confirmed** commissioning outcome promotes, and it promotes **only the capability that
  was confirmed** (Start → `canStart`, SetSpeed → `canSetSpeed`, SetIncline → `canSetIncline`,
  Stop → `canStop`). Ranges are left untouched by promotion.

### 8.5 Downgrade on identity mismatch

On every connection of a HardwareVerified treadmill:

1. Read DIS model and firmware (required, §5.1).
2. Subscribe and wait for the first valid telemetry.
3. If `(model, firmware)` differs from the stored pair, **durably** write evidence
   `PassivelyObserved` with the newly read model/firmware and the freshly **reported**
   capabilities, in which every `can*` flag is false. Only after that write succeeds may the
   connection publish Ready with the downgraded enrollment.
4. If the write fails, the connection does not become Ready (the attempt fails and retries).
5. The generation must still be current before and after the write. Otherwise abort.

The result is that a firmware update on the treadmill silently removes all control until it is
recommissioned. The UI shows "Control unavailable (read-only)".

Each new treadmill connection also **reloads the enrollment from storage** before opening
Bluetooth. A missing or replaced enrollment fails closed.

## 9. Connection lifecycle, reconnect and watchdogs

### 9.1 States

`Disconnected → (Scanning) → Connecting → DiscoveringServices → Subscribing → (Initializing) → Ready`,
plus `Reconnecting` and `Faulted`. Every attempt gets a **new connection generation**, a process-wide
monotonically increasing integer starting at 1. Every telemetry value, command intent and
result carries the generation. Anything tagged with an old generation is ignored or rejected.

### 9.2 Attempt sequence (FTMS mode)

1. Reload the enrollment (§8.5 last paragraph). Set Connecting with the new generation.
2. `connectGatt(address, autoConnect=false)`, bounded.
3. Discover services (bound **15 s**). Require `1826/2ACD` with notify, or fail with "required
   characteristic missing".
4. Read `2ACC`, `2AD4`, `2AD5` (each bound 15 s). An invalid value fails the attempt.
5. If HardwareVerified, read DIS (§5.1) and require complete identity.
6. Enable the `2AD9` CCCD if any control capability is verified (§4.4).
7. Subscribe to `2ACD`. The **first notification must arrive within 15 s**. After that, **silence
   longer than 30 s** fails the attempt ("telemetry silent"). The Omega keeps notifying while the
   belt is stopped.
8. Ready is published on the first valid primary telemetry (after the downgrade check when
   verified). For a non-verified enrollment, optional DIS reads start after Ready.
9. Any exception (disconnect, GATT error, timeout, invalid telemetry) records a reliability
   incident, sets Faulted, then Reconnecting, waits the backoff delay, and starts a new attempt
   with a new generation. **No treadmill command is ever sent as part of reconnecting.**

Teardown: cancel the subscription and release the CCCD and GATT handles, with subscription
disposal bounded to **1 s**. Do not start a separate CCCD-disable write against a vanished device.
The Omega reassembler is reset on every teardown.

### 9.3 Backoff

The delay grows with `n`, the number of consecutive failures (n ≥ 1):

| Demand | Base delay | Jitter |
|---|---|---|
| **Active** (session armed, running or paused; manual Connect; preparation window) | `min(10 s, 2^min(n−1, 4) s)` → 1, 2, 4, 8, 10, 10 … | deterministic 0–500 ms per enrollment |
| **Idle** | `min(300 s, 2^min(n−1, 9) s)` → 1, 2, 4, 8, 16, 32 … 256, 300 | `min(500 ms, max(0, (300 − base) s in ms))`, so it is 0 at the 5-minute cap |

- The legacy jitter was `((b0 << 8) | b1) mod (limit + 1)` ms, where b0 and b1 are the first two
  bytes of the enrollment UUID in .NET little-endian GUID byte layout. Any deterministic
  per-enrollment stagger is acceptable on Android. What matters is that two devices do not retry in
  lockstep, and that a given device's delay is reproducible in tests.
- **Stable-connection reset.** When the previous attempt was "durably stable", `n` restarts at 1.
  Durably stable means at least 2 valid samples, with every gap ≤ 5 s, spanning at least **30 s**.
- A freshly resolved advertisement of the exact address, or a manual **Connect**, may bypass the
  remaining delay.
- Idle households keep the treadmill **disconnected**. Connection demand comes from:
  - selecting a runner and workout, which starts a **2-minute** preparation demand;
  - arming, which holds the connection until the session is terminal (End, completion, reset or
    Interrupted). Pause keeps it, because Pause is resumable.
  - a manual Connect, which lasts until Disconnect or app restart.
  Terminal cleanup also releases the command state so that the treadmill is not kept awake.

### 9.4 Watchdog and timing summary

| Constant | Value | Purpose |
|---|---|---|
| GATT operation bound | 15 s | discovery, each read |
| First notification | 15 s | subscribe → first `2ACD` |
| Telemetry silence | 30 s | between notifications |
| Subscription dispose bound | 1 s | teardown |
| Reconnect discovery watch | 5 s | exact-address rediscovery (Windows-derived, optional) |
| Enrollment refresh | 2 s (poll) / flow on Android | evidence changes must not restart the worker |
| Preparation demand | 2 min | pre-arm connection |
| Stable threshold | 30 s | backoff reset |
| Telemetry freshness | **5 s** per field | command guards and confirmation (09) |
| Request Control response | 300 ms | Omega never answers |
| Motion response | 2 s | per command |
| Telemetry confirmation | 5 s, polled every 100 ms | 09 §5 |
| Command connection setup | 15 s | discovery of `2AD9` |
| Reliability incident retention | 90 days | diagnostics |
| Link diagnostics sample | at most once per minute | diagnostics |

### 9.5 One GATT connection on Android

Windows needed a separate command connection. Both handles had to open the FTMS service with
`GattSharingMode.SharedReadAndWrite`, or `OpenAsync` failed with `SharingViolation`. **That is a
Windows (WinRT) artefact.** Android uses **one `BluetoothGatt` per treadmill**, and all
reads, writes, CCCD writes and notifications on it go through one serialized GATT operation queue.
Android never has more than one outstanding GATT operation. The command writer (09 §4) is a
client of that queue. Control ownership and CCCD state reset whenever the `BluetoothGatt` is
closed. Command-state release (on terminal session, Disconnect or Forget) clears
`controlOwned`. It does not need to drop the telemetry link unless demand has ended.

## 10. Hardware evidence summary

All of this was observed on the owner's unit with Windows as the host. Device identity in shared
evidence is redacted to the last four characters of the address.

### 10.1 Stage 1: passive scan and GATT enumeration (2026-08-03, 22:39–22:41 Europe/Brussels)

- Allowed: a bounded passive scan and uncached GATT metadata enumeration. Nothing else: no pairing,
  no reads, no subscriptions, no writes.
- 10-second scan: no local name, RSSI about −60 dBm, advertised `1816` + `1826`.
- `FFF0` was not advertised but appeared in enumeration. The characteristics are in §3.
- Conclusion: FTMS is the preferred telemetry trial. `FFF4` is a separate alternative and must not
  be mixed with it.

### 10.2 Stage 2: read-only identity and telemetry (2026-08-03, 23:43–23:47)

- Allowed: scan, enumeration, local FTMS enrollment, characteristic reads, and a `2ACD`
  subscription. No control-point access.
- No local name; RSSI about −51 dBm; `1816` + `1826`.
- DIS: `OMEGA Z` / `V10.23.17`. Ranges: 0.8–20.0/0.1 km/h and 0–12/0.1 %. `2ACC` reports speed
  target, incline target and Start/Resume (inferred from the control point).
- Fresh stopped `2ACD`: 0.0 km/h, 0.0 %. Ready on generation 2, repeated fresh samples, no fault.
- Evidence moved from `Unknown` to `PassivelyObserved`. All `can*` flags stayed false.
- Finding: the product scan missed the device because it required the `JFTMOmega Z` name prefix.
  This led to the anonymous `1816`+`1826` rule.

### 10.3 Stage 3: FTMS command commissioning (2026-08-04)

Conditions for every motion trial: the owner present at the treadmill, belt empty, safety key
fitted, physical Stop reachable, nothing able to contact the belt. No operation id was ever reused.

**Failures before any command (none reached a motion write).**

| Observation | Motion write? | Cause | Correction |
|---|---|---|---|
| FTMS characteristic discovery returned AccessDenied | no | the command path created redundant WinRT handles while telemetry held FTMS | target only `1826/2AD9`, reuse one handle, yield once after service discovery (Windows-only) |
| Shared open returned SharingViolation | no | telemetry held the service in an incompatible sharing mode | both handles use `SharedReadAndWrite` (Windows-only) |
| Request Control write completed but no response notification arrived | no Start/Stop sent | **firmware never answers `00`** | accept a *typed* timeout for `00` only; motion still needs its own response and telemetry |

**Start/Stop.** The first success used two gateway processes. Start was confirmed at 0.8 km/h and
Stop at 0.0 km/h, but the belt moved for about 13 s visually because a second process started
between the commands. That wrapper was rejected. The single-process retrial measured:

| Measurement | Value |
|---|---|
| Start intent → confirmed 0.8 km/h | 6.234 s |
| Confirmed Start → Stop intent | 3.043 s (intended 3 s hold) |
| Stop intent → confirmed 0.0 km/h | 3.956 s |
| Confirmed Start → confirmed Stop | 6.998 s |
| Pair end-to-end | 13.232 s |

Stop had reacquired control on a new command connection and paid a 2 s missing-response timeout.
The coordinator then retained one command connection and one control ownership per generation,
and Request Control got its own 300 ms window. No later start/stop-only trial re-timed the
retained connection. The daily sequences below came afterwards; their ≈1.4 s step latencies show
no per-command 2 s control re-acquire.

Accepted: `07` starts this unit at 0.8 km/h. `08 01` stops it, and fresh telemetry reaches
0.0 km/h. Both returned matching responses and were confirmed by fresh telemetry on the same
generation. `canStart` and `canStop` were promoted.

**Daily-control sequence, first run (partial).** Approved plan: start toward 1.2 km/h, set 1.5, set
incline 1.0 %, slow to 1.0, set incline 0.5 %, Stop, then check that speed and incline return to
zero. FTMS Start has no target, so the runner used Start 0.8 followed by SetSpeed 1.2.

| Step | Disposition | End-to-end | Measured |
|---|---|---|---|
| Start at 0.8 | Confirmed | 4.644 s | 0.8 |
| SetSpeed 1.2 | Confirmed | 1.443 s | 1.2 |
| SetSpeed 1.5 | **Unknown** after a Success response | 1.032 s | unavailable (reconnect) |
| Incline 1.0 / speed 1.0 / incline 0.5 | not sent | — | — |
| Reserved safety Stop | Confirmed | 2.307 s | 0.0 |
| Post-Stop observation | fresh | — | 0.0 km/h, 0.0 % |

Root cause: a capability-promotion database update incremented the enrollment version. The
connection supervisor treated the version change as a new enrollment, cancelled generation 1 and
started generation 2 while 1.5 km/h awaited confirmation. The fix is the worker-restart rule in
§8.2. The runner correctly stopped advancing, sent the reserved Stop, and **did not retry 1.5**.

**Daily-control sequence, corrected rerun.** Sequence operation `e341adc1-9ce2-4ec4-991f-29c2fd5c0e22`
ran in one process. Each step used its own derived, never-used operation id.

| Step | Disposition | End-to-end | Measured | Generation |
|---|---|---|---|---|
| Start at 0.8 km/h | Confirmed | 4.448 s | 0.8 km/h | 1 |
| SetSpeed 1.2 | Confirmed | 1.433 s | 1.2 | 1 |
| SetSpeed 1.5 | Confirmed | 1.434 s | 1.5 | 1 |
| SetIncline 1.0 % | Confirmed | 1.445 s | 1.0 % | 1 |
| SetSpeed 1.0 | Confirmed | 1.440 s | 1.0 | 1 |
| SetIncline 0.5 % | Confirmed | 1.446 s | 0.5 % | 1 |
| Stop | Confirmed | 1.776 s | 0.0 km/h | 1 |
| Post-Stop | fresh | — | 0.0 km/h, 0.0 % (`speedAndInclineReturnedToZero = true`) | 1 |

This verified SetSpeed and SetIncline for the listed targets. The owner then asked for more time
to inspect visually, so the runner now waits **at least 2 s after every confirmed Start, speed or
incline step** before the next planned step. A rejected or unknown step skips the rest of the plan
and sends the reserved Stop **immediately**. The observation hold never delays the safety Stop.

**Latency rules of thumb for the Omega Z** (used by the simulator, §14):

- Start from stopped to a confirmed 0.8 km/h: about 4.4–6.2 s. This includes connection and
  Request Control; the belt start itself is likely a console start ramp or countdown of about 3–4 s
  (assumption).
- A 0.2–0.5 km/h speed change or a 0.5 % incline change: about 1.43–1.45 s to confirmation.
- Stop from 1.0 km/h: about 1.8 s. From 0.8 km/h with a control re-acquire: about 4.0 s.

## 11. Commissioning procedure (Android, gate DEV-08)

Commissioning is how a capability becomes `HardwareVerified` on the phone's host stack. It is an
owner-attended, staged procedure. Implementation is frozen before a session starts. No new command,
retry or protocol change is allowed during a session.

### 11.1 Fixed preconditions for every motion stage

- The treadmill is powered, the belt empty, the safety key fitted, and physical Stop within reach.
  The owner is present and named as observer (at most 100 characters). Nothing can contact the
  belt.
- No other central is connected. The Windows gateway's Bluetooth must be off, because the
  treadmill accepts one central.
- The exact enrollment: mode FTMS, protocol `horizon-omega-z`, evidence ≥ `PassivelyObserved`,
  and stored model/firmware equal to the stage's expected `OMEGA Z` / `V10.23.17`. Otherwise the
  stage is refused before connecting.
- A capability record with reported ranges exists.

### 11.2 One stage invocation

1. Validate the request: a non-empty operation id; expected model, firmware and observer present;
   a target only for SetSpeed/SetIncline, finite and ≥ 0.
2. Wait at most **30 s** for fresh telemetry: Ready, generation > 0, speed age ≤ 5 s, and for
   SetIncline also incline age ≤ 5 s. On timeout, abort with no reservation and no command.
3. Stage-specific physical preconditions, checked **before** reservation:
   - Start: speed ≤ 0.05 km/h (belt stopped).
   - SetSpeed: speed > 0.3 km/h (already moving).
   - SetIncline and Stop: no motion requirement. Incline was verified on both a stopped and a
     moving belt.
4. **Reserve the operation id durably** as a receipt with status "reserved-before-command". The
   receipt carries a fingerprint = SHA-256 over the stage kind, expected model, firmware, observer,
   target and the enrollment identity fingerprint. If the id already exists, abort: "Generate a new
   ID; the previous command must never be replayed."
5. Resolve the target: Start = the reported speed minimum (0.8); SetSpeed/SetIncline = aligned per
   09 §4.3 against the reported range; Stop = none.
6. Build a 4-s intent (09 §3) with origin `Commissioning`, a fresh session id, and a context
   validator that accepts only this exact intent object. Execute it through the normal command
   writer in commissioning mode. Commissioning mode relaxes only the `HardwareVerified`
   requirement; every other guard still applies.
7. If the outcome is Confirmed, re-load the enrollment, re-check identity, and promote only that
   capability with evidence `HardwareVerified`.
8. Return a structured outcome: disposition, promoted flag, reason, requested/accepted/measured
   values, generation, issuedAt and completedAt (for latency).

### 11.3 Composite runners

**Start/Stop pair.** This needs two **distinct**, non-empty operation ids, checked before anything
runs. Run Start. If Start is not (Confirmed **and** promoted), return without waiting and without
sending Stop. Otherwise wait exactly **3 s**, run Stop, and report the latencies: start
end-to-end, stop end-to-end, confirmed-start→stop-intent, confirmed-start→confirmed-stop, and pair
end-to-end.

**Daily-control sequence.** This takes one sequence id. Step ids are derived deterministically
(legacy: the first 16 bytes of SHA-256(`"<sequenceId-32-hex-no-dashes>:<index>"`) interpreted as a
GUID). Planned steps:

| Index | Step |
|---|---|
| 0 | Start (0.8) |
| 1 | SetSpeed 1.2 |
| 2 | SetSpeed 1.5 |
| 3 | SetIncline 1.0 |
| 4 | SetSpeed 1.0 |
| 5 | SetIncline 0.5 |
| 6 | Stop |

Index 6 is the reserved safety Stop.

- After each **Confirmed** planned step, hold **2 s**.
- The first non-Confirmed step ends the plan.
- If Start was Confirmed, always send the reserved Stop (index 6) immediately after the plan ends,
  whether it completed or broke.
- If Start itself was not Confirmed, send nothing more.
- Afterwards poll up to 20 times, 250 ms apart, for fresh (≤ 5 s) speed and incline. Report
  `speedAndInclineReturnedToZero` when speed ≤ 0.05 and |incline| ≤ 0.05.

### 11.4 Stage order for the Android phone (DEV-08)

Each stage needs separate owner approval, sanitized raw evidence (hex of `2ACD` samples, the
flags word, `2AD9` exchanges with timestamps, generation), a golden fixture added to `data/ftms/`,
the observed telemetry, timeout/disconnect results, a focused automated test, and a written
approval record.

| Stage | Action | Promotes |
|---|---|---|
| A | Read-only: DIS, `2ACC`/`2AD4`/`2AD5`, 60 s of stopped `2ACD`. Capture the flags word, present fields and notification interval. No `2AD9` write. | evidence `PassivelyObserved` |
| B | Stop on the stopped, empty belt (`00` then `08 01`). Confirm 0.0 with fresh telemetry. | `canStop` |
| C | Start/Stop pair: Start at 0.8, hold 3 s, Stop, in one process on one connection. Record the latencies and the owner's visual duration. | `canStart` |
| D | SetIncline on a stopped belt: 0.1 %, then 0.0 %. | `canSetIncline` |
| E | Daily-control sequence (§11.3). | `canSetSpeed` (and re-confirms the others) |
| F | Pause-as-Stop and Resume: Start 0.8 → Stop (Paused) → Start (Resume) → Stop. Check that session progress is kept (09 §6). | none (behavioural) |
| G | One representative planned workout from the Run screen with transitions and a manual override. | none (acceptance) |

Raw FTMS Pause `08 02` is **not** a stage and is never promoted. Any Unknown result ends the
session's motion work immediately: use physical Stop, inspect the belt, and do not retry.

### 11.5 Legacy runbook order (for reference)

1. Stop (belt stopped).
2. SetIncline 0.1, then 0.0 (belt stopped).
3. Start/Stop pair.
4. Start.
5. SetSpeed 1.0 while at 0.8.
6. Pause (raw `08 02`; **never executed**).
7. Start (resume).
8. Stop.

Operational acceptance after that:

- 5–10 min of simultaneous Omega + Polar read-only telemetry, then one treadmill power-cycle
  reconnect check;
- recovery after a host reboot;
- a daily workout (remote Start at 0.8, planned speed and incline transitions, HR modes, manual
  override → `SuspendedManualOverride`, Pause/Resume/Stop, exports, History after reconnect,
  restart during an armed session → Interrupted with no command).

## 12. Known quirks and platform notes

| Quirk | Handling |
|---|---|
| **Request Control is never answered** by `V10.23.17` | Accept a typed timeout for opcode `00` only, with a **300 ms** window. The connection then counts as control-owned until disconnect. Other failures of `00` (a non-success result, a transport error) reject before motion. Motion opcodes always need their own matching response within **2 s** plus telemetry confirmation. |
| Anonymous advertisement | Match the `1816`+`1826` signature (§2.1). |
| Start has no speed parameter | The belt starts at the device minimum of 0.8 km/h. Any other start speed is `Start` followed by a confirmed `SetSpeed`. A Start intent whose requested value is not the verified minimum (±0.0001) is rejected. |
| Start is slow (≈3.5–6 s) | Allow the full 5 s confirmation window after the response. The UI shows "Starting…" with STOP visible. |
| Treadmill counters may reset after Stop/Start | Do not trust device cumulative distance or time across stops. The recorder integrates measured speed × time (§13) or accumulates deltas with reset detection. |
| Capability/evidence DB writes must not restart BLE | Worker-restart rule (§8.2). A regression test is mandatory. |
| Windows `SharedReadAndWrite`, AccessDenied, handle yield | **Windows-only.** Not applicable to Android. Android instead has the GATT status 133 / one-op-at-a-time rules. |
| `2AD9` enumerated as notify on Windows | Prefer indicate if offered, else notify (§4.4). Verify on Android in Stage A/B. |
| `2ACD` omits fields | Per-field timestamps (§4.3). Stale speed with fresh incline must not confirm a speed command. |
| One central only | The Windows gateway's Bluetooth must be disabled while the phone is used. |
| BLE loss does not stop the belt | Never present disconnect as a stop. The physical Stop and safety key are authoritative. |

## 13. Maintenance distance counting

- Each treadmill enrollment has one maintenance policy, created with the enrollment. The default is
  **3 months or 241 km** (about 150 miles). Allowed values: 1–24 months and 1–5000 km. Updates use
  optimistic concurrency (version) and idempotent operation ids.
- **App-tracked hardware distance** = the sum of `distanceKm` over sessions with origin `Hardware`
  in a **terminal** state (`Completed`, `Stopped`, `Interrupted`, `Faulted`), across **all
  profiles**. Excluded: `Simulator`, `SystemTest`, `Legacy` origins, and sessions still running.
- Session distance is integrated by the app as `measuredSpeedKph × Δt` while the belt is moving and
  the session is Running. It is **not** the device's `totalDistanceMeters`.
- A maintenance event stores `performedAt`, an optional note, and `appDistanceBaselineKm` = the
  current app-tracked distance at the time of recording. The event is idempotent by operation id;
  a replay with the same id raises a replay conflict.
- Rules, where `last` is the latest event by `performedAt` then `createdAt`:

| Quantity | Rule |
|---|---|
| state | `SetupRequired` if there is no event |
| `nextDueAt` | `last.performedAt + intervalMonths` (calendar months) |
| `nextDueKm` | `last.baseline + distanceIntervalKm` |
| `dueByDate` | `now ≥ nextDueAt` |
| `dueByDistance` | `trackedKm ≥ nextDueKm` |
| state when events exist | `DueByDateAndDistance`, `DueByDate`, `DueByDistance` or `Current` |
| `remainingKm` | `max(0, nextDueKm − trackedKm)` |

- The user notice reads: "Only hardware sessions recorded by TreadmillRunner count toward this
  distance. Console-only use is not visible to the app."
- Legacy test (translate as-is), with policy 3 months / 10 km:
  1. Sessions: Hardware Completed 4 km, Hardware Stopped 3 km, Simulator Completed 100 km,
     SystemTest Completed 100 km, Hardware Running 100 km. Expected: `SetupRequired`, tracked
     = 7 km.
  2. Record an event at now − 2 months. Expected: `Current`. Replaying the same op id throws a
     replay conflict.
  3. At now + 2 months: `DueByDate`.
  4. Update the policy to 6 months / 20 km. At now + 2 months: `Current`.
  5. Add Hardware Completed 21 km. At now: `DueByDistance`, `isDue = true`.

## 14. Simulator model (for automated tests)

The Android test suite needs a deterministic fake treadmill at the **GATT level**, below the
`BleCentral` port, so that the real parser, codec, command writer and connection supervisor run
unchanged. Legacy only had a session-level simulator (commands applied instantly). The model below
is richer and is calibrated to §10.

### 14.1 State

- `advertisedName` (default null) and `advertisedServices` (default `[1816, 1826]`).
- `dis = (model "OMEGA Z", firmware "V10.23.17")`. Tests can change it to exercise the downgrade.
- `feature = 0000000003000000`, `speedRange = 5000D0070A00`, `inclineRange = 000078000100`.
- Belt: `actualSpeed`, `targetSpeed`, `actualIncline`, `targetIncline`, `running`. Also
  `safetyKey` (present/pulled) and `controlGranted`.
- Counters: `distanceMeters`, `elapsedSeconds` (reset on Start after Stop when `resetCountersOnStart`).
- `connected` and `cccd{2ACD, 2AD9, 2ADA}`.

### 14.2 Behaviour (defaults; all configurable)

| Parameter | Default | Basis |
|---|---|---|
| `notifyInterval` | 1000 ms | assumption, consistent with ≈1.43 s confirmations |
| `flagsWord` | `0x0008` (speed + inclination) | assumption until Stage A captures it |
| `requestControlResponds` | **false** | observed |
| `responseDelay` | 150 ms | assumption (the response arrived well inside 2 s) |
| `startDelay` | 3.5 s before the belt moves, then instantly at the range minimum | calibrated to 4.4–6.2 s start-to-confirm |
| `speedSlewRate` | 0.5 km/h per s | calibrated to ≈1.43 s for a 0.3 km/h change including cadence |
| `inclineSlewRate` | 1.0 % per s | calibrated to ≈1.45 s for a 0.5 % change |
| `stopDecel` | 1.0 km/h per s | calibrated to ≈1.8 s from 1.0 km/h |

- `07` while stopped and control granted: after the response, wait `startDelay`, then run at the
  range minimum. While running it is a no-op success.
- `08 01`: decelerate to 0 at `stopDecel`. The incline also returns to 0 (`inclineZeroOnStop`,
  default true), as observed in the corrected rerun (0.5 % → 0.0 % after Stop).
- `02`/`03` outside the range: respond `80 op 03` (Invalid Parameter), with no change.
- A motion opcode without granted control: `80 op 05`. With `requestControlResponds = false`,
  writing `00` still grants control silently.
- Console actions: `consoleStart()`, `consoleStop()`, `consoleSetSpeed(x)`, `consoleSetIncline(x)`
  change the belt with no `2AD9` traffic.
- `pullSafetyKey()`: speed goes to 0 immediately; if `2ADA` is subscribed, emit `03`.

### 14.3 Fault injection (each needs at least one scenario test)

1. No response to a motion opcode. The expected outcome is Unknown and no retry.
2. A non-success result code for each code `02`–`05`. Expected: Rejected.
3. A late response for the previous opcode, arriving inside the next command's window. It must be
   ignored.
4. A success response with no telemetry change (the belt ignores the command). Expected: Unknown.
5. A disconnect after the write and before the response, or after the response and before
   telemetry. Expected: Unknown, with a new generation on reconnect.
6. A generation change between Request Control and the motion write. Expected: Rejected with only
   `00` written.
7. Stale speed while incline stays fresh, and the reverse, via per-field notification suppression
   (`flagsWord` alternating `0x0001|0x0008` versus `0x0000`).
8. A notification with no fields (`01 00`). It must be ignored.
9. Implausible values: speed above the range maximum, or incline 20 % when the maximum is 12.
   Expected: Faulted and telemetry dropped. (NaN cannot occur on the wire; test it at the
   ingestion unit level.)
10. Malformed `2ACD` (reserved flags, truncation, trailing byte).
11. Silence: no first notification within 15 s, and a gap of more than 30 s.
12. A power cycle with a DIS change (firmware `V10.23.18`). Expected: durable downgrade before
    Ready.
13. Counter reset after Stop/Start.
14. Anonymous versus named advertisements, and split advertisement/scan-response packets.
15. FFF4 fragmented, coalesced and corrupted frames (reuse `omega-frame-reassembler.json`).
16. GATT status 133 on connect, repeated N times, to test the backoff sequence.
17. A slow belt. With `startDelay` 4 s, the first moving sample arrives inside the 5 s
    post-response confirmation window and Start is Confirmed. With 6 s it arrives after the
    window and Start is Unknown, with no retry.

A **session-level simulator** (origin `Simulator`) still exists for UI demos and scenario tests.
It applies commands instantly and is excluded from totals, progression, maintenance, plan
advancement and Garmin upload. It is never available while a real treadmill is enrolled.

## 15. Open items (must be resolved on Android)

1. The raw `2ACD` flags word, the present fields and the notification cadence of the Omega Z
   (Stage A).
2. Whether `2AD9` offers indicate or only notify on Android's enumeration.
3. Re-verify every command on the Android host stack (DEV-08). Windows evidence does not transfer.
4. Behaviour after a treadmill power cycle while a session is running, and after a safety-key pull,
   with app control enabled (after DEV-08).
5. Speeds above 1.5 km/h and inclines above 1.0 % have never been physically exercised. The ranges
   are bounds, not verified behaviour. Rate-limit increases (09).
6. Whether `2ADA` carries useful safety-key or control-lost signals.

## 16. Golden vector files (`data/ftms/`)

| File | Codec | Status |
|---|---|---|
| `ftms-control-point-codec.json` | `2AD9` encode + response parse | port |
| `ftms-treadmill-data-parser.json` | `2ACD` parse | port |
| `ftms-capability-parser.json` | `2ACC`, `2AD4`, `2AD5` | port |
| `omega-frame-reassembler.json` | `FFF4` framing | port (read-only) |
| `omega-status-decoder.json` | `FFF4` status | port (read-only) |
| `crc-ccitt.json` | CRC-16/CCITT-FALSE | port (utility) |
| `battery-level-parser.json` | `2A19` | port |
| `omega-z-advertisement-matcher.json` | discovery rule | port |
| `omega-command-encoder.reference.json` | vendor `FFF3` frames | **reference only, never ported** |

Every entry has `source`:

- `legacy-test`: copied from the previous automated tests.
- `derived`: computed from the stated rule.
- `synthetic`: constructed for coverage.

The Kotlin tests must load these files directly (a parameterized test per file), so that the spec
and the tests cannot drift apart.
