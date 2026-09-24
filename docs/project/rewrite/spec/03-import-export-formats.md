---
title: 03 — Workout import and export formats
type: specification
status: draft-v2 (scope reduced: native JSON is P1, others optional)
audience: Kotlin implementers of TreadmillRunner-Android
---

# 03 — Workout import and export formats

This chapter covers the **workout** file formats:

| Format | Direction | Priority in the new app |
|---|---|---|
| Native JSON (TreadmillRunner workout JSON, two dialects) | import, and recommended export | **P1** (required) |
| QDomyos XML (`<rows>` treadmill workouts) | import | Optional or later |
| Garmin FIT workout file | import | Optional or later |
| Garmin FIT workout file | export | Optional or later (story WKT-04, P2) |
| treadmill-workout v4 ZIP bundle (a generated plan with variants) | import, which creates workouts plus a plan | Optional or later |

The workout model, its validation and its limits are defined in [02-workouts.md](02-workouts.md). Every importer produces a schema v1 definition that must pass those rules. Session exports (FIT, TCX, CSV, JSON of runs) are in [07](07-exports-and-backup.md).

The owner decided that the new app keeps no backwards compatibility except for run (session) data. The formats below describe the current app's behaviour so it can be re-implemented where useful. Byte-exact output is not required.

**Primary format decision.** §6 compares all four formats. It concludes that the app's own **native workout JSON (schema v1)** stays the primary, stored format. All other formats are converted into it on import, and out of it on export, using the §6.4 rules.

---

## 1. Common import pipeline (all formats)

### 1.1 Preview, then confirm

Every import is a two-phase operation. **Nothing is stored until the user confirms.**

**1. Preview.**
- The user picks a file and a format. There is no auto-detection. The picker filters on `.json`, `.xml` and `.fit`; the bundle has its own control accepting `.zip`.
- The app reads the file:
  - it is limited to **10 MiB** (10,485,760 bytes), and a larger file is rejected ("The workout file exceeds the 10 MB limit.");
  - an empty file is rejected ("The workout file is empty.").
- The app parses the file with the chosen importer and keeps a **preview**. A preview holds:
  - a `previewId` (UUID);
  - the original file name (the file-name part only, with any directory removed);
  - the format;
  - **a copy of the original bytes**, and their SHA-256 (lowercase hex);
  - the parse result;
  - `createdAt`, and `expiresAt` = createdAt + **15 min**.
- The preview store is in memory and holds at most **16 previews** and **32 MiB** of source bytes in total. The oldest previews are evicted first; expired ones are purged on every access.
- The UI shows:
  - the title;
  - the format;
  - "Expanded steps" (`expandedStepCount`);
  - the duration (`knownDuration` in minutes with format `0.#`, or "Mixed goals" when unknown);
  - the expiry time (HH:mm);
  - every warning as **code** plus message.
- For QDomyos the UI adds: "Metric source required: QDomyos distance must be kilometres and speed must be km/h. Other unit encodings are rejected."

**2. Confirm.** The request carries:
- `operationId`, which must not be empty;
- `previewId`;
- `sourceSha256`, copied from the preview;
- an optional `profileId`;
- `qDomyosUnits`, which is required as `"KilometersPerHour"` for QDomyos and ignored otherwise.

The app then processes it in this order:
1. **Idempotency.** If the operation ID was already completed for the same request fingerprint (`previewId`, `sourceSha256`, `profileId`, `qDomyosUnits`), return the stored result with `replayed = true`. A different fingerprint, or a different preview under the same ID, is a **conflict**.
2. If the preview has expired or is unknown: **410 Gone**, "The import preview expired or is no longer available."
3. Recompute the SHA-256 of the stored bytes. If it differs from `sourceSha256`: **conflict**, "The preview source does not match the confirmation request."
4. If `profileId` is given, it must exist and must not be archived. Otherwise: "The selected profile does not exist or is archived."
5. **Re-parse the original bytes** with the same importer and file name. The preview result is never trusted; it could be stale if the importer or its rules changed. If the re-parse fails: "The stored source no longer reparses safely: {message}".
6. For QDomyos only, apply the unit confirmation (§3.6).
7. Store the result (§1.2) and return `workoutId`, `revisionId`, `revisionNumber`, `replayed` and the final warnings.

### 1.2 Storing an import (deduplication and audit)

The import is stored in one transaction, serialised so that only one confirmation runs at a time:
1. Compute the content hash of the re-parsed definition ([02](02-workouts.md) §3.2).
2. **Deduplicate.** Look for an existing revision to reuse, in this order:
   - a revision referenced by an earlier import audit with the **same format and the same source SHA-256** whose content hash equals the new one;
   - otherwise **any revision in the database** with that content hash, the first by revision ID.

   If one is found, **reuse it**: no new workout or revision is created, and only a new audit row is written.
3. Otherwise create a new workout (kind `Structured`, `name` = definition title) with revision 1.
4. Write the **import audit record**:

| Field | Value |
|---|---|
| id | A new UUID |
| userProfileId | The `profileId` from the request, or null |
| workoutId, workoutRevisionId | The created or reused workout and revision |
| originalFileName | The file-name part only, at most 255 characters |
| format | `NativeJson`, `QDomyosXml`, `GarminFit` or `TreadmillWorkoutBundleV4` (at most 32 characters) |
| sourceSha256 | SHA-256 of the original bytes (lowercase hex) |
| warningSummaryJson | JSON array of `{ "Code", "Message" }` for the final warnings. Bundle imports use a different object (§5.6). |
| importedAt | The confirmation time (UTC) |

The audit table is indexed on `(format, sourceSha256)`. Audit rows are never shown in the library; they are provenance.

### 1.3 Warnings

Importers never silently drop or guess data. Every loss or assumption becomes a **warning** `{ code, message }`, and the preview lists them so the user can decide. The warning codes are fixed strings (listed per format below). The message texts may be reworded. Warnings are ordered as emitted, which follows document order.

### 1.4 Current HTTP surface (for the embedded web UI)

| Endpoint | Behaviour |
|---|---|
| `POST …/workouts/import/preview` | Multipart with **exactly one** file part named `file` and exactly one form field `format`, which is an enum name, case-insensitive; numeric values are rejected. Allowed values: `NativeJson`, `QDomyosXml` and `GarminFit`. The request is limited to 10 MiB + 64 KiB (413 if larger). Parse errors are a validation problem on field `file`. |
| `POST …/workouts/import/confirm` | JSON `{ operationId, previewId, sourceSha256, profileId?, qDomyosUnits? }`. Returns 201 with `{ workoutId, revisionId, revisionNumber, replayed, warnings[] }`. |
| `POST …/workout-sets/import/preview` / `confirm` | The v4 bundle (§5) |
| `GET …/workouts/revisions/{id}/export.fit` | FIT workout export (§4.2) |

On the phone the same flow applies to the native file picker: preview, then confirm, with the original bytes kept in memory.

---

## 2. Native JSON (P1)

### 2.1 Envelope and parsing rules

- **Encoding:** UTF-8.
  - The current app does **not** skip a UTF-8 BOM; a file starting with `EF BB BF` is rejected as malformed.
  - Recommendation: accept and strip a leading BOM in the new app.
- **Strict JSON:**
  - no comments;
  - no trailing commas;
  - a maximum nesting depth of **32**, where every object or array opening counts 1 and the root object is depth 1;
  - violations give "The native workout JSON is malformed."
- **Duplicate property names:** the **last** occurrence wins, with no warning.
- **The root must be an object.** Otherwise: "The native workout root must be an object."
- **Dialect detection:**
  - if the root **has a property `schema`**, the file uses the *authoring dialect* (§2.3);
  - otherwise it uses the *canonical dialect* (§2.2).
- **Type helpers,** used by both dialects:

| Helper | Rule | Error text |
|---|---|---|
| required | The property must exist. | "Native workout field '{name}' is required." |
| required string | A JSON string, not empty or whitespace-only. | "Native workout field '{name}' must be a non-empty string." |
| optional string | Absent or `null` gives null; a string is taken as-is (the model trims it later); any other type is an error. | "Native workout field '{name}' must be a string." |
| integer (int32 or int64) | A JSON number with **no fraction and no exponent** that fits the type (`5` is valid; `5.0`, `5e0` and `1e3` are not). | "Native workout field '{name}' must be an integer." |
| number | Any JSON number that is finite as a double. `1e400`, which overflows to infinity, is rejected. | "Native workout field '{name}' must be a finite number." |
| BPM | An int32, then narrowed to an unsigned 16-bit value. Negative values or values > 65535 overflow. | "The native workout contains a value that is too large." |

- **Error mapping:**
  - a model validation failure ([02](02-workouts.md) §4) gives "The native workout contains invalid or out-of-range values.";
  - numeric overflow gives "The native workout contains a value that is too large."

### 2.2 Canonical dialect (schema v1 shape)

This is exactly the [02](02-workouts.md) §2 shape, so a stored revision or an exported workout imports unchanged. The current app also uses this parser to load stored revisions for execution.

- **Root:**
  - `schemaVersion` must be an integer equal to 1; otherwise "Unsupported native workout schema version '{n}'.";
  - `title` is a required string;
  - `description` is an optional string;
  - `blocks` is a required array; otherwise "Native workout blocks must be an array."
- **Blocks:**
  - every element must be an object ("Every native workout block must be an object.");
  - `kind` is a required string: `"repeat"`, `"step"`, or else "Unsupported native workout block kind '{k}'.".
  - A repeat has `repetitions` (int32) and `blocks` (an array; otherwise "Repeat blocks must be an array.").
  - A step has `goal`, `speed` and `incline` (required objects), plus `cue` and `notes` (optional strings).
- **Goal:**
  - `kind` `"time"` has `durationTicks` (int64);
  - `kind` `"distance"` has `kilometers` (number);
  - any other kind gives "Unsupported native workout goal kind '{k}'.".
- **Speed:**
  - `open` has no other fields;
  - `fixed` has `kilometersPerHour`;
  - `ramp` has `startKilometersPerHour` and `endKilometersPerHour`;
  - `heartRate` has `minimumBpm`, `maximumBpm`, `initialKilometersPerHour`, `minimumKilometersPerHour` and `maximumKilometersPerHour`;
  - `heartRateZone` has `zoneNumber`, `initialKilometersPerHour`, `minimumKilometersPerHour` and `maximumKilometersPerHour`;
  - any other kind gives "Unsupported native workout speed kind '{k}'.".
- **Incline:**
  - `fixed` has `percent`;
  - `ramp` has `startPercent` and `endPercent`;
  - any other kind gives "Unsupported native workout incline kind '{k}'.".
- **Unknown properties** are ignored with warning `native.unknown-field`, "The {context} field '{name}' is not supported and was ignored.". The context is one of `root`, `repeat`, `step`, `goal`, `speed` or `incline`. The known sets per object are exactly the properties listed above, including `kind`.
- **Nesting:**
  - the parser refuses repeat depth ≥ 32 ("Native workout repeat nesting exceeds 32 levels.");
  - in practice the JSON depth limit of 32 is hit first, so **14 nested repeats** is the maximum importable and 15 is rejected as malformed.
- **Title for the workout:** the `title` (trimmed by the model).

### 2.3 Authoring dialect (`"schema": "treadmillrunner.workout/v1"`)

This is a friendlier hand-written format with seconds and metres.

**Root:**
- `schema` must be a string equal to `treadmillrunner.workout/v1`, compared case-sensitively.
  - A non-string gives "Native workout field 'schema' must be a string."
  - Any other value gives "Unsupported native workout schema '{value}'."
- `name` is a required string and becomes the title.
- `description` is an optional string.
- `blocks` is a required array.
- Known root fields: `schema`, `name`, `description`, `blocks`.

**Blocks** are objects with a required string `type`:
- `"repeat"` has `count` (int32) and `blocks` (an array). Known fields: `type`, `count`, `blocks`.
- `"step"`: see the fields below.
- Any other type gives "Unsupported native workout block type '{t}'."
- **Nesting:** a repeat depth ≥ 8 is rejected with "Native workout repeat nesting exceeds eight levels." At most **7 nested repeats** are allowed.

**Step fields** (all optional in JSON; the rules below decide what is required):

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Used as the cue when `cue` is absent or null |
| `cue` | string | The cue |
| `notes` | string | The notes |
| `durationSeconds` | number | Time goal. Seconds are converted to ticks by truncation ([02](02-workouts.md) §2.3). |
| `distanceMeters` | number | Distance goal, converted as km = m / 1000 |
| `speedStartKph` | number | Fixed speed, ramp start, or the initial HR speed |
| `speedEndKph` | number | Ramp end |
| `inclineStartPercent` | number | Fixed incline or ramp start. Default 0. |
| `inclineEndPercent` | number | Incline ramp end |
| `heartRateZone` | int32 | HR zone target |
| `heartRateMinBpm`, `heartRateMaxBpm` | int32 | Explicit HR target |
| `minimumSpeedKph`, `maximumSpeedKph` | number | HR speed bounds |

**Step mapping,** evaluated in this order:
1. **Goal.** Exactly one of `durationSeconds` and `distanceMeters` must be present (not null). Otherwise: "Each native workout step must define exactly one durationSeconds or distanceMeters value." A duration too large for a time span gives "Native workout field 'durationSeconds' is too large."
2. **Speed:**
   - If `heartRateZone` is present, the step is a **HeartRateZone** directive: `(zone, initial = speedStartKph ?? minimumSpeedKph, min = minimumSpeedKph, max = maximumSpeedKph)`. Both bounds are required, otherwise "A native heart-rate zone target requires minimumSpeedKph and maximumSpeedKph." The zone takes precedence over explicit bpm without any warning.
   - Else if `heartRateMinBpm` or `heartRateMaxBpm` is present, the step is a **HeartRate** directive. Both must be present and > 0, otherwise "Native explicit heart-rate targets require both heartRateMinBpm and heartRateMaxBpm." The speeds follow the zone rule, and the missing-bounds error is "A native explicit heart-rate target requires minimumSpeedKph and maximumSpeedKph."
   - Else if `speedEndKph` is present, the step is a **Ramp** `(speedStartKph, speedEndKph)`. The start is required, otherwise "Native speed ramps require speedStartKph."
   - Else if `speedStartKph` is present, the step is a **Fixed** speed.
   - Else the step is **Open** speed, with warning `native.open-speed`, "A native workout step has no speed target and requires review before execution."
3. **Incline:**
   - If `inclineEndPercent` is present, the step is a **Ramp** `(inclineStartPercent, inclineEndPercent)`. The start is required, otherwise "Native incline ramps require inclineStartPercent."
   - Otherwise it is **Fixed** `(inclineStartPercent ?? 0)`.
4. **Cue:** `cue` if it is present and not null, otherwise `name`. A whitespace-only `cue` still wins over `name` and then becomes null.
5. **Unknown fields** produce `native.unknown-field` warnings with the context `root`, `repeat` or `step`.

### 2.4 Worked examples (golden)

The source files are in `data/workouts/imports/native/`. The full expected results (definition, steps, duration, warnings or error) are in `data/workouts/native-import-vectors.json`. The file name passed to the importer is `workout.json`.

| Id | Input summary | Expected |
|---|---|---|
| `native-authoring-progression` (unit test) | Authoring dialect. Step "Warm up" 300 s, 5→7 km/h, incline 1, cue "Relax". Repeat ×3 [400 m, zone 3, min 7, max 10]. | Title "Progression", description "A safe fixture". Step 1: time 300 s, ramp 5→7, incline fixed 1, cue "Relax". Repeat ×3: distance 0.4 km, HeartRateZone(3, initial 7, min 7, max 10), incline 0. **4** expanded steps; duration unknown; no warnings. |
| `native-unknown-schema` (unit test) | `{"schema":"future/v2",…}` | error "Unsupported native workout schema 'future/v2'." |
| `native-malformed` (unit test) | `{` | error "The native workout JSON is malformed." |
| `native-too-many-steps` (unit test) | repeat 10,000 × repeat 2 × 1 s step | error (invalid or out-of-range: 20,000 steps) |
| `native-too-long` (unit test) | one step of 43,201 s | error (invalid or out-of-range: more than 12 h) |
| `native-unknown-fields` (unit test) | root `future`, step `futureStep`, no speed | Warnings, in order: `native.unknown-field` (root 'future'), `native.unknown-field` (step 'futureStep'), `native.open-speed` |
| `native-canonical-round-trip` (unit test) | The canonical JSON of "Canonical" / "Round trip" / 2 min fixed 8.5 / incline ramp 0→2 / cue "Build" | A definition identical to the source |
| `native-canonical-nested-unknown` (unit test) | Canonical with `futureGoal`, `futureSpeed` and `futureIncline` | 3 warnings in the order goal, speed, incline; `8.0` is read as 8 |
| `native-bom` | Canonical JSON prefixed with a UTF-8 BOM | error malformed (current behaviour; see §2.1) |
| `native-both-goals` | `durationSeconds` and `distanceMeters` | error "Each native workout step must define exactly one durationSeconds or distanceMeters value." |
| `native-hr-explicit` | 600 s, 130–145 bpm, min 6, max 9, incline 1→3, name "Aerobic", notes | HeartRate(130, 145, initial **6** (= min), 6, 9); incline ramp 1→3; cue "Aerobic"; notes "Nose breathing" |
| `native-hr-missing-max` | Only `heartRateMinBpm` | error "…require both heartRateMinBpm and heartRateMaxBpm." |
| `native-speed-end-without-start` | Only `speedEndKph` | error "Native speed ramps require speedStartKph." |
| `native-nesting-7` / `-8` | 7 or 8 nested repeats (authoring) | 7 accepted; 8 rejected with "…exceeds eight levels." |
| `native-canonical-nesting-14` / `-15` | 14 or 15 nested repeats (canonical) | 14 accepted; 15 rejected as malformed (JSON depth) |
| `native-duplicate-key` | `"title":"First","title":"Second"` | Title "Second" |
| `native-int-as-decimal` | `"schemaVersion":1.0` | error "Native workout field 'schemaVersion' must be an integer." |
| `native-trailing-comma`, `native-comment` | — | error malformed |
| `native-empty-blocks` | `"blocks":[]` | error (invalid or out-of-range) |
| `native-fractional-seconds` | 0.00000019 s, 90.5 s, 1234.5 m | Ticks 1 and 905,000,000; 1.2345 km |
| `native-zone-with-explicit-hr` | Zone 2 plus 120–140 bpm, start 7, min 6, max 8 | HeartRateZone(2, 7, 6, 8); no warning |
| `native-schema-not-string` | `"schema":1` | error "Native workout field 'schema' must be a string." |
| `native-empty-file` | 0 bytes | error "The workout file is empty." |
| `native-open-with-incline` | name "  Hill walk ", description "  ", 600 s, incline 4, step name " Climb ", cue "  " | Title "Hill walk"; description null; cue null (the blank cue wins over the name); open speed plus warning `native.open-speed` |

### 2.5 Native JSON export (recommended for the new app)

The current app has no dedicated "export workout as JSON" action. Workouts leave the app only in backups and FIT. For the new app:
- **Export** a revision as its canonical JSON ([02](02-workouts.md) §3.2) with file name `{sanitised title}.json`.
- The canonical-dialect importer must read it back to an **identical definition**. This round-trip is the acceptance test.

---

## 3. QDomyos XML (optional)

This format is the XML workout of the QDomyos-Zwift app (treadmill "rows").

### 3.1 Parsing and security

- The XML parser is configured with:
  - **DTD processing prohibited**;
  - no external resolver;
  - entity expansion limited to 0 characters;
  - comments and processing instructions ignored;
  - document size limited to 10 MiB of characters.
- Any XML error, including a DTD, an entity, a declaration that is not at the very start, or malformed XML, gives "The QDomyos XML is malformed or contains a prohibited DTD/entity."
- The root element must be `<rows>`. Otherwise: "The QDomyos XML root must be <rows>." Element names are case-sensitive and use no namespace.
- **Root attributes:**
  - `device` is optional. Any value other than `treadmill` or `unknown` (case-insensitive) gives warning `qdomyos.non-treadmill-device`, "The source declares device '{device}'. Only treadmill fields are imported."
  - Any other root attribute gives `qdomyos.unknown-root-field`.
- **Title** = the file name without its directory and its **last** extension (`/tmp/My Workout.v2.xml` gives `My Workout.v2`). If that is blank, the title is "Imported QDomyos workout". The description is always null.

### 3.2 Structure

- The children of `<rows>` and of `<repeat>` are processed in document order:
  - `<repeat times="n">`: `times` is required and must consist of digits only (no sign or whitespace), otherwise "QDomyos attribute 'times' must be an integer." Other attributes give `qdomyos.unknown-repeat-field`. Children are parsed recursively.
  - `<row …>`: a step (§3.3).
  - Any other element is ignored with `qdomyos.unknown-element`.
- A repeat depth ≥ 8 gives "QDomyos repeat nesting exceeds eight levels." At most 7 nested repeats are allowed.
- Model validation applies at the end: empty `<rows>`, too many steps or more than 12 h give "The QDomyos workout contains invalid or out-of-range values." An overflow gives "…a value that is too large."

### 3.3 Row attributes

**Supported attributes:** `duration`, `distance`, `speed`, `speedfrom`, `speedto`, `inclination`, `zonehr`, `hrmin`, `hrmax`, `minspeed`, `maxspeed`, `forcespeed`, `looptimehr`.

**Bike-only attributes** (warning `qdomyos.unsupported-bike-field` each, ignored): `resistance`, `lower_resistance`, `upper_resistance`, `maxresistance`, `cadence`, `lower_cadence`, `upper_cadence`, `power`, `powerzone`, `powerzonefrom`, `powerzoneto`, `fanspeed`, `requested_peloton_resistance`, `lower_requested_peloton_resistance`, `upper_requested_peloton_resistance`.

Any other attribute gives `qdomyos.unknown-row-field`.

**Value parsing.** Culture-invariant; `.` is the only decimal separator.

| Kind | Accepted syntax | Error |
|---|---|---|
| Numbers (`distance`, speeds, `inclination`) | Optional leading and trailing whitespace, optional sign, a decimal point and an exponent. No thousands separators. The value must be finite. `6,5` is rejected. | "QDomyos attribute '{name}' must be a finite number." |
| Integers (`zonehr`, `hrmin`, `hrmax`) | Optional whitespace and sign | "QDomyos attribute '{name}' must be an integer." |
| `duration` | Exactly three `:`-separated parts, each digits only; minutes 0–59; seconds 0–59; hours unbounded. `0:0:1` is valid. | "QDomyos duration values must use HH:MM:SS." |

**Mapping rules,** in this order:
1. **Speed-unit flag.** If any of `speed`, `speedfrom`, `speedto`, `minspeed` or `maxspeed` is present, the file "used speed units". After all rows, one warning `qdomyos.assumed-speed-units` is appended ("…speed values were assumed to be km/h.").
2. **Ramp validity:**
   - `speedfrom` and `speedto` must be both present or both absent. Otherwise: "QDomyos speed ramps require both speedfrom and speedto."
   - `speed` and `speedfrom` together give "A QDomyos row cannot define both fixed speed and a speed ramp."
3. **Heart rate:**
   - `zonehr="0"` means no zone.
   - If a zone and (`hrmin` or `hrmax`) are both present, warning `qdomyos.conflicting-heart-rate-targets`: the explicit bounds are kept and the zone is ignored.
4. A `forcespeed` value other than `0` gives warning `qdomyos.forcespeed-ignored`. Any `looptimehr` gives `qdomyos.hr-loop-ignored`.
5. **Child elements of a row:**
   - `<textevent message="…">`: each non-blank message is collected. A `timeoffset` gives `qdomyos.text-offset-flattened`.
   - Any other child gives `qdomyos.unknown-row-element`.
   - The collected messages are joined with `" · "` (space, U+00B7, space) to form the **cue**.
6. **Goal.** Exactly one of `duration` and `distance`. Otherwise: "Each QDomyos row must define exactly one duration or distance." `distance` is in **km**.
7. **Speed directive:**
   - If a fixed or ramp speed is combined with any HR target (and the v4 rule in §5.4 does not apply), warning `qdomyos.conflicting-speed-target`: the explicit speed is kept.
   - Then, in order:
     1. If the §5.4 bounded-HR rule applies: HR or HR-zone with initial = `speed`.
     2. Else if `speedto` is present: **Ramp** `(speedfrom, speedto)`.
     3. Else if `speed` is present: **Fixed** `(speed)`.
     4. Else if `hrmin` or `hrmax` is present: both must be > 0, otherwise "QDomyos explicit HR targets require both hrmin and hrmax." Result: **HeartRate** `(hrmin, hrmax, speeds)`.
     5. Else if a zone is present: **HeartRateZone** `(zone, speeds)`.
     6. Else: **Open**, with warning `qdomyos.open-speed`.
   - **HR speeds:**
     - `min = minspeed ?? 0` and `max = maxspeed ?? min`;
     - if either bound was missing, add `qdomyos.hr-speed-bounds-defaulted`;
     - always add `qdomyos.hr-initial-speed-defaulted`, because the initial speed equals the minimum.
8. **Incline** = Fixed(`inclination` ?? 0). There are no incline ramps.

### 3.4 Worked examples (from the unit tests and probes)

The file name is `intervals.xml` unless stated otherwise.

| Input | Expected |
|---|---|
| `<rows device="treadmill">` containing:<br>• `<row duration="00:05:00" speedfrom="5.0" speedto="8.0" inclination="1" forcespeed="1"><textevent timeoffset="5" message="Relax your shoulders"/></row>`<br>• `<repeat times="3">` with `<row duration="00:01:30" zonehr="4" minspeed="10.0" maxspeed="14.0" looptimehr="5"/>` and `<row distance="0.4" hrmin="125" hrmax="135"/>` | Title `intervals`; **7** expanded steps; duration unknown.<br>Step 1: 5 min, ramp 5→8, incline 1, cue "Relax your shoulders".<br>Repeat ×3: [1:30, zone 4 (10, 10, 14), incline 0], [0.4 km, HR 125–135 (0, 0, 0)].<br>Warnings in order: `forcespeed-ignored`, `text-offset-flattened`, `hr-loop-ignored`, `hr-initial-speed-defaulted`, `hr-speed-bounds-defaulted`, `hr-initial-speed-defaulted`, `assumed-speed-units` (each prefixed `qdomyos.`). |
| A `<!DOCTYPE rows [ <!ENTITY xxe SYSTEM "file:///…"> ]>` document | error "…malformed or contains a prohibited DTD/entity." |
| `<rows device="bike"><row duration="00:01:00" resistance="20" cadence="90" power="200"/></rows>` | 1 step with open speed. Warnings: `non-treadmill-device`, 3 × `unsupported-bike-field`, `open-speed`. |
| `<row duration="00:01:00" zonehr="4" hrmin="125" hrmax="135" minspeed="6" maxspeed="10"/>` | HeartRate(125, 135, 6, 6, 10). Warnings `conflicting-heart-rate-targets` ("…the explicit bounds were retained and the zone was ignored."), `hr-initial-speed-defaulted`, `assumed-speed-units`. |
| `<row duration="00:05:00" speed="6" zonehr="2" minspeed="4" maxspeed="8"/>` (standalone file) | Fixed 6. Warnings `conflicting-speed-target`, `assumed-speed-units`. There is **no** `v4-` warning. |
| `<rows><row></rows>` | error malformed |
| `<repeat times="10000"><repeat times="2"><row duration="00:00:01"/></repeat></repeat>` | error (20,000 steps) |
| 10 MiB + 1 byte | error "…exceeds the 10 MB limit." |
| `duration="1:5"` | error "…must use HH:MM:SS." |
| `12:00:00` + `0:0:1` | error (more than 12 h) |
| `zonehr="0" inclination="2.5"`, then `zonehr="3" minspeed="5"` (file `zone zero.xml`) | Title `zone zero`. Step 1: open, incline 2.5 (`open-speed`). Step 2: zone 3 (5, 5, 5) with `hr-speed-bounds-defaulted` and `hr-initial-speed-defaulted`. Then `assumed-speed-units`. |
| Root `version="2"`, an element `<note/>`, row attribute `foo`, two textevents "A" and "B", `<other/>` in a row, repeat attribute `name` (file `/tmp/My Workout.v2.xml`) | Title `My Workout.v2`; cue `A · B`. Warnings `unknown-root-field`, `unknown-element`, `unknown-row-field`, `unknown-row-element`, `unknown-repeat-field`, `assumed-speed-units`. |
| `speedfrom` only; `speed` + ramp; no goal; root `<workout>`; empty `<rows>`; `speed="6,5"`; `hrmin` only | The respective errors from §3.3 |

### 3.5 Units

QDomyos XML does not reliably declare units. **The app accepts metric sources only:** speed in km/h and distance in km. There is no conversion.

### 3.6 Unit confirmation at confirm time

For `QDomyosXml`:
- Confirmation requires `qDomyosUnits = "KilometersPerHour"`. Otherwise the request is rejected: "QDomyosUnits must be KilometersPerHour. TreadmillRunner accepts Metric workout sources only."
- The final warnings are the re-parsed warnings **minus** `qdomyos.assumed-speed-units`, **plus** `qdomyos.confirmed-kph-units` ("The source speed and distance units were explicitly confirmed as Metric."). These are the warnings stored in the audit record.

---

## 4. Garmin FIT workout (optional)

The FIT workout file is `file_id.type = workout (5)`. Use the Garmin FIT SDK for decoding and encoding.

### 4.1 Import

**Validation,** in this order:
1. The FIT header is valid. Otherwise: "The FIT workout header is invalid."
2. The file CRC is valid. Otherwise: "The FIT workout CRC is invalid."
3. The file decodes. Otherwise: "The FIT workout could not be decoded." Any SDK exception gives "The FIT workout is malformed."
4. There is exactly **one** `file_id` message, and its type is `workout`. Otherwise: "The FIT file must contain exactly one Workout File Id message."
5. There is exactly **one** `workout` message and **at least one** `workout_step` message. Otherwise: "Exactly one FIT Workout message and at least one Workout Step message are required."

**Workout-level fields:**
- If `sport` is present and is neither `running (1)` nor `generic (0)`, warning `fit.non-running-sport`, "The FIT workout sport is '{Sport}'. Only treadmill-relevant fields were imported." (The sport is written with the SDK enum name, for example `Cycling`.)
- **Title:**
  1. `wkt_name`;
  2. if blank, the file name without its extension;
  3. if still blank, "Imported FIT workout".
- **Description:** `wkt_description`, trimmed; blank becomes null.
- If `num_valid_steps` is present and differs from the number of decoded `workout_step` messages (repeat markers included), warning `fit.step-count-mismatch`, "The FIT workout declares {n} steps but contains {m}; decoded messages were used."

**Step order:**
- Every step needs `message_index`. Otherwise: "Every FIT workout step requires a message index."
- Indexes must be unique. Otherwise: "FIT workout step message indexes must be unique."
- Steps are processed in **ascending `message_index`**, not in file order.

**Per step:**
- `duration_type` is required. Otherwise: "FIT workout step {i} has no duration type."
- **`repeat_until_steps_cmplt` (6):** collapse a repeat.
  - `start = duration_step` and `count = repeat_steps ?? target_value`.
  - The rule `start ≤ 65535` and `1 ≤ count ≤ 2147483647` must hold. Otherwise: "FIT repeat step {i} has invalid start/count values."
  - Find the block already built whose **start index** equals `start`. A repeat block built earlier keeps the start index of its first child, which allows repeats of repeats. If none: "FIT repeat step {i} references missing step {start}."
  - Replace that block and every block after it with `Repeat(count, those blocks)`.
  - If the marker's `target_type` is present and not `open`, warning `fit.repeat-target-ignored`.
- **Other repeat-until types** (`repeat_until_time`, `_distance`, `_calories`, `_hr_less_than`, `_hr_greater_than`, `_power_less_than`, `_power_greater_than`, `_power_last_lap_less_than`, `_max_power_last_lap_less_than`, `_training_peaks_tss`, with values 7–13, 17, 18 and 27): warning `fit.repeat-until-unsupported`. The marker is skipped and the preceding steps stay single-pass.
- **Goal:**
  - `time (0)`, `time_only (31)` and `repetition_time (28)` use `duration_time` in seconds, a float from the SDK where the raw value is milliseconds / 1000. It must be > 0 and finite, otherwise "FIT workout step {i} has an invalid time duration." Converted as seconds → ticks by truncation.
  - `distance (1)` uses `duration_distance` in metres, a float where the raw value is centimetres / 100. It must be > 0 and finite, otherwise "…invalid distance duration." km = m / 1000.
  - Any other type (for example `calories`, `open`, `hr_less_than`): warning `fit.duration-unsupported`, and the step is **skipped**.
- **Speed** from `target_type` (default `open` when absent):
  - `open (2)`: **Open**, with warning `fit.open-speed`.
  - `speed (0)`: uses `custom_target_speed_low` and `custom_target_speed_high` (m/s, float).
    - If both are present and finite and `|low − high| < 0.0001`: **Fixed** `(low × 3.6)`, computed in double from the float. For example raw 2778 gives 10.00080041885376 km/h.
    - If both are present but differ: warning `fit.speed-range-unsupported`, and **Open**. A FIT range is not a ramp.
    - Otherwise (a zone or an incomplete range): warning `fit.speed-zone-unsupported`, and **Open**.
  - `heart_rate (1)`: take `zone = target_hr_zone ?? target_value ?? 0`.
    - If `zone > 0`:
      - `zone > 10`: warning `fit.hr-zone-invalid`, and **Open**;
      - otherwise **HeartRateZone** `(zone, 0, 0, 0)` with warning `fit.hr-speed-bounds-required` ("…zero bounds were retained and require editing before execution.").
    - Otherwise use `custom_target_heart_rate_low` and `custom_target_heart_rate_high`:
      - if either is missing: `fit.hr-target-incomplete`, and **Open**;
      - if either is ≤ 100 (percent of max HR): `fit.hr-percent-unsupported`, and **Open**;
      - otherwise `bpm = value − 100` for each. Each bpm must satisfy 1 ≤ bpm ≤ 250, and low ≤ high; otherwise the error "FIT step {i} has invalid absolute HR bounds." The result is **HeartRate** `(low, high, 0, 0, 0)` with `fit.hr-speed-bounds-required`.
  - Any other target (cadence, power, grade and so on): warning `fit.target-unsupported`, and **Open**.
- If `secondary_target_type` is present and not open, warning `fit.secondary-target-unsupported`.
- The step is built with:
  - **incline Fixed(0)** (FIT has no treadmill incline target);
  - cue = `wkt_step_name`, trimmed, blank becomes null;
  - notes = `notes`, trimmed, blank becomes null.
- If no step remains, the error is "The FIT workout contains no supported time or distance steps."

**Known defect in the current app (do not replicate).** In a process that has recently *encoded* multi-step FIT workouts, later decoding with the current SDK usage can return step names and notes as strings of NUL characters. `EmptyToNull` does not treat NUL as blank, so the NUL string is stored as the cue.

In the Kotlin app:
- strip trailing `\u0000` from every FIT string;
- treat NUL-only or blank strings as null.

The examples below show the correct, intended results.

**Worked examples** (the unit-test fixtures are built with the FIT SDK encoder; the file name is `fixture.fit`):

| Fixture | Expected |
|---|---|
| Running, "Intervals":<br>• step 0 "Warm up": time 300 s, speed 2.5–2.5 m/s<br>• step 1 "Zone work": distance 400 m, HR zone 3<br>• step 2: `repeat_until_steps_cmplt`, `duration_step` 0, `repeat_steps` 3 | One Repeat ×3 of [300 s fixed **9** km/h cue "Warm up"; 0.4 km HeartRateZone(3, 0, 0, 0) cue "Zone work"]. **6** expanded steps; duration unknown. Warning `fit.hr-speed-bounds-required` (step 1). |
| Cycling, "Mixed targets":<br>• step 0: 60 s, HR custom 225–240<br>• step 1: 60 s, power zone 3, secondary target cadence | HeartRate(**125**, **140**, 0, 0, 0), then Open. Warnings `fit.non-running-sport` ('Cycling'), `fit.hr-speed-bounds-required`, `fit.target-unsupported` ('Power'), `fit.secondary-target-unsupported` ('Cadence'). |
| "Partial":<br>• step 0: calories 50<br>• step 1: 30 s, open | 1 step (30 s, open). Warnings `fit.duration-unsupported` (step 0 'Calories'), `fit.open-speed` (step 1). |
| Bytes `01 02 03 04 05` | error "The FIT workout header is invalid." |
| A valid file with its last byte flipped | error "The FIT workout CRC is invalid." |
| Two `workout` messages | error "Exactly one FIT Workout message…" |
| Generic sport, blank name, file `My FIT.fit`, steps written in order index 1, 0, 2:<br>• index 1: 90.5 s, speed 2.0–3.0 m/s, notes "  keep form  "<br>• index 0: 60 s, HR 60–80<br>• index 2: 1000 m, 2.778 m/s | Title "My FIT". Steps in index order: [60 s, Open (`fit.hr-percent-unsupported`)], [90.5 s, Open (`fit.speed-range-unsupported`), notes "keep form"], [1 km, Fixed 10.00080041885376]. No sport warning. |
| "Nested":<br>• 0: 600 s at 1.5 m/s<br>• 1: 60 s at 3 m/s<br>• 2: 60 s open<br>• 3: repeat from 1 ×2<br>• 4: repeat from 0 ×3, target HR<br>• 5: `repeat_until_time`<br>• 6: duration `open` | Repeat ×3 [600 s at 5.4 km/h; Repeat ×2 [60 s at 10.8; 60 s open]]. 15 expanded steps; 2,520 s. Warnings `fit.open-speed`, `fit.repeat-target-ignored`, `fit.repeat-until-unsupported`, `fit.duration-unsupported`. |
| A step without `message_index` | error "Every FIT workout step requires a message index." |
| HR zone 11 | Open, with warning `fit.hr-zone-invalid` |
| `num_valid_steps` = 5 with 1 decoded step, description "  Described  ", step name "  " | Description "Described"; cue null. Warnings `fit.open-speed`, `fit.step-count-mismatch`. |

### 4.2 Export (optional)

This exports one revision as a FIT workout file. **It is lossy by design:** FIT cannot express treadmill incline, speed ramps or HR speed bounds, so they are described in the step notes. Native JSON is the round-trip format; FIT is for watches and Garmin Connect.

**Messages, in order:**
1. **`file_id`:**
   - type `workout (5)`;
   - manufacturer `development (255)`;
   - product 1;
   - serial number = the first group of the revision UUID read as a 32-bit unsigned number (for `c0123456-…` it is 0xC0123456 = 3222418518);
   - `time_created` = the revision creation time;
   - product name "TreadmillRunner".
2. **`workout`:**
   - `wkt_name` = title ("TreadmillRunner workout" if missing);
   - sport `running (1)`;
   - `num_valid_steps` = the number of step messages, repeat markers included.
3. **`workout_step` messages,** depth-first. `message_index` = the running position, starting at 0.
   - **Step:**
     - `wkt_step_name` = the cue, or `Step {index + 1}`.
     - Goal: time gives `duration_type time (0)` and `duration_time = ticks / 10^7` seconds (float). Distance gives `distance (1)` and `duration_distance = km × 1000` metres (float).
     - Speed: see the table below.
     - `notes`: see below.
   - **Repeat:**
     1. First write all child messages.
     2. Then a marker with:
        - the next `message_index`;
        - `wkt_step_name` "Repeat {n} times";
        - `duration_type repeat_until_steps_cmplt (6)`;
        - `duration_step` = the message index of the repeat's first child;
        - `target_type open`;
        - `repeat_steps = n`.

   | Speed kind | FIT fields |
   |---|---|
   | open | `target_type open` |
   | fixed | `target_type speed`, custom low = high = kph / 3.6 (m/s, float) |
   | ramp | `speed` with low = min and high = max of the two endpoints |
   | heartRate | `heart_rate` with custom low = minBpm + 100 and high = maxBpm + 100 |
   | heartRateZone | `heart_rate` with `target_hr_zone` = zone |

   **`notes`** = these parts joined by single spaces:
   1. the original notes, if any;
   2. for a ramp: "Speed ramp {a}-{b} km/h; FIT stores the endpoints as a target range.";
   3. for HR kinds: "Treadmill speed safety bounds remain in the immutable TreadmillRunner revision and are not a standard FIT workout target.";
   4. always one of "Treadmill incline {p}%." or "Treadmill incline ramp {a}-{b}%.".

   Numbers in notes use the `0.##` format.
- If the step count is 0 or > 65535, the export fails ("The workout cannot be represented within FIT workout step limits.").
- **Download:** content type `application/vnd.ant.fit`; file name `treadmillrunner-workout-{revisionId as 32 hex characters}.fit`.

**Worked example** (unit test):
- Revision `c0123456-789a-4bcd-8ef0-123456789abc`, created 2026-08-23T09:00Z.
- Definition "Metric intervals": one 10 min step, fixed 7.2 km/h, incline 1.5, cue "Steady".
- The decoded result must contain:

| Message | Decoded fields |
|---|---|
| file_id | type 5, manufacturer 255, product 1, serial 3222418518, time_created 1156410000 (FIT epoch seconds), product_name "TreadmillRunner" |
| workout | wkt_name "Metric intervals", sport 1, num_valid_steps 1 |
| workout_step | message_index 0, wkt_step_name "Steady", duration_type 0, duration_time 600 s (raw 600000), target_type 0, custom speed low = high ≈ 2.0 m/s (raw 2000), notes "Treadmill incline 1.5%." |

Second example: "Intervals" (see [02](02-workouts.md) §2.6) gives 3 step messages:
1. "Warm up": time 300 s, speed low 2.222 / high 2.778 m/s (raw 2222 and 2778), notes "Speed ramp 8-10 km/h; FIT stores the endpoints as a target range. Treadmill incline 1%.".
2. "Work": distance 400 m (raw 40000), `heart_rate` low 245 / high 255, notes "Hold form Treadmill speed safety bounds remain … Treadmill incline ramp 1-3%.".
3. "Repeat 2 times": type 6, duration_step 1, repeat_steps 2, target open.

Compare **decoded fields**, not bytes. The field order in the definition messages depends on the SDK.

---

## 5. treadmill-workout v4 ZIP bundle (optional)

This is a ZIP produced by the external generator tool "treadmill-workout" (major version 4). It contains a multi-week plan whose slots each have one primary variant and optional alternatives, with per-device workout files. TreadmillRunner imports the **Horizon Omega Z** QDomyos XML files. The import creates one `Structured` workout per selected variant, plus one ordered plan (program) revision.

### 5.1 Archive checks

These checks run before anything is parsed:
- The file name extension must be `.zip`, case-insensitive. Otherwise: "Choose a treadmill-workout v4 ZIP bundle."
- The archive size is at most **64 MiB**. An empty archive gives "The generated workout-set ZIP is empty."
- It has 1–**5,000** entries.
- Directory entries (names ending in `/`) are skipped.
- Every file path must be **safe**:
  - not blank;
  - does not start with `/`;
  - contains no `\` and no `:`;
  - has no empty, `.` or `..` segments;
  - is unique both case-sensitively and case-insensitively.

  Otherwise: "The ZIP contains an unsafe or duplicate path: {name}."
- Symbolic links are rejected (Unix mode `0xA000` in the external attributes).
- Each entry is at most **10 MiB** uncompressed.
- The expansion ratio `uncompressed / compressed` must be ≤ **100** when the entry is compressed.
- The total uncompressed size is at most **256 MiB**.

### 5.2 `manifest.json` (required)

- JSON with a maximum depth of 16. The required values are:
  - `format_version` = 2;
  - `tool` = `"treadmill-workout"`;
  - `compatibility_profile` = `"treadmill-multi-device-bundle-v4"`.

  Otherwise: "The manifest is not a treadmill-workout v4 bundle."
- `tool_version` must parse as a version with major **4** (for example `4.0.1`). Otherwise: "Only treadmill-workout major version 4 bundles are supported."
- `device_profile_ids` must contain `"horizon-omega-z-dark-2023-ftms"`. Otherwise: "The bundle does not include the Horizon Omega Z profile."
- `artifacts` is an object of `{ safe path: 64-hex SHA-256 }`. Otherwise: "The manifest artifact map is malformed." The hex is compared in lowercase.
- Any other structural problem gives "The bundle manifest is malformed."
- **Integrity:**
  - The set of ZIP file paths, excluding `manifest.json`, must equal the artifact key set exactly. Otherwise: "The ZIP file set does not match its manifest."
  - Every file's SHA-256 must match its manifest entry. Otherwise: "Artifact digest mismatch: {path}."

### 5.3 `workout_index.csv` (required)

**CSV rules:**
- UTF-8.
- Fields are separated by commas.
- A field that starts with `"` is quoted, and `""` inside quotes is an escaped quote.
- `\r` is ignored and `\n` ends a record.
- Records whose fields are all empty are skipped.
- An unterminated quote gives an error.
- There must be a header plus at least 1 row. Otherwise: "The workout index has no workout rows."

**Columns:**
- The header must contain every required column, with no duplicates: `canonical_slot, session_id, variant, intended_control_mode, week, session, title, horizon_omega_z_file, perform_exactly_one_variant, alternative_of, selection_rule`. Extra columns are allowed.
- Every row must have the header's column count.
- Values are trimmed.
- At most **1,000** rows are allowed.

**Row validation:**

| Column | Rule |
|---|---|
| `canonical_slot` | 1–64 characters, no control characters |
| `session_id` | 1–100 characters, no control characters, **unique** across the file |
| `intended_control_mode` | 1–64 characters |
| `selection_rule` | 1–256 characters |
| `alternative_of` | 0–64 characters. It must be empty for `primary`, and must equal the slot for every other variant. |
| `perform_exactly_one_variant` | `true` (case-insensitive) |
| `variant` | One of `primary`, `hr-alternative`, `fixed-fallback`, `omega-recovery-incline` |
| `week`, `session` | Digits only, ≥ 1 |
| `title` | Not blank |
| `horizon_omega_z_file` | Starts with `treadmill/horizon-omega-z-dark/sessions/`, ends with `.xml` (case-insensitive), is a safe path, and exists in the ZIP |

**Per slot** (rows grouped by `canonical_slot`):
- exactly one `primary`;
- no duplicate variant;
- all rows share the same week and session.

**Slot order:** ascending week, then session (taken from each slot's first row).

### 5.4 Workout parsing per variant

Each referenced XML file is parsed with the QDomyos importer (§3), using the file name only as the title source, with **one extra rule: bounded HR**. A row uses bounded HR when all of these hold:
- `speed > 0`;
- no `speedfrom`;
- `minspeed > 0` and `maxspeed > 0`;
- the row has an HR target (a zone, `hrmin` or `hrmax`).

Such a row becomes:
- **HeartRate** `(hrmin, hrmax, initial = speed, min = minspeed, max = maxspeed)` when both `hrmin` and `hrmax` are present, with warning `qdomyos.v4-bounded-heart-rate`;
- otherwise **HeartRateZone** `(zone, speed, minspeed, maxspeed)` when a zone is present, with `qdomyos.v4-bounded-heart-rate-zone`.

In this mode `qdomyos.conflicting-speed-target` is not emitted. Edge case: with only one of `hrmin` and `hrmax` and no zone, the row falls through to Fixed(`speed`).

The resulting definition gets:
- title `"{canonical_slot} · {normalised title}"`, where the normalised title has runs of whitespace collapsed to a single space, is at most 140 characters, and becomes "Generated workout set" when empty;
- description `"Imported from treadmill-workout {tool_version}; {selection_rule}"`.

The bundle's own warnings are:
- one message, `"{k} alternative variants are available. The training plan will contain exactly one variant per slot."`, when k > 0;
- nothing otherwise.

Per-XML warnings are not surfaced in the bundle preview.

### 5.5 Plan identity (`config.json`, optional)

- **Without `config.json`:**
  - plan name = the normalised file name without its extension;
  - category "Generated".
- **With `config.json`:**
  - plan name = `plan_name`, falling back to the file name;
  - category = `preset_id` with `_` replaced by a space, at most 40 characters, falling back to "Generated".

  Wrong types (for example `plan_name` being an object) or invalid JSON give "The bundled config.json is malformed."

### 5.6 Variant selection, preview and confirm

**Strategies.** For each slot, pick the preferred variant if the slot has it, otherwise `primary`:

| Strategy | Preferred variant |
|---|---|
| `Default` | (primary) |
| `PreferHeartRate` | `hr-alternative` |
| `PreferFixed` | `fixed-fallback` |
| `PreferOmegaRecovery` | `omega-recovery-incline` |

**Preview:**
- The request has exactly one file part and no other form fields.
- The preview store holds at most **4** previews and **128 MiB**, for 15 min.
- The preview shows:
  - plan name, category and tool version;
  - the slot count and the total variant count;
  - the warnings;
  - per strategy, the number of substitutions (selected variants that are not `primary`);
  - per slot, its variants (session ID, variant, title, control mode, selection rule).
- The UI offers a "Variant selection" choice and "Create plan for": a runner or "Shared library only".

**Confirm** `{ operationId, previewId, sourceSha256, profileId?, selectionStrategy }`:
- The strategy name is case-insensitive.
- The idempotency and SHA checks are as in §1.1, and the original bytes are **re-parsed**.
- Then, in one transaction:
  1. For each selected variant, in slot order, create a `Structured` workout with revision 1 (**no deduplication**).
  2. Write an audit row per workout with:
     - format `TreadmillWorkoutBundleV4`;
     - the archive's SHA-256;
     - `warningSummaryJson = {"CanonicalSlot":…, "Variant":…, "SourcePath": file name only}`.
  3. Create one plan (program) revision 1 with:
     - the plan name;
     - description "Generated by treadmill-workout {tool_version}. Exactly one variant is selected for each canonical slot ({strategy}).";
     - the category;
     - one item per slot, at positions 1..n, each referencing the new revision.

     There is no template ID, so the workouts stay visible in the library.
  4. The optional profile must exist and must not be archived.
- The result is `{ workoutProgramId, workoutProgramRevisionId, programName, workoutCount, strategy, replayed }`.

**Worked example** (unit test). File `five-k.zip` containing:
- `config.json` = `{"plan_name":"Household 5K","preset_id":"5k"}`;
- three Omega XML files:
  - `W01D1_Easy.xml`: 1 min at 4.5, incline 1.0;
  - `W01D1H_Easy.xml`: 1 min, zone 2, min 4.0, max 8.0, speed 5.0, incline 1.0;
  - `W01D2_Steady.xml`: 1 min at 6.0;
- a CSV with slots W01D1 (primary plus hr-alternative W01D1H) and W01D2 (primary);
- a manifest with tool version `4.0.1`.

| Aspect | Expected |
|---|---|
| Plan | "Household 5K", category "5k", 2 slots |
| Warning | "1 alternative variants are available. …" |
| Default selection | [W01D1 primary, W01D2 primary] |
| PreferHeartRate selection | [W01D1H hr-alternative, W01D2 primary] |
| W01D1H definition | Title "W01D1 · Easy HR", description "Imported from treadmill-workout 4.0.1; Use instead of W01D1", step 1 min HeartRateZone(2, initial **5**, min **4**, max **8**), incline 1 |
| Rejections | Tampered digest ("Artifact digest mismatch: …W01D2_Steady.xml."); a `../outside.txt` entry ("unsafe or duplicate path"); a duplicate `primary` in a slot; mismatched week/session in a slot; a 65-character slot; a duplicate `session_id`; `plan_name` as an object ("config.json is malformed"); a file name not ending in `.zip` |

---

## 6. Choosing the primary workout format

The owner asked whether FIT workout, the v4 bundle or QDomyos XML would be a better *native* format than the app's own JSON. If one were, the new app would store it natively and convert the others on import. This section answers that question.

### 6.1 What each format can express

Legend: ✅ yes; ⚠️ partial or unreliable; ❌ no.

| Capability | Native JSON (schema v1) | FIT workout file | QDomyos XML | v4 bundle |
|---|---|---|---|---|
| Speed **and** incline in the same step | ✅ each step always has both a speed and an incline directive | ⚠️ one primary target per step. A secondary target exists, and `grade` is a target type, but FIT defines no units or scale for grade values and watches do not execute it (§6.2). | ✅ `speed`/`speedfrom`/`speedto` together with `inclination` | ✅ (its steps are QDomyos XML) |
| Speed ramps | ✅ `ramp` start→end, interpolated continuously | ❌ only a low–high *range*, which means "stay within", not a ramp | ✅ `speedfrom`→`speedto` | ✅ |
| Incline ramps | ✅ | ❌ | ❌ (only a fixed `inclination`) | ❌ |
| HR zone target | ✅ zone 1–10 plus treadmill speed bounds | ⚠️ zone only; there are no treadmill speed bounds | ✅ `zonehr` (speed bounds via `minspeed`/`maxspeed`) | ✅ with an initial speed (bounded-HR rule, §5.4) |
| HR bpm range | ✅ plus speed bounds and an initial speed | ⚠️ `custom_target_heart_rate` as bpm + 100; no speed bounds | ✅ `hrmin`/`hrmax` (no initial speed) | ✅ with an initial speed |
| Open (manual) speed | ✅ | ✅ `open` | ✅ (no speed attribute) | ✅ |
| Nested repeats | ✅ up to 31 levels | ✅ repeat markers (`repeat_until_steps_cmplt`), which can nest | ✅ `<repeat times>` (up to 7 levels in the importer) | ✅ |
| Time goals | ✅ 100 ns resolution | ✅ ms | ✅ whole seconds (`HH:MM:SS`) | ✅ whole seconds |
| Distance goals | ✅ km (double) | ✅ cm | ✅ `distance` in km (the units are ambiguous in the format) | ✅ |
| Cues | ✅ `cue` per step | ✅ `wkt_step_name` (a short string) | ✅ `<textevent message>` (with time offsets; flattened on import) | ✅ |
| Notes | ✅ `notes` per step, and a description per workout | ✅ `notes`, `wkt_description` | ❌ | ❌ |
| Alternatives and variants | ❌ (handled by plans, [04](04-calendar-and-plans.md)) | ❌ | ❌ | ✅ primary / hr-alternative / fixed-fallback / omega-recovery-incline per slot |
| Plan or program structure | ❌ (plans are separate entities referencing revisions) | ❌ in a workout file (training plans are not part of the public workout file type) | ❌ | ✅ weeks, sessions and slots in `workout_index.csv` |
| Tool support | TreadmillRunner only | ✅ Garmin watches and Garmin Connect | ✅ QDomyos-Zwift app | Produced only by the external "treadmill-workout" generator; consumed by TreadmillRunner |
| Human readability and editing | ✅ plain JSON with explicit field names | ❌ binary; needs the SDK | ✅ small XML | ⚠️ a ZIP with a manifest, CSV and many XML files, all with SHA-256 digests |
| Units unambiguous | ✅ km/h, km, % | ✅ SI with scale factors | ❌ speed and distance units are not declared (the app assumes metric) | ❌ (same as QDomyos) |
| Validation and limits well defined | ✅ ([02](02-workouts.md) §4) | ✅ (FIT profile) | ⚠️ loosely specified | ✅ manifest, digests and CSV rules |

### 6.2 Evidence from the format definitions

**FIT workout (`workout_step` message, Garmin FIT SDK 21.205 profile).** The fields are:

| # | Field |
|---|---|
| 254 | `message_index` |
| 0 | `wkt_step_name` |
| 1 | `duration_type` |
| 2 | `duration_value` |
| 3 | `target_type` |
| 4 | `target_value` |
| 5, 6 | `custom_target_value_low`, `custom_target_value_high` |
| 7 | `intensity` |
| 8 | `notes` |
| 9–13 | equipment and exercise fields |
| 19 | `secondary_target_type` |
| 20 | `secondary_target_value` |
| 21, 22 | `secondary_custom_target_value_low`, `secondary_custom_target_value_high` |

What this means for treadmill workouts:
- **One primary target per step.** `target_type` is one of speed, heart_rate, open, cadence, power, **grade (5)**, resistance, power_3s/10s/30s, power_lap, swim_stroke, speed_lap or heart_rate_lap.
- **Speed and grade can coexist only as primary plus secondary target.** The profile defines typed subfields for speed, heart rate, cadence and power (for example `custom_target_speed_low` in m/s with scale 1000), but **no subfield, unit or scale for grade**. A grade value would be a raw number that only our app understands, and Garmin watches do not guide incline. So incline cannot be carried reliably.
- **No ramps.** Custom low/high is a range to stay within. Our exporter already has to describe ramps in `notes` (§4.2).
- **HR targets have no treadmill speed bounds.** Our HR directives need a minimum, initial and maximum speed for safety. FIT has no place for them.
- **Developer fields** (FIT's extension mechanism) could carry incline, ramps and speed bounds, and any message can hold them. But they need `developer_data_id` and `field_description` messages, Garmin Connect and watches ignore them, and the result is a binary private format. It would give the same "only TreadmillRunner understands it" property as native JSON, while being harder to read, debug and hand-edit. **Developer fields therefore do not make FIT a better primary format.**

**QDomyos XML** (attributes the importer recognises, §3.3):
- Treadmill fields: `duration`, `distance`, `speed`, `speedfrom`, `speedto`, `inclination`, `zonehr`, `hrmin`, `hrmax`, `minspeed`, `maxspeed`, `forcespeed`, `looptimehr`, `<textevent timeoffset message>` and `<repeat times>`.
- There is no incline ramp, no notes, no initial HR speed, no workout title (the file name is used), and no declared units. The many bike fields show it is a general multi-device format.

**v4 bundle** (§5):
- `manifest.json` with `format_version` 2, `tool`, `tool_version` 4.x, `compatibility_profile`, `device_profile_ids` and an `artifacts` SHA-256 map;
- `workout_index.csv` with slot, session, variant, week and selection-rule columns;
- per-device QDomyos XML files;
- an optional `config.json`.

It is a *plan packaging* format generated by an external tool. Its step content is QDomyos XML, with the same limits.

### 6.3 Recommendation

**Keep the native workout JSON (schema v1) as the primary, stored workout format.** Reasons:
1. It is the only format that expresses everything the run engine executes **losslessly**: speed and incline in the same step, speed **and** incline ramps, HR directives **with** treadmill speed bounds and an initial speed, notes, and exact durations. Every other format loses at least one of these (§6.1).
2. It is plain, readable, hand-editable JSON with explicit units, and it is trivial to validate and diff. That fits a private, keep-it-simple app.
3. FIT is the right **exchange** format for Garmin, but not a storage format: it is binary, has no ramps or incline, and developer fields only move the proprietary part into a harder container.
4. QDomyos XML is ambiguous about units and cannot express incline ramps or notes.
5. The v4 bundle is a plan package whose steps are QDomyos XML. Plan structure and variants belong to the plan model ([04](04-calendar-and-plans.md)), not to a workout.

Consequences for the app:
- Storage and backups use native JSON.
- **Import:** native JSON is **P1**. QDomyos XML, FIT and the v4 bundle are optional or later and are always converted on import with the §6.4 rules.
- **Export:** native JSON is **P1** (§2.5). FIT is optional, for Garmin. QDomyos XML export is optional and only if wanted.

### 6.4 Conversion rules (into native JSON and back)

"Lossless" means that converting native → X → native gives the same definition.

**Native JSON ↔ native JSON:** lossless. Both dialects (§2) are read. Write the canonical dialect only.

**FIT workout → native** (the rules are in §4.1; the losses are summarised here):

| FIT content | Native result | Lossy? |
|---|---|---|
| time or distance step | time or distance goal (ms → ticks; cm → km) | no |
| speed low == high | Fixed(low × 3.6) | float rounding only (for example 10.0008 km/h) |
| speed range low ≠ high, or a speed zone | **Open** plus a warning | **yes** |
| HR zone 1–10 | HeartRateZone(zone, 0, 0, 0), which **must be edited** before running | yes: speed bounds are missing |
| HR bpm (value + 100) | HeartRate(bpm, 0, 0, 0), which must be edited | yes: speed bounds are missing |
| percent-of-max HR, other targets, secondary targets | Open or ignored, with a warning | **yes** |
| repeat_until_steps_cmplt | Repeat (nesting allowed) | no |
| conditional repeats, other duration types | skipped, with a warning | **yes** |
| step name and notes | cue and notes | no (NULs stripped) |
| incline | always Fixed(0) | **yes**: FIT has none |

**Native → FIT workout** (§4.2): lossy.
- The following are lost as structured data but described in `notes` text: incline (fixed and ramp), speed ramps (exported as a low–high range) and HR speed bounds.
- The step cue becomes `wkt_step_name`; when there is no cue, "Step n" is written.
- Durations are exported as float seconds, so sub-millisecond parts are lost.

**QDomyos XML → native** (§3): mostly lossless for treadmill content.
- Losses are:
  - text-event time offsets (flattened into the cue);
  - bike fields, `forcespeed` and `looptimehr` (dropped, with warnings);
  - the zone, when explicit bpm are also present;
  - a speed given together with an HR target (outside the v4 bounded-HR rule).
- HR rows get initial speed = minimum speed, with a warning.
- Units are assumed metric and must be confirmed.

**Native → QDomyos XML** (optional export; not in the current app):

| Native | QDomyos | Loss |
|---|---|---|
| time goal | `duration="HH:MM:SS"` | sub-second parts are truncated |
| distance goal | `distance` (km) | none |
| Fixed speed | `speed` | none |
| speed Ramp | `speedfrom`, `speedto` | none |
| Open speed | no speed attribute | none |
| HeartRate | `hrmin`, `hrmax`, `minspeed`, `maxspeed`, plus `speed` = the initial speed (the v4 bounded-HR form) | none for our importer in bundle mode. A standalone import re-reads it as Fixed plus a conflict warning, so the initial speed is lost. |
| HeartRateZone | `zonehr`, `minspeed`, `maxspeed`, `speed` | as for HeartRate |
| Fixed incline | `inclination` | none |
| incline Ramp | `inclination` = start percent | **yes** |
| cue | `<textevent message>` | none |
| notes, description | — | **yes** |
| Repeat | `<repeat times>` | none. Nesting deeper than 7 levels cannot be re-imported. |
| title | the file name | the title must be a valid file name |

**v4 bundle → native** (§5): each selected variant becomes one native workout, created by the QDomyos → native rules with the bounded-HR rule. The slot, week and session order becomes a plan ([04](04-calendar-and-plans.md)). Losses:
- the non-selected variants (only one per slot is kept);
- the device files other than Omega Z;
- the per-XML warnings;
- the manifest and tool metadata, except the tool version, which is kept in the description.

**Native → v4 bundle:** not supported. The bundle is produced by the external generator, and plan export is out of scope for this private app.

---

## 7. Test checklist (Kotlin implementation must pass)

**Primary format (§6):**
- [ ] Workouts are stored, backed up and exported in native JSON (schema v1). Every other format is converted into it on import.
- [ ] native → native round-trip is lossless for every sample in `data/workouts/definitions/`.
- [ ] Lossy imports emit the warnings listed in §3.3 and §4.1 and never guess values. HR speed bounds of 0 are flagged for editing.
- [ ] FIT incline is imported as 0. The current app emits no warning for this; recommended: add one warning per file, code `fit.incline-not-supported`.
- [ ] (Optional) Native → QDomyos XML export follows the §6.4 table. Re-importing it in bundle (bounded-HR) mode gives the same definition, except for incline ramps, notes and sub-second durations.

**Pipeline (P1):**
- [ ] Files > 10 MiB or empty are rejected before parsing.
- [ ] Preview stores a copy of the original bytes and their SHA-256, and expires after 15 min. At most 16 previews / 32 MiB are kept, oldest evicted.
- [ ] Confirm re-parses the stored original bytes (not the preview result) and rejects a SHA mismatch (conflict) and an expired preview (gone).
- [ ] A confirm replay with the same operation ID returns the same revision with `replayed = true`. The same ID with another preview is a conflict.
- [ ] Deduplication reuses an existing revision with the same content hash (same source first, then any), and still writes an audit row.
- [ ] The audit row holds the profile (nullable), workout, revision, file name (without directory, ≤ 255), format, source SHA-256, warnings JSON and time.

**Native JSON (P1):**
- [ ] Every case in `data/workouts/native-import-vectors.json` gives the same accept/reject decision, definition content (field by field), expanded step count, duration and warning codes in order.
- [ ] Dialect detection is by the presence of `schema`.
- [ ] Integers reject `1.0` and `1e3`; non-finite numbers are rejected; duplicate keys resolve last-wins.
- [ ] Nesting: authoring allows 7 nested repeats and rejects 8; canonical allows 14 and rejects 15.
- [ ] Exporting a revision as canonical JSON and importing it gives an identical definition (round-trip), for every sample in `data/workouts/definitions/`.

**QDomyos XML (optional):**
- [ ] DTDs and entities are rejected; the root must be `<rows>`; the title is the file name without its directory and last extension.
- [ ] The §3.4 table passes, including warning order and cue joining with `" · "`.
- [ ] Confirm requires `KilometersPerHour` and swaps `assumed-speed-units` for `confirmed-kph-units`.

**FIT workout import and export (optional):**
- [ ] The §4.1 table passes, including repeat-of-repeat collapsing, message-index ordering, float speed conversion and HR +100 offsets.
- [ ] FIT strings are NUL-stripped; a NUL-only or blank string becomes null.
- [ ] The export decodes to the §4.2 field values (serial number from the UUID, notes texts, repeat markers).

**v4 bundle (optional):**
- [ ] The archive safety limits (paths, symlinks, sizes, ratio, entry count) and manifest digests are enforced.
- [ ] The CSV and slot validation rules reject every defect listed in §5.6.
- [ ] Bounded-HR rows become HR directives with initial = `speed`.
- [ ] Selection strategies pick the preferred variant or fall back to `primary`.
- [ ] Confirm creates n workouts, n audit rows and one plan revision in slot order, idempotently.
