---
title: 02 — Workouts
type: specification
status: draft-v2 (scope reduced: no byte-compatibility)
audience: Kotlin implementers of TreadmillRunner-Android
---

# 02 — Workouts

This chapter specifies everything the app does with **workouts**:
- the workout model (schema v1) and its validation rules and limits;
- storage as immutable revisions, and workout kinds (library, manual run, plan-internal);
- expansion of repeats into a flat list of segments;
- the derived summary shown on library cards, plus search, filter and sort;
- the capability preflight that checks targets against the treadmill and the runner profile;
- how a running session consumes a workout (segment cursor, planned targets, overrides, ramps, heart-rate steps, open-ended manual runs);
- the editor, including browser-local drafts.

File formats for importing and exporting workouts are in [03-import-export-formats.md](03-import-export-formats.md). Plans and the calendar, which reference workout revisions, are in [04](04-calendar-and-plans.md). The heart-rate speed controller is in [06](06-profiles-and-heart-rate.md). Command confirmation and treadmill safety are in [09](09-safety-and-command-contract.md).

## 0. Scope and compatibility

The owner decided that the new app keeps **no backwards compatibility** with the current app, except for the run (session) data structure. For workouts this means:

| Topic | Requirement for the Kotlin app |
|---|---|
| Model, rules, limits, behaviour (§1–§9) | **Required.** Re-implement them as specified. |
| JSON shape of a definition (§2) | **Recommended.** Keep the schema v1 shape and property names, so the P1 native JSON importer ([03](03-import-export-formats.md) §2) can read files written by the current app. Native JSON remains the **primary stored format**; FIT, QDomyos XML and the v4 bundle were compared and rejected as primary formats ([03](03-import-export-formats.md) §6). |
| Byte-exact canonical JSON and SHA-256 identical to the current app (§3.1) | **Not required.** It is documented for information only. |
| Hashes of stored revisions | Recompute with the app's own canonical form (§3.2). |

Golden data for this chapter lives in `data/workouts/` (§11). It covers behaviour: validation, expansion, summaries, preflight and the run-time segment cursor. It deliberately contains no byte-level hash vectors.

Terms used in this chapter:
- **Workout:** a named library entry that owns one or more revisions.
- **Revision:** one immutable version of a workout definition.
- **Definition:** the structured content of a revision: title, description and blocks.
- **Block:** either a *step* or a *repeat*.
- **Step (segment):** one unit with a goal (time or distance), a speed directive and an incline directive. The UI calls an expanded step a **segment**.
- **Repeat:** a group of blocks executed *n* times.
- **Expanded steps:** the flat list of steps after all repeats are unrolled (§5).

---

## 1. The model at a glance

```
WorkoutDefinition
  schemaVersion = 1
  title          : String (required, trimmed)
  description    : String? (trimmed; blank -> null)
  blocks         : List<Block> (>= 1)

Block = Step | Repeat

Step
  goal     : TimeGoal(duration > 0) | DistanceGoal(km > 0)
  speed    : Open | Fixed(kph) | Ramp(startKph, endKph)
           | HeartRate(minBpm, maxBpm, initialKph, minKph, maxKph)
           | HeartRateZone(zone 1..10, initialKph, minKph, maxKph)
  incline  : Fixed(percent) | Ramp(startPercent, endPercent)
  cue      : String? (trimmed; blank -> null)   -- shown/spoken when the segment starts
  notes    : String? (trimmed; blank -> null)   -- extra text shown with the segment

Repeat
  repetitions : Int >= 1
  blocks      : List<Block> (>= 1)
```

Derived values (computed and never stored as input):
- `expandedStepCount`: the number of steps after unrolling repeats.
- `knownDuration`: the sum of all time goals after unrolling, or **null (unknown)** if any expanded step has a distance goal.

There is no "open" incline: every step has a fixed incline or an incline ramp. A plain "no incline" step uses `Fixed(0)`.

---

## 2. Schema v1 JSON shape

This is the JSON shape the current app stores for every revision and accepts as native JSON import. The recommended new canonical form (§3.2) uses exactly this shape.

### 2.1 Root object

| Property (order) | JSON type | Required | Rules |
|---|---|---|---|
| `schemaVersion` | integer | yes | Must be `1`. `1.0` is **not** an integer and is rejected on import. |
| `title` | string | yes | Must not be null, empty or whitespace-only. It is trimmed. |
| `description` | string or null | yes when writing (the value may be null) | Trimmed; empty or whitespace-only becomes `null`. |
| `blocks` | array of block objects | yes | At least one block. |

### 2.2 Block objects

A **step** block:

| Property (order) | JSON type | Rules |
|---|---|---|
| `kind` | string `"step"` | Discriminator. Case-sensitive on import. |
| `goal` | object (§2.3) | Required. |
| `speed` | object (§2.4) | Required. |
| `incline` | object (§2.5) | Required. |
| `cue` | string or null | Trimmed; blank becomes null. |
| `notes` | string or null | Trimmed; blank becomes null. |

A **repeat** block:

| Property (order) | JSON type | Rules |
|---|---|---|
| `kind` | string `"repeat"` | Discriminator. |
| `repetitions` | integer | ≥ 1 (a 32-bit signed int). |
| `blocks` | array of block objects | At least one block. Nested repeats are allowed (§4.3). |

### 2.3 Goal objects

| `kind` | Other properties (order) | Units and rules |
|---|---|---|
| `"time"` | `durationTicks`: integer (64-bit) | **1 tick = 100 ns**, so 1 s = 10,000,000 ticks and 1 min = 600,000,000 ticks. Must be > 0. |
| `"distance"` | `kilometers`: number | km. Must be finite and > 0. |

**Converting other time units to ticks (current behaviour):**
- `ticks = truncate_toward_zero(value × scale)`, where the product is one IEEE-754 double multiplication.
- The scale is 10,000,000 for seconds and 600,000,000 for minutes.

Examples:

| Input | Ticks |
|---|---|
| 20 s entered in the editor, sent as the minutes value 20/60 = 0.3333333333333333 | 200,000,000 (0.3333333333333333 × 6e8 rounds to exactly 2e8 in double arithmetic) |
| 7 s → 7/60 minutes | 70,000,000 |
| 90.5 s | 905,000,000 |
| 0.00000019 s | 1 (truncated) |
| 0.00000005 s | 0, which is rejected (duration must be positive) |

In the Kotlin app, store durations as `Long` ticks, or as `Long` milliseconds with a documented conversion. Never store them as floating-point minutes.

### 2.4 Speed directive objects

All speeds are in **km/h**. Values must be finite and ≥ 0. A `-0.0` input is normalised to `0` (§4.2).

| `kind` | Other properties (order) | Rules |
|---|---|---|
| `"open"` | — | No speed target: the runner controls speed manually. |
| `"fixed"` | `kilometersPerHour` | ≥ 0. |
| `"ramp"` | `startKilometersPerHour`, `endKilometersPerHour` | Both ≥ 0. The ramp may go up or down. |
| `"heartRate"` | `minimumBpm`, `maximumBpm`, `initialKilometersPerHour`, `minimumKilometersPerHour`, `maximumKilometersPerHour` | BPM are integers: `1 ≤ minimumBpm ≤ maximumBpm ≤ 250` (equal is allowed). Speeds: `minimumKph ≤ initialKph ≤ maximumKph`, all ≥ 0. All-zero speeds are allowed; importers produce them as "needs editing" (see [03](03-import-export-formats.md)). |
| `"heartRateZone"` | `zoneNumber`, `initialKilometersPerHour`, `minimumKilometersPerHour`, `maximumKilometersPerHour` | `1 ≤ zoneNumber ≤ 10`. The zone refers to the runner profile's HR zones ([06](06-profiles-and-heart-rate.md)). Speed rules as for `heartRate`. |

The `kind` values are case-sensitive in JSON: `heartRate` and `heartRateZone` use camelCase.

### 2.5 Incline directive objects

All inclines are in **percent grade**. Values must be finite. **Negative values are allowed** by the model (decline); the preflight decides whether the treadmill can do them.

| `kind` | Other properties (order) |
|---|---|
| `"fixed"` | `percent` |
| `"ramp"` | `startPercent`, `endPercent` |

### 2.6 Complete example

This is `data/workouts/definitions/mixed-intervals.json`, pretty-printed here for reading:

```json
{
  "schemaVersion": 1,
  "title": "Intervals",
  "description": "Mixed targets",
  "blocks": [
    {
      "kind": "step",
      "goal": { "kind": "time", "durationTicks": 3000000000 },
      "speed": { "kind": "ramp", "startKilometersPerHour": 8, "endKilometersPerHour": 10 },
      "incline": { "kind": "fixed", "percent": 1 },
      "cue": "Warm up",
      "notes": null
    },
    {
      "kind": "repeat",
      "repetitions": 2,
      "blocks": [
        {
          "kind": "step",
          "goal": { "kind": "distance", "kilometers": 0.4 },
          "speed": { "kind": "heartRate", "minimumBpm": 145, "maximumBpm": 155,
                     "initialKilometersPerHour": 10, "minimumKilometersPerHour": 8,
                     "maximumKilometersPerHour": 12 },
          "incline": { "kind": "ramp", "startPercent": 1, "endPercent": 3 },
          "cue": "Work",
          "notes": "Hold form"
        }
      ]
    }
  ]
}
```

It expands to 3 steps. `knownDuration` is `null`, because distance steps exist.

---

## 3. Serialization and content hash

### 3.1 Current canonical form (informational only)

The current app serialises every definition to a **canonical compact JSON string**. It stores that string as the revision's `DefinitionJson` and stores `SHA-256(UTF-8 bytes)` as lowercase hex in `ContentSha256`. The rules, for reference only:
- No whitespace. Property order exactly as in the §2 tables.
- `description`, `cue` and `notes` are always written, with `null` when absent.
- Numbers use .NET's shortest round-trip formatting:
  - integers are written without `.0`, so `9.0` becomes `9`;
  - scientific notation is used below 1E-04 and from 1E+17 upward, in the form `1E-05` and `1E+17`.
- Strings use .NET's default JSON escaping:
  - every non-ASCII character is written as `\uXXXX` (uppercase hex), for example `é` as `\u00E9` and emoji as surrogate pairs;
  - these characters are also escaped: `"` as `\u0022`, `&`, `'`, `+`, `<`, `>`, `` ` `` and DEL;
  - common control characters use their short forms (`\n`, `\t` and so on);
  - `/` is not escaped.

The Kotlin app **does not** need to reproduce these bytes. Do not port the hash values.

### 3.2 Recommended canonical form for the new app

Keep it simple and deterministic:
1. **Use the §2 shape and property order.** Write compact JSON with explicit nulls for `description`, `cue` and `notes`, for example kotlinx.serialization with `explicitNulls = true` and `encodeDefaults = true` and a fixed class-per-kind model with the discriminator `kind` first.
2. **Normalise before writing:**
   - trim strings, and turn blanks into null;
   - turn `-0.0` into `0.0`;
   - write `durationTicks` as an integer.
3. **Numbers:** use whatever stable formatting the chosen serializer produces (for example Kotlin's `9.0`). The formatting only needs to be stable **within the new app**.
4. **Content hash:** lowercase hex `SHA-256` over the UTF-8 bytes of that canonical string. It is used only to detect "nothing changed" when saving (§4.4) and duplicate imports ([03](03-import-export-formats.md) §6).
5. **Importers accept any valid JSON formatting.** Whitespace, number spelling (`8.0` or `8`) and property order must not matter when reading.

---

## 4. Validation rules and limits

Validation happens when a definition is built: in the editor, in importers, and when loading a stored revision. An invalid definition is never stored.

### 4.1 Per-field rules

| Element | Rule | Current error text (for reference) |
|---|---|---|
| `schemaVersion` | must equal 1 | "Only workout schema version 1 is supported." |
| `title` | not null, empty or whitespace-only | "The value cannot be an empty string or composed entirely of whitespace." |
| root `blocks` | ≥ 1 and no null entries | "A workout must contain at least one block." |
| Repeat `repetitions` | ≥ 1 | "Repeat count must be positive." |
| Repeat `blocks` | ≥ 1 and no null entries | "A repeat must contain at least one block." |
| Time goal | duration > 0 | "Duration must be positive." |
| Distance goal | finite and > 0 | "Value must be finite." or "Value must be greater than zero." |
| Fixed and ramp speed values | finite and ≥ 0 | "Value must be finite." or "Value cannot be negative." |
| HeartRate BPM | `minimumBpm ≠ 0`, `maximumBpm ≤ 250`, `minimumBpm ≤ maximumBpm` | "Heart-rate bounds must be ordered and between 1 and 250 bpm." |
| HR speeds (both HR kinds) | each finite and ≥ 0; `min ≤ max`; `min ≤ initial ≤ max` | "Initial HR speed must be inside the ordered minimum and maximum speeds." |
| HeartRateZone `zoneNumber` | 1..10 | "Zone number must be between 1 and 10." |
| Incline values | finite (any sign) | "Value must be finite." |
| cue and notes | no length or charset rule | — |

The rewrite may use its own error texts. The **accept/reject decision** must match `data/workouts/validation-vectors.json`.

**No length limits** are enforced on the title, description, cue or notes today. The database column for the workout name is nominally 160 characters, but SQLite does not enforce that. Recommendation for the new app: limit the title to 160 characters and the description, cue and notes to 2,000 characters in the editor, and reject longer values in importers.

### 4.2 Normalisation

These rules apply in this order when a definition is built:
1. **Strings.**
   - Title: trimmed.
   - Description, cue and notes: null or whitespace-only becomes `null`; otherwise the value is trimmed.
   - Inner whitespace is kept: `"  Padded  title "` becomes `"Padded  title"`.
   - "Whitespace" follows .NET `char.IsWhiteSpace`: U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F and U+3000.
   - Note that Kotlin's `trim()` and `isBlank()` differ from this set: they treat U+001C–U+001F as whitespace but not U+0085. Use an explicit predicate.
2. **Negative zero.** Every speed and incline value equal to 0 (including `-0.0`) is stored as `+0.0`.
3. **Nothing else is rounded or clamped** by the model. Alignment to treadmill increments happens only in the preflight (§7) and is never written back to the stored revision.

### 4.3 Limits

| Limit | Value | Precise rule |
|---|---|---|
| Expanded steps | **10,000** | `expandedStepCount > 10000` is rejected. Exactly 10,000 is valid. |
| Nesting | **32 levels** | Top-level blocks are at depth 1, and each repeat adds 1 for its children. A depth > 32 is rejected. This allows at most **31 nested repeats**. |
| Known duration | **12 h** | The sum of all **time** goals after unrolling must be ≤ 12 h (432,000,000,000 ticks). Exactly 12 h is valid; 12 h + 1 tick is rejected. The limit also applies when the workout contains distance steps: the time part alone must fit. |

Import formats have tighter nesting limits: 7 nested repeats in the native authoring format and in QDomyos XML, and 14 in canonical JSON because of the JSON depth limit. See [03](03-import-export-formats.md).

**Counting algorithm.** It must not overflow, even for `repetitions = 2147483647`:

```
count(blocks, depth):
  if depth > 32: reject "nesting"
  steps = 0; ticks = 0; hasDistance = false
  for block in blocks:
    if block is Step:
      steps += 1
      if goal is Time: ticks += goal.ticks  (checked add)
      else hasDistance = true
    if block is Repeat:
      nested = count(block.blocks, depth + 1)
      steps = satAdd(steps, satMul(nested.steps, block.repetitions, 10001), 10001)
      ticks = satAdd(ticks, satMul(nested.ticks, block.repetitions, 12h+1 tick), 12h+1 tick)
      hasDistance |= nested.hasDistance
    if steps > 10000 or ticks > 12h: return early (the caller rejects)
  return (steps, ticks, hasDistance)

satMul(v, m, limit) = if v > limit / m then limit else v * m
satAdd(a, b, limit) = if a > limit - b then limit else a + b
```

After counting:
- Reject if `steps > 10000`: "A workout can expand to at most 10000 steps."
- Reject if `ticks > 12 h`: "Known workout duration cannot exceed 12 hours."
- Otherwise `expandedStepCount = steps` and `knownDuration = hasDistance ? null : ticks`.

### 4.4 Validation examples (golden)

These rows come from the current unit tests and from additional probes. The full list is in `data/workouts/validation-vectors.json`.

| Given | Expected |
|---|---|
| Step 5 min ramp 8→10 incline 1 "Warm up"; repeat ×2 [0.4 km HR 145–155 (10, 8, 12) incline ramp 1→3 "Work" / "Hold form"] | valid; 3 expanded steps; known duration null |
| One 30 min step HR zone 2 (9, 7, 11) | valid; known duration 30 min |
| One 10 min step, open speed, incline ramp 0→4 | valid; the speed is serialised as `{"kind":"open"}` |
| One step of 12 h + 1 s | rejected (duration) |
| Repeat ×10,001 of one 0.1 km step | rejected (steps) |
| Repeat ×10,000 of one 0.1 km step | valid (10,000 steps) |
| Repeat ×2,147,483,647 of one 1 min step | rejected (steps), with no overflow |
| FixedSpeed(NaN) or FixedSpeed(+∞) | rejected |
| FixedSpeed(−0.1) | rejected; FixedIncline(−3) is valid |
| HeartRateZoneSpeed zone 0 or zone 11 | rejected |
| HeartRateSpeed 150–150 bpm | valid; 0–150, 100–251 and 160–150 are rejected |
| HeartRate initial 6 with min 7 | rejected; min 12 with max 11 is also rejected |
| HeartRateZoneSpeed(3, 0, 0, 0) | valid |
| 31 nested repeats | valid; 32 nested repeats are rejected |
| Two definitions that are identical apart from `FixedSpeed(-0.0)` versus `FixedSpeed(0)` | identical canonical form and identical hash |

---

## 5. Expansion of repeats

The run engine, the timeline and every count use the same **depth-first expansion**:

```
expand(blocks):
  for block in blocks (in order):
    Step   -> emit step
    Repeat -> repeat `repetitions` times: expand(block.blocks)
```

Properties:
- The order is stable and deterministic. Repeated children appear in source order on every pass.
- The same step object is emitted multiple times. Consumers must not rely on identity.
- **Start offset** of expanded step *i*: the sum of the durations of all earlier time steps, as long as every earlier step is a time step. From the first distance step onward, every **later** start offset is unknown and shown as "—". The distance step itself still has a known start.
- **Segment numbering in the UI:** expanded steps are numbered from 1 ("Segment 1 of 16").

Golden expansions are in `data/workouts/definition-examples.json` (`expandedSteps`, with `index`, `sourcePath`, `startSeconds`, `goal`, `speed`, `incline`, `cue`, `notes`). Example `nested-repeats`: a warm-up, then 3 × [2 min fast, 2 × (30 s at 6 km/h, 30 s open)].

| index | sourcePath | startSeconds | goal | speed | cue |
|---|---|---|---|---|---|
| 0 | blocks[0] | 0 | 300 s | ramp 4.5→6 | Warm up |
| 1 | blocks[1].blocks[0] | 300 | 120 s | fixed 9 | Fast |
| 2 | blocks[1].blocks[1].blocks[0] | 420 | 30 s | fixed 6 | — |
| 3 | blocks[1].blocks[1].blocks[1] | 450 | 30 s | open | — |
| 4 | blocks[1].blocks[1].blocks[0] | 480 | 30 s | fixed 6 | — |
| 5 | blocks[1].blocks[1].blocks[1] | 510 | 30 s | open | — |
| 6 | blocks[1].blocks[0] | 540 | 120 s | fixed 9 | Fast |
| … | … | … | … | … | … |
| 15 | blocks[1].blocks[1].blocks[1] | 990 | 30 s | open | — |

This gives 16 expanded steps and a known duration of 1,020 s (17 min).

Example `distance-then-time`: 5 min at 6, then 1 km at 10, then 5 min at 6. The start offsets are 0, 300 and **null**.

---

## 6. Derived summary, library cards, search and filter

### 6.1 Summary algorithm

The library shows a summary of each workout's **latest** revision. It is computed from the definition by one recursive pass with a `multiplier`, which starts at 1 and is multiplied by `repetitions` for each enclosing repeat.

```
for each block (depth-first, multiplier m):
  Step:
    steps += m
    time goal     -> hasTime = true;     durationTicks += ticks × m
    distance goal -> hasDistance = true; distanceKm += km × m
    representativeSpeeds.append(       // ONE entry per source step, not per repetition
       fixed -> kph; ramp -> endKph; heartRate/heartRateZone -> initialKph; open -> null)
    speed:
      open          -> hasOpenSpeed = true
      fixed         -> speedRange ∪= [kph, kph]
      ramp          -> hasRamp = true; speedRange ∪= [min(s,e), max(s,e)]
      heartRate     -> usesHeartRate = true; speedRange ∪= [minKph, maxKph];
                       bpmMin = min(bpmMin, minBpm); bpmMax = max(bpmMax, maxBpm)   (a bpm of 0 is ignored)
      heartRateZone -> usesHeartRate = true; speedRange ∪= [minKph, maxKph]; zones.add(zoneNumber)
    incline:
      ramp  -> hasRamp = true; inclineRange ∪= [min(s,e), max(s,e)]
      fixed -> inclineRange ∪= [p, p]
  Repeat:
    hasRepeat = true; recurse with m × repetitions
```

`expandedStepCount = steps`. `durationMinutes = hasDistance ? null : durationTicks / 600,000,000` (fractional minutes).

### 6.2 Structure label (classification)

The first matching rule wins:

| # | Condition | Label |
|---|---|---|
| 1 | `usesHeartRate` and `hasRepeat` | `HR intervals` |
| 2 | `usesHeartRate` | `HR adaptive` |
| 3 | `hasRepeat` | `Intervals` |
| 4 | `hasRamp` (a speed **or** incline ramp) | `Progression` |
| 5 | `representativeSpeeds` without nulls has ≥ 2 distinct values: if the sequence is non-decreasing **and** last > first, then `Progression`, otherwise `Intervals` | `Progression` / `Intervals` |
| 6 | `steps == 1` | `Steady` |
| 7 | otherwise | `Multi-stage` |

Notes:
- An open-speed step is skipped in rule 5. For example [6, open, 7] gives `Progression`.
- Rule 5 compares doubles exactly.

### 6.3 Other labels

Numbers are formatted with the pattern **`0.##`**:
- at most 2 decimals, trailing zeros removed, `.` as the decimal separator;
- rounding is half away from zero, applied to the shortest round-trip decimal form of the double. For example 1.005 gives `1.01` and 6.456 gives `6.46`.

The labels use two special characters: `–` is U+2013 (en dash) and `·` is U+00B7 (middle dot).

| Label | Rule |
|---|---|
| Goal label | If `hasTime` and `hasDistance`: `Time + distance`. If only distance: `{distanceKm:0.##} km`. Otherwise: `{durationMinutes:0.##} min`. |
| Speed range text | If no speed range: `No fixed km/h`. If `abs(min − max) < 0.001`: `{min} km/h`. Otherwise: `{min}–{max} km/h`. |
| Speed label | If zones are used: `Z{a} · {range}`, or `Z{a}–Z{b} · {range}` with a = the lowest zone and b = the highest. Else if both bpm bounds are known: `{bpmMin}–{bpmMax} bpm · {range}`. Else if there is an open speed and no speed range: `Manual speed`. Else if there is an open speed: `Manual + {range}`. Otherwise: `{range}`. |
| Incline label | Same as the range text with the suffix `% incline`, for example `0–4% incline`. With no range: `No fixed % incline`. |
| `usesHeartRate` | true if any step has an HR directive. |

Zones take precedence over explicit bpm in the speed label, even when both occur.

### 6.4 Summary examples (golden)

The full set is in `data/workouts/summary-vectors.json`, and every entry of `definition-examples.json` also carries a summary.

| Definition | Structure | Goal | Speed | Incline |
|---|---|---|---|---|
| 1 step 30 min fixed 8 | Steady | 30 min | 8 km/h | 0% incline |
| 2 steps 10 min fixed 8 (incline 0, then 2) | Multi-stage | 20 min | 8 km/h | 0–2% incline |
| 3 × 5 min fixed 6, 6, 7 | Progression | 15 min | 6–7 km/h | 0% incline |
| 3 × 5 min fixed 6, 8, 6 | Intervals | 15 min | 6–8 km/h | 0% incline |
| 2 × 5 min fixed 8, 7 | Intervals | 10 min | 7–8 km/h | 0% incline |
| 1 step 10 min ramp 6→9 | Progression | 10 min | 6–9 km/h | 0% incline |
| 1 step 10 min fixed 6, incline ramp 0→5 | Progression | 10 min | 6 km/h | 0–5% incline |
| repeat ×4 [1 min at 10, 1 min at 6] | Intervals | 8 min | 6–10 km/h | 0% incline |
| 1 step 30 min zone 2 (7, 6, 8) | HR adaptive | 30 min | Z2 · 6–8 km/h | 0% incline |
| repeat ×3 [3 min HR 140–160 (9, 8, 11) incline 1; 2 min fixed 6] | HR intervals | 15 min | 140–160 bpm · 6–11 km/h | 0–1% incline |
| zone 2 (7, 6, 8) + zone 4 (9, 8, 10), 10 min each | HR adaptive | 20 min | Z2–Z4 · 6–10 km/h | 0% incline |
| 1 step 10 min open | Steady | 10 min | Manual speed | 0% incline |
| 10 min open + 10 min fixed 7 | Multi-stage | 20 min | Manual + 7 km/h | 0% incline |
| repeat ×4 [0.4 km fixed 10 incline 1] | Intervals | 1.6 km | 10 km/h | 1% incline |
| 5 min fixed 6 + 1 km fixed 8 | Progression | Time + distance | 6–8 km/h | 0% incline |
| 20 s fixed 6.456 incline 1.005 | Steady | 0.33 min | 6.46 km/h | 1.01% incline |
| Manual run template (§8.6) | Steady | 240 min | 0.8 km/h | 0% incline |

### 6.5 Library (standalone workouts)

**What is listed.** A workout appears in the library only if all of these hold:
- it is **not archived**;
- its kind is **not** `PlanInternal` (§8.1);
- no revision of it is referenced by an item of a plan revision that has a template ID (premade-plan provenance), which also covers legacy rows;
- its kind is **not** `ManualTemplate` (the manual-run workout is hidden from the library and from plan pickers).

**Card content.** Each card shows:
- the eyebrow "Revision {latestRevisionNumber}";
- the name, as a button that opens the details;
- the description, or "No description yet.";
- the structure label, plus a "Heart rate" badge if `usesHeartRate`;
- stats: Segments = `expandedStepCount`, Total = goal label, Speed = speed label, Incline = incline label;
- actions: **View details**, **Schedule** (primary), and **More**. More holds **New revision** (opens the editor on the latest revision) and **Archive**.

**Archive confirmation.** Archive asks for confirmation with the title "Archive {name}?" and the text "It will be hidden from the workout library. Existing session history is kept." The buttons are **Cancel** (the default and safe action) and **Archive workout**.

**Toolbar.** The toolbar holds search, filters and sort. Show "{n} result(s)".

| Control | Values and behaviour |
|---|---|
| Search | Case-insensitive substring match on the name, description, structure label, goal label, speed label or incline label. |
| Duration | `all`; `short` = `durationMinutes ≤ 30`; `medium` = `> 30 and ≤ 60`; `long` = `> 60`; `distance` = `durationMinutes` is null (any distance step). The short, medium and long filters exclude workouts with an unknown duration. |
| Structure | `all`; `steady` = label `Steady`; `intervals` = the label contains "interval", case-insensitive, so it matches both `Intervals` and `HR intervals`; `progression` = `Progression`; `heart-rate` = `usesHeartRate`; `multi-stage` = `Multi-stage`. |
| Sort | `updated` (default) = the latest revision's creation time, newest first; `title` = name, case-insensitive; `duration` = `durationMinutes` ascending with unknown durations last, then name. |
| Clear | Resets all of the above. It is disabled when nothing is set. |

**Empty states:**
- If the library is empty: "Your library is empty."
- Otherwise: "No workouts match these filters."
- Both show a "Build a workout" action.

**Details view.** The details view renders the latest revision as a tree without flattening repeats:
- A top-level step is labelled "Segment {i}". A step inside a repeat is labelled "Step {i}". The index is 1-based within the parent list.
- Each item shows its start time `H:MM:SS`, or "—" when unknown. The start time is rounded half away from zero to whole seconds.
- A step shows its goal (`{v} min` or `{v} km`) and its speed:

  | Speed kind | Shown as |
  |---|---|
  | fixed | `{v} km/h` |
  | ramp | `{a} → {b} km/h` |
  | heartRate | `{min}–{max} bpm · {minKph}–{maxKph} km/h` |
  | heartRateZone | `Heart-rate Z{n} · {minKph}–{maxKph} km/h` |
  | open | `Manual speed` |

  It also shows the incline (`{v}% incline` or `{a} → {b}% incline`), "Cue: …" and "Note: …".
- A repeat shows "Repeat", its start time, "{n} × this pattern" and "{k} expanded segment(s)".

**"Run again" (reuse).** For the active runner, the app lists the most recent **Completed** sessions:
- Excluded: `SystemTest` sessions, and sessions whose workout is archived, not `Structured`, or plan-internal.
- The sessions are grouped by exact revision and ordered by the last completion time, newest first.
- By default 4 groups are shown (allowed range 1–12). Each shows its completion count and the last actual duration.
- Choosing one runs that **exact revision** again.

---

## 7. Capability preflight

Before a session is armed, the workout's targets are checked against:
- the **verified treadmill ranges** (speed and incline: minimum, maximum and increment, as exact decimals; see [08](08-ftms-and-treadmill.md));
- the runner profile's **maximum speed** (nullable; [06](06-profiles-and-heart-rate.md)).

The result has two parts:
1. A list of evaluated targets.
2. A **normalised copy** of the definition, used for execution. It is never written back to the stored revision, with one exception: premade-plan installation stores normalised definitions ([04](04-calendar-and-plans.md)).

The workout is **valid** if no target is `Rejected`.

### 7.1 Rules

The guiding principle is that targets are **never** made more aggressive. Values are only ever aligned *down*. A value outside the range is **rejected, not clamped**.

Constants: tolerance `ε = 0.000001`.

**Step 1 — profile below treadmill minimum.** If a speed range and a profile maximum both exist and `profileMax < speedRange.min − ε`, add a rejection:
- path `profile.maximumSpeed`, kind Speed;
- requested = profileMax, normalised = null;
- reason "Profile maximum is below the treadmill minimum of {min} km/h."

This check applies even to a workout with only open speed.

**Step 2 — every target, in depth-first block order.** Each block has a path `blocks[i]`, and a repeat's children are at `blocks[i].blocks[j]`. Targets are evaluated in this order:

| Directive | Evaluated values (path suffix) |
|---|---|
| Open speed | none (no target) |
| Fixed speed | `.speed` |
| Speed ramp | `.speed.start`, then `.speed.end` |
| HeartRate or HeartRateZone | `.speed.minimum`, `.speed.maximum`, `.speed.initial` (in that order). BPM and zone are not checked. |
| Fixed incline | `.incline` |
| Incline ramp | `.incline.start`, then `.incline.end` |

**Step 3 — evaluate one value.** For speed, the profile maximum is used. For incline, it is not.

```
evaluate(path, kind, requested, range, profileMax):
  if range == null:
    if kind == Speed and profileMax != null and requested > profileMax + ε:
      -> Rejected (normalized = null), reason "Target exceeds the profile maximum of {profileMax} km/h."
         the step keeps the requested value
    -> Accepted (normalized = requested), reason "No verified hardware range is active."
  min = range.min
  max = min(range.max, profileMax ?? +∞)
  if requested < min − ε or requested > max + ε:
    -> Rejected (normalized = null), reason "Target is outside the verified {min}–{max} range."
       the step keeps the requested value
  offset  = toDecimal(requested) − range.min           (exact decimal arithmetic)
  steps   = floor(offset / range.increment)
  aligned = toDouble(range.min + steps × range.increment)
  aligned = clamp(aligned, min, max)
  if |aligned − requested| > ε:
    -> Normalized (normalized = aligned),
       reason "Aligned down to the verified {increment} increment without increasing intensity."
  else
    -> Accepted (normalized = aligned),
       reason "Target is within the verified range and increment."
```

The value used for execution is `aligned`, even when it is Accepted.

Details:
- **`toDecimal(double)`** reproduces .NET's double-to-decimal conversion: round the exact binary value to **15 significant digits, HALF_EVEN**. In Kotlin: `BigDecimal(d).round(MathContext(15, RoundingMode.HALF_EVEN))`. This matters: with an exact `BigDecimal(7.6)` = 7.59999…, `floor((7.6 − 0.8) / 0.1)` would give 67 and align to 7.5 (wrong). With 15 digits it is 7.6, which gives 68 and 7.6.
- **`toDouble(decimal)`** is the nearest double (`BigDecimal.toDouble()`).
- Numbers in reasons use up to 3 decimals, invariant (`0.###`). The current app uses the machine culture here, which is a minor defect; use `.` as the separator.
- **HR directives.** After the three values are evaluated, the normalised min, max and initial are re-checked:
  - if `min ≤ initial ≤ max` still holds, a new directive with the same BPM or zone is built;
  - otherwise the original directive is kept unchanged (a defensive fallback).
- **Incline** uses the same algorithm with the incline range, and never the profile maximum.

**Step 4 — use of the result:**
- The preflight check "Workout targets" is:
  - **Blocked** when invalid. The message is the first 3 rejections, joined by spaces, each as `"{path}: {reason}"`.
  - **Ready** with "Targets are valid; safer treadmill-increment alignment will be applied." when anything was Normalized.
  - **Ready** with "Targets fit the selected profile and verified treadmill limits." otherwise.
- Arming refuses an invalid workout with "Workout targets exceed the selected profile or verified treadmill capabilities." Otherwise the **normalised definition** is what the session executes.
- The simulator uses the ranges speed 0.8–20 by 0.1 km/h and incline 0–12 by 0.1 %.

### 7.2 Preflight examples (golden)

Speed range 0.8–20 by 0.1 km/h and incline range 0–12 by 0.1 %, unless stated otherwise. The full data is in `data/workouts/preflight-vectors.json`.

| Case | Profile max | Workout (1 × 1 min step) | Result |
|---|---|---|---|
| align-down | 12 | fixed 7.56, incline 1.06 | valid. speed → 7.5 **Normalized**; incline → 1.0 **Normalized** |
| exact increment | 12 | fixed 7.5, incline 1 | valid. Both **Accepted** |
| reject, no clamp | 12 | ramp 7→13, incline ramp 1→13 | invalid. `speed.start` Accepted; `speed.end` **Rejected** ("outside the verified 0.8–12 range"); `incline.start` Accepted; `incline.end` **Rejected** ("0–12"). The definition keeps 13. |
| nested HR | 10 | repeat ×2 [HR 120–140 (6.06, 5.06, 8.06)] | valid. Paths `blocks[0].blocks[0].speed.minimum`, `.maximum`, `.initial` → 5, 8, 6, all Normalized |
| HR zone | 10 | zone 3 (6.06, 5.06, 8.06) | valid. Zone 3 kept; 6/5/8 |
| profile max, no hardware | 12 | fixed 12.1 | invalid. `blocks[0].speed` Rejected ("exceeds the profile maximum of 12 km/h"); incline Accepted ("No verified hardware range is active.") |
| no ranges, no profile | null | fixed 25, incline −3 | valid. Both Accepted unchanged |
| profile below treadmill minimum | 0.5 | open speed | invalid. Only `profile.maximumSpeed` is Rejected |
| tolerance | 12 | fixed 12.0000001, incline 12.0000001 | valid. Both **Accepted** with a normalised value of 12 |
| below minimum | 12 | fixed 0.5, incline −0.5 | invalid. Both Rejected |
| open speed | null | open, incline 0.25 | valid. Only the incline is evaluated: 0.2 Normalized |
| HR collapse | 12 | HR 120–140 (5.05, 5.05, 5.09) | valid. All three → 5.0 (min = initial = max = 5) |
| increment 0.5 (range 1–16) | null | fixed 7.9 | valid. 7.5 Normalized ("0.5 increment") |
| profile caps the hardware maximum | 14.95 | fixed 15 | invalid. "outside the verified 0.8–14.95 range" |

---

## 8. Revisions, kinds and the editor

### 8.1 Workout and revision records

| Workout field | Rules |
|---|---|
| id | UUID |
| name | The **title of the latest saved revision**. It is updated when a new revision is appended. |
| kind | `Structured` (default), `ManualTemplate` or `PlanInternal` |
| isArchived | Default false. Archiving hides the workout; it never deletes it. |
| createdAt | UTC |

| Revision field | Rules |
|---|---|
| id | UUID. **Sessions, calendar entries and plan items reference revision IDs, never workout IDs.** |
| workoutId | The owning workout |
| revisionNumber | 1, 2, 3, … per workout. Unique per workout. |
| definition | The canonical JSON (§3.2) |
| contentHash | SHA-256 hex of the canonical JSON. Unique per workout. |
| createdAt | UTC |

**Immutability.** A revision is never updated or deleted. The data layer must refuse it.

**Create.** Validate the definition, then create the workout with `name = title` and the requested kind, plus revision 1.

**Append (save edits).** Validate the definition and compute its hash.
- If **any existing revision of that workout** already has the same hash, return that revision. No new row is written and the name is not updated.
- Otherwise create revision `max(revisionNumber) + 1` and set the workout name to the new title.

A quirk of the current app: saving content equal to an *older* revision returns that older revision, but the library keeps showing the latest one. Recommendation: compare only with the **latest** revision, and otherwise always append.

**Idempotent writes.** Every create, append, archive and import confirmation carries a client-generated operation ID plus a fingerprint of the request:
- Replaying the same operation returns the stored result.
- Reusing the ID for a different request is a conflict.

On the phone this still matters for the web UI, where browser retries happen. Keep it.

**Archive.** Set `isArchived = true`. Archived workouts:
- are hidden from the library, pickers and "Run again";
- keep their revisions resolvable by ID for history, the calendar and plans.

There is no unarchive action in the UI.

### 8.2 Kinds and plan-internal visibility

| Kind | Created by | Visible in the library and pickers |
|---|---|---|
| `Structured` | The editor (default), native and other imports, and v4 bundle imports | yes |
| `ManualTemplate` | The first press of **Manual run** (§8.6) | no |
| `PlanInternal` | Plan generation, and the editor when a new workout is created from inside the plan editor ("return to plan" flow) | no. Only its owning plan shows it. |

**Plan-internal rule** (the flag used everywhere): a workout is plan-internal if either of these holds:
- `kind == PlanInternal`;
- **any** of its revisions is referenced by an item of a plan revision that has a **template ID** (premade provenance).

The second condition catches older generated workouts stored as `Structured`. Custom plans without a template ID do **not** hide their workouts.

### 8.3 Editor (web UI) behaviour

The editor edits a tree of blocks and sends one flat request per block.

**Block request fields:**
- `kind` (`step` or `repeat`, case-insensitive), `repetitions` and `blocks`;
- `goalKind` (`time` or `distance`) and `goalValue` (**minutes** for time, **km** for distance);
- `speedKind` (`open`, `fixed`, `ramp`, `heartRate` or `heartRateZone`, case-insensitive), `speedStartKph` and `speedEndKph`;
- `heartRateMinimumBpm`, `heartRateMaximumBpm`, `heartRateZoneNumber`, `heartRateInitialSpeedKph`, `heartRateMinimumSpeedKph` and `heartRateMaximumSpeedKph`;
- `inclineKind` (`fixed` or `ramp`), `inclineStartPercent` and `inclineEndPercent`;
- `cue` and `notes`.

The request has a name, a description and a `kind` (default `Structured`).

**Server-side mapping to the model:**
- time → `minutes → ticks` (§2.3);
- fixed → `FixedSpeed(speedStartKph)`;
- ramp → `(speedStartKph, speedEndKph)`;
- incline fixed → `inclineStartPercent`.

A null block, a null child list or an unknown discriminator is a validation error (HTTP 400 in the current app).

**Loading a revision for editing.** The definition is flattened into these requests:
- missing numbers become 0;
- a fixed speed fills `speedStartKph` (and the editor mirrors it into `speedEndKph`);
- a fixed incline fills `inclineStartPercent`.

**Editor input constraints** (UI level; the model rules in §4 still apply):

| Field | Constraint |
|---|---|
| Duration | Text `mm:ss`: minutes are digits only, seconds 0–59. The total must be 1–43,200 s. It is converted to minutes as `totalSeconds / 60`. It is displayed as `mm:ss` with seconds rounded half away from zero, minimum 1 s. |
| Distance | 0.01–1000 km, step 0.01 |
| Speed and HR speeds | 0–40 km/h, step 0.1 |
| Incline | −5 to 40 %, step 0.1 |
| BPM | 1–250 |
| Zone | 1–10 |
| Repeat count | 1–100 |

**New-step defaults:**
- time 5 min;
- fixed 7 km/h (ramp end 9);
- HR 120–150 bpm, zone 2;
- HR speeds initial 7, min 4, max 10;
- incline fixed 0.

A new repeat has 2 repetitions and one default child step.

**Quick-add presets** (all time goals with a fixed incline):

| Preset | Content |
|---|---|
| Warm-up | 5 min, ramp 4.5→6, incline 0.5, cue "Ease into your run" |
| Steady | 10 min, fixed 7, incline 1, cue "Settle into a steady rhythm" |
| Intervals | Repeat ×4 [2 min fixed 8, incline 1, "Fast interval"; 2 min fixed 5.5, incline 0.5, "Recovery"] |
| Cool-down | 5 min, ramp 6→4.5, incline 0.5, cue "Let your heart rate come down" |

**Block operations:**
- move up or down within the same list;
- duplicate (a deep copy inserted after the block);
- remove (ignored when it is the only block in its list);
- select several blocks, then copy them and paste them at the end of the root list.

**Live readouts:**
- the expanded step count;
- the planned duration as `{minutes:0.#} min`, or "mixed time/distance" when any distance step exists;
- per-block start labels `H:MM:SS`, or "—" after a distance step.

The preview chart plots planned speed and incline per expanded segment.

**Saving:**
- Creating a workout posts a create request. Editing posts an append request. Both reuse a pending operation ID until they succeed.
- On success, the draft is deleted and the message "Saved immutable revision {n}." is shown. The editor then navigates to the library, or back to the plan editor with the new revision ID.
- On failure it shows a retry message; the same operation ID is reused safely.

### 8.4 Drafts (unsaved editor state)

Drafts are browser-local today. On the phone, use the same rules with a small Room table or DataStore keyed the same way.

| Aspect | Rule |
|---|---|
| Key | `treadmillrunner.draft.v1.` + `{profileId as 32 hex characters without dashes, or "household"}.workout.{workoutId as 32 hex characters, or "new"}` |
| Stored envelope | `{ "schemaVersion": 1, "savedAtUtc": ISO-8601, "payload": "<JSON string>" }` |
| Payload | `{ "Name", "Description", "Blocks": [flat block requests as in §8.3] }` |
| When saved | 450 ms after the last input or change event (debounced) |
| Size limit | UTF-8 payload ≤ 256 KiB. When it is larger, the draft is not saved and the message "Browser draft recovery is unavailable because this draft is too large or local storage is unavailable." is shown. |
| Expiry | A draft older than 30 days, from the future, or with a malformed envelope is deleted on load and ignored. |
| Recovery UI | "Unfinished workout found." with **Continue draft** (restores the fields, then shows "Browser-local draft restored.") and **Discard draft**. A payload that cannot be parsed is discarded. |
| Deleted when | The workout is saved successfully, or the user discards the draft |
| Profile switch | The key is recomputed and a pending draft for the new key is offered |

Plan drafts use the same mechanism with a different key.

### 8.5 Revision history and export

- The revision list for a workout is ordered by revision number, newest first.
- Each revision can be opened by its ID.
- Each revision can be exported as a FIT workout file ([03](03-import-export-formats.md) §4, optional).

### 8.6 Manual run (open-ended session)

The Run screen offers **Manual run** when neither the calendar nor a plan supplies a workout.
1. On first use, the app creates one workout of kind `ManualTemplate`:
   - title "Manual run";
   - description "Open-ended treadmill session controlled from the live dashboard.";
   - one step: time 240 min, **fixed 0.8 km/h**, incline fixed 0, cue null, notes "Manual speed and incline control.".
   (See `data/workouts/definitions/manual-run-template.json`.)
2. Later manual runs reuse the first non-archived `ManualTemplate`.
3. A manual run is a normal session with selection source `Manual`.

Consequences of the current design:
- Because the step is **fixed 0.8**, the first fixed-target application (§9.2) sets the belt to 0.8 km/h, clamped to the treadmill minimum. After that, every console or app change sticks for the rest of the (single) segment.
- After 240 min the workout completes like any other workout.

Recommendation: keep this behaviour. It is safe (a slow start) and simple. A `ManualTemplate` is excluded from the library and from "Run again".

---

## 9. How a running session consumes a workout

### 9.1 Segment cursor (WorkoutProgression)

The session builds a cursor over the **expanded steps** of the *normalised* definition (§7). The cursor is driven only by the session's **authoritative, monotonic totals**:
- elapsed running time, which does not advance while paused;
- distance in km (see [05](05-sessions-and-recording.md)).

**State:**
- `currentStepIndex`, starting at 0. When it equals `totalStepCount`, the workout is complete.
- `lastElapsed` and `lastDistanceKm`.
- `stepStartedAtElapsed` and `stepStartedAtDistanceKm`.
- `progressStartedAtElapsed`, used after a restart.

**`advance(elapsed, distanceKm)`:**
- Throws if `elapsed < lastElapsed` ("Elapsed workout time cannot move backwards.").
- Throws if the distance is non-finite or `< lastDistanceKm` ("Workout distance must be finite and cannot move backwards.").
- Stores both values.
- If already complete, returns no transitions.
- Otherwise it loops **while the current step's goal is complete**:
  - A **time** goal is complete when `lastElapsed − stepStartedAtElapsed ≥ duration`. The completion instant is `stepStartedAtElapsed + duration`, so the time does not drift with the sampling instant.
  - A **distance** goal is complete when `lastDistanceKm − stepStartedAtDistanceKm + 1e-9 ≥ km`. The completion distance is `stepStartedAtDistanceKm + km`.
  - For each completed step it emits a transition `(completedStepIndex, newCurrentIndex or null if complete, atElapsed, atDistanceKm)`, increments the index, and starts the next step at the completion instant and distance.
  - A large jump in elapsed time (for example after a reconnect) therefore completes **several** steps in one call.

**Derived values:**

| Value | Rule |
|---|---|
| `progressFraction` | For time steps `(lastElapsed − stepStart) / duration`; for distance steps `(lastDist − stepStartDist) / km`; clamped to [0, 1]. It is 1 when complete. |
| `plannedSpeedKph` | fixed → kph; ramp → `start + (end − start) × progressFraction`; heartRate or heartRateZone → `initialKph`; open → null; complete → null. |
| `plannedInclinePercent` | fixed → percent; ramp → linear interpolation as for speed; complete → null. |
| `heartRateTarget` | heartRate → (minBpm, maxBpm, zone null); heartRateZone → (null, null, zone); otherwise null. |
| `remainingDuration` | 0 if complete. Null if any remaining step (current or later) is a distance step. Otherwise `(current duration − time spent in the current step, at least 0) + the sum of later durations`. |
| `elapsedSinceRestart` | `lastElapsed − progressStartedAtElapsed` |
| `currentStep` and `nextStep` | Expose the cue, notes and planned values for display. `nextStep` is null on the last step. |

**`restart(elapsed, distanceKm)`** ("Reset progress" while paused and stopped):
- index = 0;
- the step start and the progress start become the given totals.

The session totals themselves are **not** erased.

**Checkpoint and restore** (crash or restart recovery). Persist and restore all six state values. Restore rejects a checkpoint when any of these holds:
- the index is outside 0..count;
- a time is negative;
- `stepStart > last`;
- `progressStart > last`;
- a distance is non-finite or negative;
- `stepStartDistance > lastDistance`.

**Golden traces** are in `data/workouts/progression-vectors.json`. Example (unit test "Time and distance steps advance from monotonic authoritative totals"): warm-up 10 s at fixed 8 and incline 1, then repeat ×2 [0.1 km, ramp 8→10, incline ramp 1→3].

| advance(elapsed s, km) | Transitions (completed → current @ s, km) | Index | Progress | Planned kph / % |
|---|---|---|---|---|
| initial | — | 0 | 0 | 8 / 1 |
| (10, 0.02) | 0 → 1 @ 10, 0.02 | 1 | 0 | 8 / 1 |
| (12, 0.07) | — | 1 | 0.5 | 9 / 2 |
| (15, 0.12) | 1 → 2 @ 15, 0.12 | 2 | 0 | 8 / 1 |
| (20, 0.22) | 2 → complete @ 20, 0.22 | 3 (complete) | 1 | null / null |

Other traces in the file:
- **Large jump:** 3 × 1 min steps with `advance(130 s, 0.2)` gives two transitions, at **60 s and 120 s**, then index 2 with progress ≈ 0.1667 and 50 s remaining. The checkpoint round-trips.
- **Restart:** `advance(75, 0.15)`, then `restart(75, 0.15)`, then `advance(90, 0.18)` gives index 0, progress 0.25 and elapsed-since-restart 15 s. Going backwards in time or distance is rejected.
- **Distance epsilon:** a 0.1 km step is not complete at 0.0999999985 km, but is complete at 0.0999999995 km.
- **Ramp interpolation:** a 10 min ramp 6→9 km/h with incline 4→0 gives 6.75 km/h and 3 % at 150 s, and 7.5 km/h and 2 % at 300 s.

### 9.2 Applying targets to the treadmill

The engine turns the cursor into automated commands. Every command goes through the confirmation and safety contract in [09](09-safety-and-command-contract.md). Automation only runs when all of these hold:
- the session is `Running`;
- automation is not suspended;
- treadmill telemetry is fresh;
- the connection generation is unchanged;
- for HR steps, fresh HR telemetry is available.

| Situation | Rule |
|---|---|
| **Fixed speed or fixed incline** | Applied **once per segment**. The engine remembers the index of the last segment whose fixed target was applied (separately for speed and incline, initially none). While `appliedIndex ≠ currentIndex` and there is no override, it requests `clamp(planned, range.min, min(profileMax, range.max))` if `|requested − measured| > max(0.15, increment / 2)`. Once the measured value is within that tolerance, or the command is confirmed, `appliedIndex = currentIndex`. After that, the segment is **not re-enforced**, so console changes stick. |
| **Manual override** (app speed or incline set by the runner) | Stored as `speedOverride` or `inclineOverride`. The requested value is `override ?? planned ?? measured`. An override sticks **for the rest of the segment** and suppresses planned commands for that axis. A manual speed override also suspends HR automation until it is explicitly re-enabled. |
| **Segment change** | When `advance` changes the index: clear both overrides, append a `WorkoutStepTransition` event (completed index, new index, new cue), and announce the new cue. The new segment's fixed targets are then applied once. |
| **Ramps** | Applied **continuously**. For a speed ramp (or incline ramp) the planned value is re-evaluated every tick and a command is sent whenever `|requested − measured|` exceeds the tolerance, unless an override exists for that axis. |
| **Open speed** | No speed command. `planned` is null, so `requested = measured`. |
| **HeartRate or HeartRateZone speed** | Speed is controlled by the HR speed controller ([06](06-profiles-and-heart-rate.md)). Its bounds are `max(directive.min, range.min)` and `min(directive.max, profileMax, range.max)`, using the target BPM or zone and the range increment. The session records its HR controller configuration as `shadow` when the workout contains any HR step, and as `disabled` otherwise; automation modes are defined in [06](06-profiles-and-heart-rate.md). Incline still follows the fixed or ramp rules. |
| **Profile maximum** | If the profile has no maximum speed, 20 km/h is used for clamping. |
| **Pause** | Pause stops the belt and **keeps progress**: elapsed time and distance do not advance, so the cursor does not move. Automation is suspended until resume. |
| **Workout complete** | The index equals the count. On hardware the engine may request a completion Stop, depending on the completion-stop policy in [09](09-safety-and-command-contract.md); otherwise it warns "Workout steps are complete…" and waits for a physical stop. In the simulator the session is finalised as Completed. |
| **Reset progress** | Allowed only while paused and the belt is confirmed stopped. It restarts the cursor, clears overrides and applied indices, disables HR automation, and re-applies step-0 targets after the next start with fresh motion. |

Samples record `PlannedSpeedKph` and `PlannedInclinePercent` from the cursor at capture time. See [05](05-sessions-and-recording.md).

---

## 10. Where workouts come from

| Source | Kind | Notes |
|---|---|---|
| Editor | `Structured` (or `PlanInternal` from the plan editor) | §8.3 |
| Native JSON import (P1) | `Structured` | [03](03-import-export-formats.md) §2 |
| QDomyos XML, FIT workout, v4 bundle (optional) | `Structured` | [03](03-import-export-formats.md) §3–§5. The v4 bundle also creates a plan. |
| Premade plan install | Generated definitions linked to a template program | [04](04-calendar-and-plans.md). Plan-internal through template provenance. |
| Manual run | `ManualTemplate` | §8.6 |

---

## 11. Golden data (`data/workouts/`)

| File | Content |
|---|---|
| `definitions/*.json` | Sample definitions in the schema v1 shape (compact, as the current app writes them). Useful as import fixtures and UI samples. |
| `definition-examples.json` | For each sample: `expandedStepCount`, `knownDurationSeconds`, the derived `summary` (§6) and the full `expandedSteps` list with start offsets (§5). |
| `validation-vectors.json` | Accept/reject cases for the model (§4). |
| `summary-vectors.json` | Summary and classification cases (§6), each with its definition. |
| `preflight-vectors.json` | Capability preflight cases (§7): ranges, profile maximum, input definition, target evaluations (path, kind, requested, normalised, disposition, reason) and the normalised definition. |
| `progression-vectors.json` | Segment-cursor traces (§9.1): operations and the resulting state after each one. |
| `native-import-vectors.json`, `imports/native/*.json` | Native JSON import cases ([03](03-import-export-formats.md) §2). |

Durations in the vectors are given in **seconds** for readability. Definitions use `durationTicks`. Float comparisons in tests should use an absolute tolerance of 1e-9, except where a rule says exact.

---

## 12. Test checklist (Kotlin implementation must pass)

**Model and validation:**
- [ ] Every case in `validation-vectors.json` gives the same accept/reject decision.
- [ ] Exactly 10,000 expanded steps is accepted and 10,001 is rejected. A repeat of 2,147,483,647 is rejected without overflow.
- [ ] 31 nested repeats are accepted and 32 are rejected.
- [ ] Exactly 12 h is accepted and 12 h + 1 tick is rejected. A 12 h time part plus a distance step is accepted.
- [ ] Title and cue are trimmed; a blank description or notes becomes null; the whitespace set is .NET's (U+0085 and U+00A0 are whitespace; U+001C–U+001F are not).
- [ ] `-0.0` speed or incline is stored as `0`, and the content hash equals the `+0` variant.
- [ ] NaN or infinite values are rejected everywhere. Negative speeds are rejected; negative inclines are accepted.
- [ ] HR: 1 ≤ min ≤ max ≤ 250 bpm; `min ≤ initial ≤ max` km/h; zone 1..10; all-zero HR speeds are accepted.
- [ ] `knownDuration` is null when any expanded step has a distance goal.

**Serialization and revisions:**
- [ ] The canonical form (§3.2) is deterministic: serialise → parse → serialise gives identical bytes; equal definitions give equal hashes; a changed title changes the hash.
- [ ] All `definitions/*.json` samples parse, and re-serialising them preserves their meaning (field by field).
- [ ] Revisions are immutable: update or delete attempts fail.
- [ ] Appending identical content (same hash) creates no new revision. Appending different content increments the revision number and updates the workout name.
- [ ] A replayed operation ID returns the same result; a reused ID with a different request is a conflict.
- [ ] Archiving hides the workout from the library, pickers and "Run again", and its revisions stay resolvable.

**Kinds and library:**
- [ ] `PlanInternal` workouts, template-linked workouts (legacy `Structured` rows referenced by a template plan) and `ManualTemplate` workouts never appear in the library.
- [ ] Search matches the name, description and all four labels, case-insensitively.
- [ ] The duration filters exclude unknown durations (except `distance`), and the structure filter `intervals` matches `HR intervals`.
- [ ] Sort `updated`, `title` and `duration` (unknown last, then name) behave as specified.

**Expansion and summary:**
- [ ] `expandedSteps` for every entry of `definition-examples.json` matches: order, source path, goal, targets, cue and notes, and start seconds (null after the first distance step).
- [ ] Every summary in `definition-examples.json` and `summary-vectors.json` matches (structure, goal, speed and incline labels, `usesHeartRate`, `expandedStepCount`, `durationMinutes`).
- [ ] The `0.##` formatting gives `6.46` for 6.456, `1.01` for 1.005 and `0.33` for 20 s in minutes.

**Preflight:**
- [ ] Every case in `preflight-vectors.json` matches: validity, target order and paths, dispositions, normalised values, and the normalised definition.
- [ ] Out-of-range values are rejected, never clamped. In-range values are only aligned down.
- [ ] The decimal conversion aligns 7.6 with increment 0.1 from 0.8 to exactly 7.6, not 7.5.
- [ ] A profile maximum below the treadmill minimum is rejected even for an open-speed workout.

**Run-time consumption:**
- [ ] Every trace in `progression-vectors.json` matches (transitions with exact completion instants, index, progress, planned values, HR target, remaining duration, elapsed since restart).
- [ ] Time and distance going backwards are rejected. Restore rejects invalid checkpoints.
- [ ] A fixed speed or incline is applied once per segment; a later console change is not undone within the segment.
- [ ] A manual override persists until the segment changes, then it is cleared and the next segment's fixed targets are applied.
- [ ] Ramps are commanded continuously when the deviation exceeds `max(0.15, increment / 2)`.
- [ ] Open speed produces no speed command.
- [ ] HR steps use the controller with bounds clamped to the treadmill range and the profile maximum.
- [ ] Pause freezes the cursor. Reset progress requires paused and stopped, and restarts at step 0.
- [ ] Manual run: the first use creates exactly one `ManualTemplate` (240 min, fixed 0.8, incline 0); later uses reuse it.

**Editor and drafts:**
- [ ] Minutes are converted to ticks by truncation (20 s → 200,000,000; 90 s → 900,000,000).
- [ ] The `mm:ss` parser accepts 1–43,200 s and rejects seconds ≥ 60 and non-digits.
- [ ] Drafts are saved after 450 ms of inactivity and keyed per runner and workout; oversized (> 256 KiB) or stale (> 30 days) drafts are discarded; the draft is removed after a successful save.
