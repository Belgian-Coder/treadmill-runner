---
title: 07 — Exports and backup
type: spec
status: draft-v1
audience: agent-and-developer
updated: 2026-09-24
---

# 07 — Exports and backup

**Scope (owner decision):**
- The only backwards-compatibility requirement is **run (session) data**.
- The **session JSON export `treadmillrunner.session/v1` is the compatibility contract**:
  - The new app must **import** every legacy export.
  - It must **produce** exports with the **same structure** (§2).
- The CSV, TCX, FIT Activity and FIT Workout exports keep their current semantics (§3–§6).
- The legacy backup (`.trb`) is described for reference (§7.1–§7.3). The new app only reads it to extract runs ([01](01-data-model.md) §5). The new backup design is in §7.4.

All golden files were produced by running the **current exporters** on a fixture run: the actual .NET code, not a reimplementation. They live in `data/exports/`:

| File | Content |
|---|---|
| `fixture-session.json` | JSON export of the completed hardware run `4d5e6f70-…` (15 samples, 9 events, a debrief, a pause of 4 s) |
| `fixture-session-interrupted.json` | JSON export of the interrupted simulator run `5a6b7c8d-…` (legacy v1 calories, `{}` configuration, null planned values, a null HR, a progress reset) |
| `session-export-v1.schema.json` | JSON Schema (draft 2020-12) of the contract. Both fixtures validate against it |
| `fixture-session.csv`, `fixture-session-interrupted.csv` | CSV exports |
| `fixture-session.tcx` | TCX export |
| `fixture-session.fit` + `fixture-session.fit.txt` | FIT Activity (binary) and its decoded message dump (FIT SDK field names, values and units) |
| `fixture-workout-definition.json`, `fixture-workout.fit` + `.fit.txt` | The workout revision and its FIT Workout export |
| `fixture-metrics.txt` | The derived metrics of the fixture run (statistics, zones, adherence, the elevation trace, cumulative calories) |
| `legacy-backup-fixture.trb`, `legacy-schema.sql`, `legacy-rows.txt` | The legacy backup of the same data ([01](01-data-model.md) §5) |

**Line endings:**
- The golden text files were generated on Linux and use `\n`. The legacy app runs on Windows and emits `\r\n` in CSV, TCX and indented JSON.
- Line endings are **not significant**: readers accept both, and golden comparisons normalize them.
- The new app writes `\n`.

**Common rules for all session exports:**
- Exports are read-only. They never modify the run.
- They use the **authoritative stored samples at full resolution**, never the 240-point display projection.
- A run with more than **100 000 samples** is refused (HTTP 413 "The session exceeds the bounded export sample limit.").
- An export larger than **64 MiB** is refused (413 "The generated export exceeds 64 MiB.").
- File name: `treadmillrunner-{sessionId as 32 lower-case hex digits}.{csv|fit|tcx|json}`.
- Media types:
  - CSV `text/csv; charset=utf-8`;
  - FIT `application/vnd.ant.fit`;
  - TCX `application/vnd.garmin.tcx+xml`;
  - JSON `application/json; charset=utf-8`.
- **[rewrite]** On the phone: the Android share sheet. On the web: a download ([00](00-plan.md) REC-07).

---

## 1. Units and encodings used by every format
| Quantity | Unit |
|---|---|
| speed | km/h in CSV and JSON; m/s (= km/h ÷ 3.6) in TCX and FIT |
| distance | km in CSV and JSON; m in TCX and FIT |
| incline / grade | % |
| energy | kcal |
| time | UTC |

**Number formatting** (the legacy .NET behaviour):
- JSON and CSV numbers use the **shortest round-trip** representation of the IEEE double: `5`, `0.001388889`, `132.6153846153846`, `3.9999995999999998`.
- There is no exponent for normal magnitudes. NaN and Infinity never occur: values are validated as finite.

---

## 2. Session JSON export — `treadmillrunner.session/v1` (THE contract)

### 2.1 Envelope and encoding
- UTF-8 without BOM. The legacy writer indents with 2 spaces; indentation is not significant.
- Property names are **camelCase**, except inside `session.controllerConfiguration`, which embeds the stored snapshot verbatim (PascalCase in legacy runs).
- **Property order** (the legacy writer's order, which the new writer must reproduce) is the order of the tables below. Readers must not depend on order.
- **UUIDs**: lower-case canonical `8-4-4-4-12`. Readers accept any case.
- **Instants** (the .NET round-trip form): `yyyy-MM-ddTHH:mm:ss[.fffffff]+00:00`, with trailing fractional zeros **and** the dot removed when the fraction is zero. Examples: `2026-09-01T06:30:00+00:00`, `2026-09-01T06:30:08.5+00:00`, `2026-09-01T06:30:08.1999999+00:00`, `2026-09-24T17:10:24.1162687+00:00`.
  - Readers accept 0–9 fractional digits and any offset, including `Z`, and convert to UTC.
  - Writers use `+00:00` and up to 7 digits. The new app stores microseconds, so it writes up to 6.
- **Enums are integers (ordinals)** in v1. Readers must also accept the enum **names** (case-sensitive). Writers emit integers.

| Field | Ordinals |
|---|---|
| `session.state` | 0 Idle, 1 ArmedWaitingForPhysicalStart, 2 Running, 3 PausedWaitingForPhysicalResume, 4 Completed, 5 Stopped, 6 Interrupted, 7 Faulted |
| `session.origin` | 0 Legacy, 1 Hardware, 2 Simulator, 3 SystemTest |
| `session.selection.source` | 0 Legacy, 1 Manual, 2 Library, 3 Calendar, 4 Program |
| `session-paused.reason` | 0 WebControl, 1 PhysicalConsole, 2 TreadmillStopped |
| `device-*.deviceRole` | 0 Treadmill, 1 HeartRate |
| `control-lease.kind` | 0 Acquired, 1 Renewed, 2 Released, 3 Expired, 4 Reclaimed |

- **Durations** are numbers of seconds (`durationSeconds`, `elapsedSeconds`, `previousWorkoutElapsedSeconds`) or milliseconds (`telemetryAgeMilliseconds`). They are doubles, and may be fractional.
- **Nulls are written explicitly** for every nullable field. No field is ever omitted.

### 2.2 Root
| # | Property | Type | Rule |
|---|---|---|---|
| 1 | `schema` | string | Exactly `treadmillrunner.session/v1` |
| 2 | `unitSystem` | string | Exactly `Metric` |
| 3 | `exportedAtUtc` | instant | The time of export (not reproducible; excluded from golden comparisons) |
| 4 | `session` | object | §2.3 |
| 5 | `samples` | array | §2.5. **All** stored samples, ascending by `sequence` |
| 6 | `events` | array | §2.6. All events, ascending by `occurredAt` (the new writer breaks ties by event ID) |

### 2.3 `session`
| # | Property | Type | Null | Rule / source |
|---|---|---|---|---|
| 1 | `sessionId` | uuid | no | |
| 2 | `userProfileId` | uuid | no | |
| 3 | `userProfileName` | string (1–100) | no | Snapshot at arm |
| 4 | `workoutRevisionId` | uuid | no | |
| 5 | `workoutTitle` | string (1–160) | no | Snapshot at arm |
| 6 | `armedAt` | instant | no | |
| 7 | `selection` | object | no | §2.4 |
| 8 | `origin` | int | no | ordinal |
| 9 | `controllerConfiguration` | object (or string) | no | The stored snapshot, parsed and embedded **as-is** ([01](01-data-model.md) §4.1). `{}` for runs without one. If the stored text was not valid JSON, the raw text is embedded as a JSON **string** |
| 10 | `metricAlgorithmVersion` | string | no | `estimated-calories/acsm-speed-grade-v2`, or legacy `estimated-calories/v1` |
| 11 | `state` | int | no | ordinal. Any state can be exported, including non-terminal ones |
| 12 | `startedAt` | instant | yes | |
| 13 | `endedAt` | instant | yes | |
| 14 | `durationSeconds` | number ≥ 0 | no | Active duration |
| 15 | `distanceKilometers` | number ≥ 0 | no | |
| 16 | `estimatedKilocalories` | number ≥ 0 | no | **The stored session value** (no recalculation in this format) |
| 17 | `averageHeartRateBpm` | number | yes | Stored |
| 18 | `maximumHeartRateBpm` | int 1–250 | yes | Stored |
| 19 | `averageSpeedKph` | number ≥ 0 | no | Stored |
| 20 | `averageInclinePercent` | number | no | Stored |
| 21 | `debrief` | object | yes | Present when a debrief was saved:<br>• `sessionId` (uuid, = the session);<br>• `perceivedExertion` (int 1–10 or null);<br>• `note` (string ≤ 1000, trimmed, or null);<br>• `updatedAt` (instant). |

### 2.4 `session.selection`
| # | Property | Type | Rule |
|---|---|---|---|
| 1 | `source` | int | WorkoutSelectionSource ordinal |
| 2 | `programRunId` | uuid or null | Set only when `source = 4` (Program) |
| 3 | `programItemId` | uuid or null | as above |
| 4 | `recordPolarH10Memory` | bool | The per-run H10 memory opt-in |
| 5 | `replaceExistingPolarH10Recording` | bool | An arm-time request flag. **Always `false`** in exports; not persisted |
| 6 | `replacePolarH10ExerciseId` | string or null | An arm-time request value. **Always `null`** in exports |

### 2.5 `samples[]`
| # | Property | Type | Null | Rule |
|---|---|---|---|---|
| 1 | `sequence` | int ≥ 0 | no | Strictly increasing |
| 2 | `capturedAt` | instant | no | Non-decreasing |
| 3 | `elapsedSeconds` | number ≥ 0 | no | `elapsedMilliseconds / 1000` |
| 4 | `plannedSpeedKph` | number ≥ 0 | yes | |
| 5 | `requestedSpeedKph` | number ≥ 0 | no | |
| 6 | `measuredSpeedKph` | number ≥ 0 | no | |
| 7 | `plannedInclinePercent` | number | yes | |
| 8 | `requestedInclinePercent` | number | no | |
| 9 | `measuredInclinePercent` | number | no | |
| 10 | `heartRateBpm` | int | yes | 30–250 when present (stored values outside that range are null) |
| 11 | `distanceKilometers` | number ≥ 0 | no | Cumulative |
| 12 | `estimatedKilocalories` | number ≥ 0 | no | Cumulative, **as stored** |
| 13 | `telemetryAgeMilliseconds` | number ≥ 0 | no | |
| 14 | `metricAlgorithmVersion` | string | no | Equals the session's value |

### 2.6 `events[]`
Every event starts with `eventType` then `occurredAt`, followed by the kind-specific fields **in this order**:

| `eventType` | Fields after `occurredAt` |
|---|---|
| `manual-speed-override` | `expectedSpeedKph` (number), `observedSpeedKph` (number) |
| `manual-incline-override` | `previousInclinePercent` (number), `requestedInclinePercent` (number) |
| `workout-step-transition` | `completedStepIndex` (int), `currentStepIndex` (int or null), `cue` (string or null) |
| `workout-progress-reset` | `previousStepIndex` (int), `previousWorkoutElapsedSeconds` (number) |
| `session-paused` | `reason` (int, pause reason) |
| `session-resumed` | – |
| `device-disconnected` | `deviceRole` (int), `reason` (string or null) |
| `device-reconnected` | `deviceRole` (int) |
| `session-warning` | `code` (string), `message` (string) |
| `control-lease` | `kind` (int), `leaseId` (uuid), `holderId` (string) |
| `session-completed` | – |
| `session-stopped` | – |
| `session-interrupted` | `reason` (string or null) |
| `session-faulted` | `code` (string), `message` (string) |

Note the difference from the stored event details ([05](05-sessions-and-recording.md) §6): the export converts the progress-reset `previousWorkoutElapsed` (a TimeSpan) into `previousWorkoutElapsedSeconds` (a number), and puts `eventType`/`occurredAt` first.

### 2.7 Golden example (excerpt of `fixture-session.json`)
```json
{
  "schema": "treadmillrunner.session/v1",
  "unitSystem": "Metric",
  "exportedAtUtc": "2026-09-24T17:10:24.1162687+00:00",
  "session": {
    "sessionId": "4d5e6f70-8192-4a34-9c5d-6e7f80910213",
    "userProfileId": "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0",
    "userProfileName": "Marc",
    "workoutRevisionId": "6d71930d-d3dd-4db9-b68c-b474dcb2e6e1",
    "workoutTitle": "Spec fixture intervals",
    "armedAt": "2026-09-01T06:29:50+00:00",
    "selection": { "source": 2, "programRunId": null, "programItemId": null,
                   "recordPolarH10Memory": false, "replaceExistingPolarH10Recording": false,
                   "replacePolarH10ExerciseId": null },
    "origin": 1,
    "controllerConfiguration": { "Mode": "hardware:ftms:Ftms", "HeartRateController": "shadow",
      "Profile": { "WeightKilograms": 72.5, "MaximumHeartRateBpm": 190, "MaximumSpeedKph": 12,
                   "HeartRateZones": [ { "Number": 1, "Name": "Warm up", "MinimumBpm": 95, "MaximumBpm": 113 }, "…" ],
                   "HeartRateController": { "IncreaseStepKph": 0.2, "IncreaseCooldownSeconds": 30,
                                            "DecreaseStepKph": 0.5, "DecreaseCooldownSeconds": 15 } },
      "HeartRateSourceLabel": "Polar H10 ABCD1234", "HeartRateSourceKind": "ChestStrap",
      "HeartRateSourceFamily": "Polar", "Treadmill": { "IdentityLabel": "OMEGA Z", "…": "…" } },
    "metricAlgorithmVersion": "estimated-calories/acsm-speed-grade-v2",
    "state": 4,
    "startedAt": "2026-09-01T06:30:00+00:00",
    "endedAt": "2026-09-01T06:30:18+00:00",
    "durationSeconds": 14,
    "distanceKilometers": 0.02675,
    "estimatedKilocalories": 2.052846,
    "averageHeartRateBpm": 132.6153846153846,
    "maximumHeartRateBpm": 156,
    "averageSpeedKph": 6.878571428571429,
    "averageInclinePercent": 1.6071428571428572,
    "debrief": { "sessionId": "4d5e6f70-8192-4a34-9c5d-6e7f80910213", "perceivedExertion": 6,
                 "note": "Felt controlled; legs fresh.", "updatedAt": "2026-09-01T06:32:18+00:00" }
  },
  "samples": [
    { "sequence": 7, "capturedAt": "2026-09-01T06:30:07+00:00", "elapsedSeconds": 7,
      "plannedSpeedKph": 8, "requestedSpeedKph": 8.5, "measuredSpeedKph": 8.5,
      "plannedInclinePercent": 2, "requestedInclinePercent": 2, "measuredInclinePercent": 2,
      "heartRateBpm": null, "distanceKilometers": 0.012166667, "estimatedKilocalories": 0.878748,
      "telemetryAgeMilliseconds": 190, "metricAlgorithmVersion": "estimated-calories/acsm-speed-grade-v2" }
  ],
  "events": [
    { "eventType": "device-disconnected", "occurredAt": "2026-09-01T06:30:08.1999999+00:00",
      "deviceRole": 1, "reason": "Heart-rate telemetry became stale." },
    { "eventType": "session-paused", "occurredAt": "2026-09-01T06:30:08.5+00:00", "reason": 2 }
  ]
}
```
In the full file:
- Samples 9–14 have `capturedAt` 4 s later than `startedAt + elapsedSeconds`, because of the pause from 08.5 s to 12.5 s.
- The first sample has `estimatedKilocalories` 0.

`fixture-session-interrupted.json` shows:
- `origin` 2, `state` 6, `selection.source` 1;
- `controllerConfiguration: {}`;
- null `plannedSpeedKph`/`plannedInclinePercent`, and a null `heartRateBpm`;
- `debrief: null`;
- `session-paused` reason 2, `workout-progress-reset` with `previousWorkoutElapsedSeconds: 3`, and `session-interrupted` with reason `"Simulator reset."`.

### 2.8 Importing a v1 export (new app)
1. The size must be ≤ 64 MiB. Parse strict JSON: no comments, no trailing commas, max depth 64.
2. Require `schema == "treadmillrunner.session/v1"` and `unitSystem == "Metric"`. Otherwise reject ("Unsupported session export schema.").
3. Validate against `session-export-v1.schema.json`. Additionally:
   - `samples.length ≤ 100 000`;
   - sequences strictly increasing;
   - `capturedAt` and `elapsedSeconds` non-decreasing;
   - every sample's `metricAlgorithmVersion` equals the session's;
   - `startedAt ≥ armedAt` and `endedAt ≥ startedAt` when set;
   - `durationSeconds ≤ endedAt − startedAt`, with a 1 ms tolerance.
4. Enums: accept an ordinal or a name. Reject unknown values, **except** an unknown `eventType`: keep that event as an opaque JSON object and show it generically.
5. **Idempotency** by `sessionId`:
   - If the ID doesn't exist, insert the run.
   - If it exists with identical content, skip ("already imported").
   - If it exists with different content, report a conflict, and never overwrite.
   - Event IDs are not in the export; generate UUIDv7s. Two events are identical when (`eventType`, `occurredAt`, and the fields) are equal.
6. Mapping:
   - `elapsedSeconds × 1000` → `elapsedMilliseconds`;
   - `previousWorkoutElapsedSeconds` → the stored TimeSpan;
   - `controllerConfiguration` is stored **verbatim**: re-serialized compactly, key order and case preserved;
   - `debrief` → `perceivedExertion`, `debriefNote`, `debriefUpdatedAt`;
   - `selection.replace*` are ignored;
   - `exportedAtUtc` is ignored;
   - sample `heartRateBpm` outside 30–250 becomes null.
7. A non-terminal `state` is imported as `Interrupted`, with an added `session-interrupted` event ("Imported from export while unfinished.") and the summary rules of [05](05-sessions-and-recording.md) §8.4.
8. Mark the run `imported = true`. It is never auto-uploaded to Garmin. Soft references (profile, workout revision, program) may point to nothing.
9. Show a preview before committing: the run, its date, distance, sample and event counts, and conflicts. Commit in one transaction.

### 2.9 Producing a v1 export (new app)
- The same structure, property names, property order, types, null handling and enum ordinals as §2.2–2.6.
- `controllerConfiguration` is the stored snapshot, embedded as parsed JSON. New runs store the legacy PascalCase shape ([01](01-data-model.md) §4.1).
- `estimatedKilocalories` and the other session summary fields are the stored values. The samples are the stored values.
- **Round-trip law:** `export(import(E))` equals `E` as JSON values, with these exceptions:
  - `exportedAtUtc` differs;
  - instants are compared as instants (the fraction may be shortened to microseconds);
  - numbers are compared as IEEE doubles;
  - event ties may be ordered by ID.

  The golden test runs this law on both fixtures.

---

## 3. CSV export
- One header line, then one row per stored sample, ascending by sequence.
- Encoding: UTF-8 without BOM. Separator `,`. **No quoting** (no field can contain a comma).
- A null value is an empty field. Numbers use the shortest round-trip form.

Header (exact):
```
captured_at_utc,elapsed_seconds,planned_speed_kph,requested_speed_kph,measured_speed_kph,planned_incline_percent,requested_incline_percent,measured_incline_percent,heart_rate_bpm,distance_km,estimated_kcal,telemetry_age_ms
```
| Column | Value |
|---|---|
| captured_at_utc | `yyyy-MM-ddTHH:mm:ss.fffffff+00:00`: **always 7 fractional digits** (for example `2026-09-01T06:30:00.0000000+00:00`) |
| elapsed_seconds | elapsed in seconds |
| planned_speed_kph … measured_incline_percent | as stored; empty when null |
| heart_rate_bpm | integer or empty |
| distance_km | cumulative km |
| estimated_kcal | **v2 runs**: the stored cumulative value; **on the last row**, `max(sample value, session.estimatedKilocalories)`.<br>**v1 runs with a snapshot weight**: recalculated cumulative calories ([05](05-sessions-and-recording.md) §7.1), row by row.<br>**v1 without a weight**: as stored. |
| telemetry_age_ms | formatted `0.###` (at most 3 decimals, no trailing zeros) |

Golden rows (from `fixture-session.csv`):
```
2026-09-01T06:30:00.0000000+00:00,0,5,5,5,1,1,1,110,0,0,180
2026-09-01T06:30:07.0000000+00:00,7,8,8.5,8.5,2,2,2,,0.012166667,0.878748,190
2026-09-01T06:30:18.0000000+00:00,14,7,7,7,1,1,1,149,0.02675,2.052846,200
```
Interrupted fixture row with null planned values: `2026-09-02T18:00:06.0000000+00:00,2,,4,4,,0,0,,0.002222222,0.1,250`.

---

## 4. TCX export (Training Center Database v2)
Requires `startedAt` and `endedAt`; otherwise the error is "Only a terminal session with start and end timestamps can be exported as TCX Activity.". Samples are used as stored (not normalized). UTF-8 without BOM, with an XML declaration `<?xml version="1.0" encoding="utf-8"?>`, indented by 2 spaces.

```xml
<TrainingCenterDatabase xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2"
    xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Running">
      <Id>{startedAt, round-trip form, 7 digits}</Id>
      <Lap StartTime="{startedAt}">
        <TotalTimeSeconds>{durationSeconds, 0.###}</TotalTimeSeconds>
        <DistanceMeters>{distanceKm×1000, 0.###}</DistanceMeters>
        <Calories>{round(estimatedKilocalories) to nearest, ties to even, clamped 0..65535}</Calories>
        <AverageHeartRateBpm><Value>{round(avgHr), ties to even, clamped 0..255}</Value></AverageHeartRateBpm>   <!-- only if present -->
        <MaximumHeartRateBpm><Value>{maxHr}</Value></MaximumHeartRateBpm>                                          <!-- only if present -->
        <Intensity>Active</Intensity>
        <TriggerMethod>Manual</TriggerMethod>
        <Track>
          <Trackpoint>                                         <!-- one per sample -->
            <Time>{capturedAt, 7 digits}</Time>
            <DistanceMeters>{km×1000, 0.###}</DistanceMeters>
            <HeartRateBpm><Value>{hr}</Value></HeartRateBpm>  <!-- only if the sample has HR -->
            <Extensions><ns3:TPX><ns3:Speed>{measuredKph/3.6, 0.###}</ns3:Speed></ns3:TPX></Extensions>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
```
- One lap. The summary uses the **stored** session values.
- **Not written:** altitude, cadence, position, `Notes`, `Creator`, watts.
- Golden (`fixture-session.tcx`):
  - lap `TotalTimeSeconds 14`, `DistanceMeters 26.75`, `Calories 2`, average HR `133`, maximum `156`;
  - the second trackpoint has `DistanceMeters 1.389` and `Speed 1.389`;
  - the trackpoint at 06:30:07 has no `HeartRateBpm`.

---

## 5. FIT Activity export
Encoded with the Garmin FIT SDK, protocol **2.0**. The file must pass the SDK `isFIT` and `checkIntegrity` (header and CRC) checks. Only a run with `startedAt` and `endedAt` can be exported: "Only a completed session with start and end timestamps can be exported as FIT Activity."

### 5.1 Inputs
- **The samples are normalized first** ([05](05-sessions-and-recording.md) §7.11).
- `start = startedAt`.
- `end` = the **effective end**:
  - `endedAt` for Completed and Stopped;
  - for Interrupted and Faulted:
    ```
    durationEnd = start + duration
    timelineEnd = last sample capturedAt (or durationEnd if no samples)
    lastTimer = latest session-paused/resumed occurredAt within [start, endedAt], if later than timelineEnd
    effectiveEnd = clamp(max(timelineEnd, durationEnd), start, endedAt)
    ```
    This keeps a late reconciliation time (for example an interruption 8 h later) out of the elapsed time.
- FIT timestamps are **whole seconds** since 1989-12-31T00:00:00Z. Fractions are **truncated** (08.5 s → 08 s).
- `serial = the first 32-bit group of the session UUID read as an unsigned integer`. This equals the .NET `BitConverter.ToUInt32(guid.ToByteArray(), 0)`. For `4d5e6f70-…` it is `0x4D5E6F70 = 1298034544`.
- `statistics` = sample statistics over the normalized samples with the snapshot weight ([05](05-sessions-and-recording.md) §7.4).
- `elevation` = the elevation trace ([05](05-sessions-and-recording.md) §7.3).
- **Calories**:
  - v2: `authoritative = max(session.estimatedKilocalories, last sample cumulative)`;
  - v1: `statistics.estimatedKilocalories ?? session.estimatedKilocalories`;
  - `totalCalories = clamp(round(authoritative) with ties to even, 0, 65535)`.
- `avgHr = statistics.average ?? session.average`; `maxHr = statistics.max ?? session.max`; `minHr = statistics.min`.
- **FIT HR**: `clamp(round(x) half away from zero, 0, 254)`. **254 is the maximum written**, so a stored 255+ never becomes the FIT invalid sentinel 255.
- `timer = durationSeconds`.
- `elapsed = max(timer, (end − start) seconds)`.
- `distance = km × 1000`.
- `avgSpeed = duration > 0 ? distance_m / durationSeconds : 0`.
- `maxSpeed = statistics.maximumSpeedKph / 3.6` (if present).

### 5.2 Message sequence
| # | Message | Fields |
|---|---|---|
| 1 | `file_id` | `type` = activity (4); `manufacturer` = development (255); `product` = 1; `serial_number` = serial; `time_created` = start; `product_name` = "TreadmillRunner" |
| 2 | `device_info` | `timestamp` = start; `device_index` = 0; `manufacturer` = 255; `product` = 1; `product_name` = "TreadmillRunner" |
| 3 | `event` | `timestamp` = start; `event` = timer (0); `event_type` = start (0) |
| 4… | `record` × N, interleaved with timer `event`s | Before writing a record, write every pending pause/resume timer event whose timestamp ≤ the record's timestamp. Record fields below |
| … | remaining timer events | |
| n−3 | `event` | `timestamp` = end; timer; `event_type` = stop_all (4) |
| n−2 | `lap` | below |
| n−1 | `session` | below |
| n | `activity` | `timestamp` = end; `total_timer_time` = timer; `num_sessions` = 1; `type` = manual (0); `event` = activity (26); `event_type` = stop (1) |

**Timer events** come from `session-paused` (stop, `event_type` 1) and `session-resumed` (start, 0), filtered and normalized:
- keep events in `[start, effectiveEnd]`;
- sort by time, with a pause before a resume at the same instant;
- start in the "running" state:
  - a pause is written only while running (running → paused);
  - a resume is written only while paused;
  - redundant events are dropped.

**Record fields** (per normalized sample `i`):

| FIT field | Value | Wire scale (resolution) |
|---|---|---|
| timestamp | capturedAt | s |
| speed, enhanced_speed | measured km/h ÷ 3.6 | 1/1000 m/s |
| distance | km × 1000 | 1/100 m |
| heart_rate | FIT HR, only if the sample has HR | bpm |
| grade | measured incline % | 1/100 % |
| altitude, enhanced_altitude | elevation trace [i] (starts at 0) | 1/5 m, offset 500 m. Values are quantized to 0.2 m (0.0139 → 0) |
| vertical_speed | `(elev[i] − elev[i−1]) / (elapsed[i] − elapsed[i−1])`; 0 for i = 0, or when Δt = 0 | 1/1000 m/s |
| zone | only if the snapshot has **exactly zones 1–5**, i ≥ 1, the HR is present and falls in a zone: the zone number 1–5 | – |

**Lap** (`message_index` 0) and **session** (`message_index` 0) share these fields:
- `timestamp` = end; `start_time` = start;
- `total_elapsed_time` = elapsed; `total_timer_time` = timer; `total_distance` = distance;
- `sport` = running (1); `sub_sport` = treadmill (1);
- `avg_speed` and `enhanced_avg_speed` = avgSpeed; `total_calories`;
- `max_speed` and `enhanced_max_speed` (if present);
- `total_moving_time` and `active_time` = movingTime (if present);
- `avg_grade`, `avg_pos_grade`, `avg_neg_grade` (each if present);
- `max_neg_grade` (only if min incline < 0); `max_pos_grade` (only if max incline > 0);
- `total_ascent` = floor(ascent) as an integer; `total_fractional_ascent` = ascent − floor (clamped 0..65535); the same for descent;
- `avg_pos_vertical_speed`, `avg_neg_vertical_speed`, `max_pos_vertical_speed`, `max_neg_vertical_speed` (each if present);
- `time_in_hr_zone[0..4]` = seconds per zone (five-zone snapshot only; [05](05-sessions-and-recording.md) §7.5 with the FIT zone rule);
- `avg_heart_rate`, `min_heart_rate`, `max_heart_rate` (each if present).

Lap only:
- `event` = lap (9); `event_type` = stop (1);
- `lap_trigger` = session_end (7); `intensity` = active (0).

Session only:
- `event` = session (8); `event_type` = stop (1); `trigger` = activity_end (0);
- `sport_profile_name` = "TreadmillRunner";
- `first_lap_index` = 0; `num_laps` = 1.

**Never written by the local exporter:**
- position (lat/long);
- cadence, power, temperature, respiration, running dynamics (stance, vertical oscillation, step length);
- `compressed_speed_distance`;
- developer fields and field descriptions;
- training effect, anaerobic training effect, training stress score, normalized power;
- `time_in_zone`, `split` and `split_summary` messages;
- `user_profile`, `zones_target` and `hr_zone` messages;
- `activity.local_timestamp`;
- HRV;
- more than one lap or session.

The Garmin **merge** (which may carry watch-only fields onto this timeline) is specified in [11](11-garmin.md).

### 5.3 Golden (`fixture-session.fit`, 1197 bytes; decoded in `fixture-session.fit.txt`)
- 24 messages:
  - `file_id` (serial 1298034544, `time_created` 1157178600 = 2026-09-01T06:30:00Z);
  - `device_info`;
  - timer start;
  - 15 records;
  - timer stop at 1157178608, before record 9 (the 08.5 pause, truncated);
  - timer start at 1157178612, before the record at …13;
  - stop_all at 1157178618;
  - lap, session, activity.
- Lap and session:
  - `total_elapsed_time` 18 s; `total_timer_time` 14 s; `total_distance` 26.75 m; `total_calories` 2;
  - `avg_speed` 1.911 m/s; `max_speed` 2.361;
  - `avg_heart_rate` 133; `max_heart_rate` 156; `min_heart_rate` 110;
  - `avg_grade` 1.61; `avg_pos_grade` 1.85; `max_pos_grade` 3;
  - `total_moving_time` 14; `total_ascent` 0 + fractional 0.46 m;
  - `avg_pos_vertical_speed` 0.036; `max_pos_vertical_speed` 0.067;
  - `time_in_hr_zone` 1, 5, 5, 2, 0 s.
- The record without HR decodes `heart_rate` as 255 (invalid) and `zone` as 255 (absent).

Legacy exporter tests (fixture: three samples at 0/1/2 s with speeds 0/8/4 km/h, inclines 0/2/0 %, HR 135/150/120, cumulative km 0 / 8/3600 / 12/3600, calories 0/0.5/1, snapshot zones 100–119, 120–129, 130–139, 140–149, 150–200, weight 70, algorithm label "v1"):

| # | Given | Expected |
|---|---|---|
| FIT-1 | the fixture | a valid FIT (header and CRC); `file_id` activity; session avg HR **135**, max **150**, min **120**; moving time **2 s**, active time 2; max speed 2.221–2.223 m/s; avg grade **1**; ascent 0; descent 0; lap calories **0**; lap max HR 150; device product name "TreadmillRunner" |
| FIT-2 | the fixture relabelled v2, stored kcal 42.6 | session `total_calories` **43**; the CSV last row = max(sample, 42.6), other rows as stored |
| FIT-3 | v2, stored 0.4 (the last sample is 1) | `total_calories` **1**; the CSV last row = 1 |
| FIT-4 | the sample 1 HR forced to 251 / 255 / 500 | record 1 HR = **min(hr, 254)**; session max HR the same |
| FIT-5 | elevation fixture: km 0 / 0.1 / 0.2, inclines 0 / +10 / −5 %, HR 135/150/120 | `total_ascent` **9** + fractional 0.950–0.951; descent **4** + 0.989–0.991; avg_pos_grade 10; avg_neg_grade −5; `time_in_hr_zone[1]` = 1 and `[4]` = 1; record 1 vertical speed > 0, record 2 < 0; record 1 zone **5**, record 2 zone **2**; last record enhanced altitude 4.9–5.1 |
| FIT-6 | a legacy regression: sample 1 captured +1 h with elapsed 1 h | normalized to samples 0 and 2; valid FIT |
| FIT-7 | Interrupted, endedAt = start + 8 h, one pause 1 s after the last sample | elapsed = max(duration, last sample, pause time) − start (≈ 3 s); 3 timer events (start, stop, stop_all at the effective end); session, lap and activity timestamps = the effective end |
| FIT-8 | pause at +3 s, resume at +7 s, samples at 0/3/7/12 s (elapsed 0/3/4/8) | events start@0, stop@3, start@7, stop_all@12; elapsed 12 s; timer 8 s (verified through the merge path, [11](11-garmin.md)) |

---

## 6. FIT Workout export
It exports one immutable workout revision (definition JSON, [02](02-workouts.md)). Protocol 2.0.

| Message | Fields |
|---|---|
| `file_id` | `type` = workout (5); manufacturer 255; product 1; `serial_number` = the first 32-bit group of the **revision** UUID; `time_created` = the revision `createdAt`; `product_name` "TreadmillRunner" |
| `workout` | `wkt_name` = the title; `sport` = running (1); `num_valid_steps` = the number of step messages, **including repeat markers** |
| `workout_step` × n | See below. `message_index` is sequential from 0 |

**Blocks are flattened depth-first:**
- A **step** gets:
  - `wkt_step_name` = the cue, or "Step {index+1}";
  - duration:
    - time goal → `duration_type` time (0), `duration_time` = seconds (wire value in ms);
    - distance goal → `duration_type` distance (1), `duration_distance` = metres (wire value in cm);
  - target:
    - `open` → `target_type` open (2);
    - `fixed` → speed (0), with custom low = high = km/h ÷ 3.6 (wire value in mm/s);
    - `ramp` → speed, with low = min(start, end) ÷ 3.6 and high = the max;
    - `heartRate` → heart_rate (1), with custom low/high = bpm + **100** (the FIT custom-HR offset);
    - `heartRateZone` → heart_rate, with `target_hr_zone` = the zone number;
  - `notes` = the space-joined list of:
    1. the original notes (if any);
    2. for a ramp: "Speed ramp {start}-{end} km/h; FIT stores the endpoints as a target range.";
    3. for HR targets: "Treadmill speed safety bounds remain in the immutable TreadmillRunner revision and are not a standard FIT workout target.";
    4. the incline: "Treadmill incline {p}%." (fixed) or "Treadmill incline ramp {a}-{b}%." (ramp).

    Numbers use the format `0.##`.
- A **repeat** writes its children first, then a marker step:
  - `wkt_step_name` "Repeat {n} times";
  - `duration_type` repeat_until_steps_cmplt (6);
  - `duration_step` = the index of the first child;
  - `target_type` open;
  - `repeat_steps` = n.
- **Errors:**
  - 0 steps or more than 65 535;
  - an unknown block kind;
  - a goal other than time or distance ("FIT export supports time and distance workout goals only.");
  - an unknown speed kind.

**Golden** (`fixture-workout.fit.txt`):
- Warm up 5 s @ 5 km/h → speed 1389 mm/s, notes "Treadmill incline 1%.";
- repeat 2 × {Fast 3 s @ 8 km/h, notes "Stay tall Treadmill incline 2%."; Ease 2 s ramp 8→6, low 1667, high 2222};
- marker "Repeat 2 times" (`duration_step` 1, `repeat_steps` 2);
- Zone 3 200 m (wire 20000), `target_hr_zone` 3;
- HR hold 60 s, custom HR 230–250 (130–150 bpm);
- `num_valid_steps` 6.
- Legacy test: a 60 s step at 7.2 km/h / 1.5 % → duration 60, target speed, low ≈ 2.0 m/s, notes contain "incline 1.5%".

---

## 7. Backup and restore

### 7.1 Legacy full backup (`.trb`) — reference
- **Format**: a plain SQLite database file, copied page by page from the live database with the SQLite online-backup API. The details and decoding rules are in [01](01-data-model.md) §5.1–5.3.
- **Manual download**:
  - It requires an idle live session: no session, or a terminal one. Otherwise 409 "Backup download requires an idle session."
  - An online backup is made to a temporary file. If it is > 256 MiB: 413 "The database exceeds the 256 MiB backup limit."
  - Response: `treadmillrunner-{yyyyMMdd-HHmmss}.trb`, `application/vnd.treadmillrunner.backup`.
- **Automatic verified backups** (policy: destination folder, interval 1–168 h (default 24), retention 2–60 (default 14), enabled):
  - A worker wakes every 15 min. It runs when the latest verification is older than the interval.
  - It waits for an idle session (the maintenance boundary). Otherwise "Backup verification waits until the active workout is idle."
  - Steps:
    1. Online backup to `integrity-backup-{yyyyMMdd-HHmmssfff}-{guid:N}.tmp`.
    2. Require 0 < size ≤ 256 MiB.
    3. Open the copy separately and run a **full integrity check**.
    4. Compute the SHA-256 (upper-case hex).
    5. Rename to `integrity-last-known-good-{same suffix}.db`.
    6. Prune the `integrity-last-known-good-*.db` files beyond the retention count, newest first by write time. Never delete the file just created. Locked files are skipped.
    7. Delete the temporary files and their `-wal`/`-shm` sidecars. Stale `.tmp` files older than a minimum age are cleaned up later.
  - A **verification receipt** is written: `Verified` with the detail "Isolated full SQLite integrity check passed. SHA-256 {hex}." and the byte count; or `Failed` with the error message.

### 7.2 Legacy restore — preview semantics (reference)
1. **Preview**: upload the raw file body.
   - It requires an idle session. The size must be 0 < n ≤ 256 MiB ("A non-empty SQLite backup no larger than 256 MiB is required.").
   - Save it to a temporary file. The header must be `SQLite format 3\0` ("The uploaded file is not a SQLite database.").
   - `PRAGMA integrity_check` must be `ok`.
   - Count: applied migrations, `UserProfiles`, `Workouts`, `WorkoutSessions`.
   - Compute the SHA-256 (upper-case hex).
   - Return `{token (uuid), sizeBytes, sha256, appliedMigrationCount, profileCount, workoutCount, sessionCount, expiresAtUtc (= now + 15 min)}`.
   - Expired previews are deleted with their file.
2. **Confirm**: `{token, confirmation}`.
   - `confirmation` must be **exactly `RESTORE`** (400 otherwise).
   - Enter the maintenance boundary: 409 if it is busy or a session is active.
   - The token is single-use. An unknown or expired token gives 404 "The restore preview expired or was already consumed."
   - Reset the live session, abandoning startup recovery. If the reset can't complete, the preview is **returned** (its expiry is extended to at least now + 5 min) and the response is 409 "retry".
   - **Restore**:
     1. Migrate the candidate to the current schema.
     2. Integrity check.
     3. **Semantic validation**: no pending migrations; every model table is queryable; `PRAGMA foreign_key_check` is empty; the JSON columns (`CapabilitiesJson` nullable, `WarningSummaryJson`, `ControllerConfigurationJson`, `DetailsJson`, `OutcomeJson`) parse strictly; every workout definition is a valid schema-v1 workout within the import size limit.
     4. Make a **rollback copy** of the live database.
     5. Page-copy the candidate over the live database.
     6. Re-verify the integrity and semantics of the live database. **On any failure, copy the rollback back.**
   - Then reload the state with restored-database reconciliation (interrupt the unfinished sessions of the restored data), refresh the devices, run a live integrity check, and return `{restored, preview, stateReloaded, stateReloadError, databaseIntegrity}`.

### 7.3 What the new app does with legacy backups
It only **extracts runs** from a `.trb` ([01](01-data-model.md) §5), with the preview → confirm flow above:
- counts of runs, samples and events;
- a date range;
- conflicts.

It never restores a legacy database wholesale.

### 7.4 New app backups ([00](00-plan.md) §7.3; summary)
- Automatic verified backups:
  - after each completed session, daily, and before every update or restore;
  - `VACUUM INTO`, then `PRAGMA integrity_check` on the copy, then a verification receipt;
  - retention 2–60, default 14.
- Copies go to the external folder (microSD/USB, through SAF) and to the NAS over SMB. Uploads are atomic (`*.tmp`, read-back SHA-256, rename without replace). The app prunes only its own files, keeping 30 on the NAS.
- The file format is `.trb2`, a ZIP:
  - `manifest.json`: app version, schema version, created-at, row counts, and the SHA-256 of each entry;
  - `db.sqlite`;
  - `blobs/`.

  It is encrypted by default: AES-GCM with a PBKDF2 key from the backup passphrase.
- **Restore:**
  - Always preview first: counts, date range, app and schema version, and what will be replaced.
  - A safety backup is taken first.
  - An older schema is migrated; a newer schema is refused.
  - An integrity check runs afterwards.
- It is blocked while a session is non-terminal.

---

## 8. Test checklist
- [ ] **EXP-01** Both JSON fixtures validate against `session-export-v1.schema.json`. A negative case (an unknown `eventType` when validated strictly by the schema) fails.
- [ ] **EXP-02** The new writer's output for the two imported fixtures equals the golden files under the §2.9 round-trip law: the same property names and order, nulls written, enums as integers, `previousWorkoutElapsedSeconds` in seconds.
- [ ] **EXP-03** The importer accepts enum names as well as ordinals, `Z` offsets, and 0–9 fractional digits.
- [ ] **EXP-04** The importer rejects: a wrong `schema` or `unitSystem`; non-increasing sequences; decreasing capturedAt or elapsed; a mismatched sample algorithm; `endedAt < startedAt`; more than 100 000 samples; more than 64 MiB.
- [ ] **EXP-05** Import is idempotent by `sessionId`; a changed duplicate is a conflict; a non-terminal state imports as Interrupted; imported runs are flagged and never auto-uploaded.
- [ ] **EXP-06** `controllerConfiguration` is preserved verbatim (PascalCase, `{}`, or a string fallback). Analytics read the weight and zones from it case-insensitively.
- [ ] **EXP-07** The CSV equals `fixture-session.csv` and `fixture-session-interrupted.csv` byte-for-byte after line-ending normalization: 7-digit timestamps, empty nulls, `0.###` telemetry age, the v2 last-row calorie floor, v1 recalculation.
- [ ] **EXP-08** The TCX is XML-equivalent to `fixture-session.tcx`: one lap; stored summary values; banker's rounding for calories and HR; trackpoints without HR omit `HeartRateBpm`; no altitude.
- [ ] **EXP-09** The FIT Activity decodes (FIT SDK) to the same messages and field values as `fixture-session.fit.txt`, and passes the SDK integrity check. The tests FIT-1..FIT-8 pass. Nothing on the never-write list appears.
- [ ] **EXP-10** The FIT Workout decodes to the same values as `fixture-workout.fit.txt` (repeat marker, HR +100 offset, notes text).
- [ ] **EXP-11** The export limits: > 100 000 samples → refused; > 64 MiB → refused. File names and media types are as specified.
- [ ] **EXP-12** Legacy `.trb` run extraction passes [01](01-data-model.md) DM-06..DM-11, with preview and confirm.
- [ ] **EXP-13** New backups: `VACUUM INTO` plus the integrity check; the manifest hashes verify; the encrypted bundle fails with a wrong passphrase; retention and pruning touch only the app's own files; restore previews and takes a safety backup first; a newer schema is refused.
