# Premade plan catalog data

This folder holds the complete premade training-plan catalog as data: all 16 templates, every session, every alternative and every workout definition. The Kotlin app ships these files as resources, or tests its own generator against them byte for byte. The rules for installing and running the plans are in [04-calendar-and-plans.md](../../04-calendar-and-plans.md), section 7.

The files were produced by running the current app's catalog code and serialising what it builds. Nothing was edited by hand.

| File | Content |
|---|---|
| `catalog.json` | Index of the 16 templates, in catalog display order, with metadata and hashes |
| `templates/<templateId>.json` | One file per template: header, phases, and every session with its full workout definitions |
| `walkingpad-5k-to-10k-source.json` | The verbatim normalised source payload of the 58-week plan (174 slots, 260 variants, raw rows including the legacy stop tail). Its SHA-256 is the plan's `sourceContentSha256`. |

## Counts to check on load

| Template | Version | Sessions | Variants | Unique definitions | HR zones referenced |
|---|---|---|---|---|---|
| getting-started | 1.0.0 | 12 | 12 | 12 | – |
| first-5k | 1.0.0 | 18 | 18 | 18 | – |
| beginner-5k-standard | 1.0.0 | 27 | 27 | 27 | – |
| beginner-5k-progressive | 1.0.0 | 30 | 30 | 30 | – |
| beginner-5k-gentle | 1.0.0 | 42 | 42 | 42 | – |
| heart-rate-5k-gentle | 1.0.0 | 36 | 36 | 36 | 2, 3 |
| 5k-performance | 1.0.0 | 32 | 32 | 32 | – |
| first-10k | 1.0.0 | 18 | 18 | 18 | – |
| 10k-builder-gentle | 1.0.0 | 42 | 42 | 42 | – |
| heart-rate-10k-gentle | 1.0.0 | 42 | 42 | 42 | 2, 3 |
| 10k-performance | 1.0.0 | 32 | 32 | **30** | – |
| 5k-to-10k-distance-first-58 | **2.0.0** | **174** | **260** | 260 | 1, 2, 3 |
| general-treadmill-fitness | 1.0.0 | 18 | 18 | 18 | – |
| 5k-maintenance | 1.0.0 | 12 | 12 | 12 | – |
| 10k-maintenance | 1.0.0 | 12 | 12 | 12 | – |
| walking-and-recovery | 1.0.0 | 12 | 12 | 12 | – |

The raw WalkingPad payload is 357,025 bytes. Its SHA-256 is `c476161e23a94242c8172dffe3b42fc2efb559b1232753bca7fbe297529bdff3`.

## `catalog.json`

```json
{
  "schema": "treadmillrunner.premade-catalog/1",
  "catalogVersion": "1.0.0",
  "templateCount": 16,
  "templates": [ <TemplateHeader>, ... ]
}
```

`catalogVersion` is the version shared by the 15 generated templates. The WalkingPad plan has its own version, `2.0.0`.

`TemplateHeader` has the same fields as the top of a template file (below), plus `file`: the path of the template file relative to this folder.

## `templates/<id>.json`

| Field | Type | Meaning |
|---|---|---|
| `schema` | string | Always `treadmillrunner.premade-template/1` |
| `id` | string | Stable template ID (≤ 100 chars). Stored on the installed program as `templateId`. |
| `version` | string | Template version (`major.minor.patch`, ≤ 40 chars). Installation is idempotent per runner + `id` + `version`. |
| `name` | string | Display name. It also becomes the installed program's name. |
| `description` | string | Catalog description. It also becomes the installed program's description. |
| `goal` | string | One of `5K`, `10K`, `General fitness`, `Walking`. It becomes the installed program's `category`. |
| `experience` | string | One of `Beginner`, `Intermediate`, `Experienced`, `All levels` |
| `weeks` | int | Number of weeks |
| `sessionsPerWeek` | int | Sessions per week. This is also the number of weekdays a runner must pick when scheduling. |
| `repeatable` | bool | True for the maintenance/recovery cycles. This is informational only: nothing auto-restarts a plan. |
| `requiresHeartRate` | bool | When true, installation is blocked unless the runner has every referenced zone |
| `tags` | string[] | Sorted ordinal. It includes the goal slug (`5k`, `10k`, `general-fitness`, `walking`). |
| `sessionCount` | int | Number of ordered positions (= `weeks × sessionsPerWeek`) |
| `variantCount` | int | `sessionCount` + total number of alternatives |
| `uniqueDefinitionCount` | int | Distinct canonical definition hashes across primaries and alternatives. This is how many workout revisions installation creates before per-runner normalisation. |
| `heartRateZoneReferences` | int[] | Zone numbers used by `heartRateZone` speed directives. Personal BPM values never appear in any definition. |
| `maximumDurationMinutes` | int | Max of the sessions' `durationMinutes` |
| `maximumSpeedKph` | number | Max of the sessions' `targetSpeedKph` |
| `maximumInclinePercent` | number | Max of the sessions' `targetInclinePercent` |
| `sourceContentSha256` | string or null | Only for the WalkingPad plan: SHA-256 of `walkingpad-5k-to-10k-source.json` |
| `contentSha256` | string | Template content hash (lower-case hex). The formula is in 04, section 7.4. It is stored on the installation record. |
| `phases` | Phase[] | Phases in first-appearance order: `{ name, firstWeek, lastWeek, sessionCount }` |
| `sessions` | Session[] | Ordered by `position` (1..sessionCount, contiguous) |

A `Session` has these fields:

| Field | Type | Meaning |
|---|---|---|
| `position` | int | 1-based order. It becomes the program item `position`. |
| `weekNumber` | int | 1-based week. It becomes the program item `weekNumber`. |
| `sessionNumber` | int | 1-based session within the week. It becomes the program item `sessionNumber`. |
| `phase` | string | Phase label (≤ 80 chars). It becomes the program item `phase`. |
| `workoutKey` | string | Template-local key of the primary workout. For generated plans it looks like `long-26-5.0-1.0-fixed`. For WalkingPad it is the source variant ID, like `W01D1`. |
| `workoutName` | string | Primary workout name (equal to the definition title for generated plans) |
| `durationMinutes` | int | Nominal duration. For WalkingPad this is the ceiling of the primary's summed step minutes after the stop tail is removed. |
| `targetSpeedKph` | number | Nominal main speed. For WalkingPad this is the max row speed of the primary. |
| `targetInclinePercent` | number | Nominal main incline. For WalkingPad this is the max row incline of the primary. |
| `heartRateZoneNumber` | int or null | Zone of the main step for generated HR templates. Always null for WalkingPad: its zones live inside the definitions. |
| `definitionSha256` | string | Lower-case hex SHA-256 of the **canonical JSON** of `definition` (schema and writer: 02-workouts.md) |
| `definition` | object | Workout definition, schema v1 |
| `alternatives` | Alternative[] | Zero or more alternatives for the same slot |

An `Alternative` has `displayOrder` (1-based; the primary is implicitly 0), `workoutKey`, `variant` (`hr-alternative` or `fixed-fallback`, ≤ 40 chars), `workoutName`, `definitionSha256` and `definition`.

### Workout definition objects

These are workout schema v1 objects exactly as the canonical writer emits them:

- `schemaVersion`, `title`, `description`, `blocks`;
- each block is `{ kind: "step", goal, speed, incline, cue, notes }`;
- `goal` is `{ kind: "time", durationTicks }`, where one tick is 100 ns, so 1 minute = 600,000,000 ticks;
- `speed` is `{ kind: "fixed", kilometersPerHour }` or `{ kind: "heartRateZone", zoneNumber, initialKilometersPerHour, minimumKilometersPerHour, maximumKilometersPerHour }`;
- `incline` is `{ kind: "fixed", percent }`.

No catalog definition uses `repeat`, `distance`, `ramp`, `open` or BPM-range `heartRate` directives.

These files are pretty-printed and write non-ASCII characters such as `·` (U+00B7) literally. The **canonical** form is compact and escapes every non-ASCII character as `\uXXXX` with upper-case hex; for example, the title `W01D1 · …` is written `W01D1 · …`. To reproduce `definitionSha256`, parse the object and re-serialise it with the canonical writer. Do not hash the pretty text. Numbers such as `8` and `4.5` are written in shortest round-trip form, and integer-valued doubles have no `.0`.

## `walkingpad-5k-to-10k-source.json`

This is a compact UTF-8 JSON array of 174 slots, ordered by week and then session:

```json
[{ "slot": "W01D1", "week": 1, "session": 1,
   "variants": [{ "id": "W01D1", "variant": "primary", "title": "…", "selectionRule": "Default schedule choice",
                  "rows": [{ "durationSeconds": 300, "speed": 4.5, "incline": 0.0, "forceSpeed": true,
                             "zone": 0, "heartRateMinimum": 0, "heartRateMaximum": 0,
                             "minimumSpeed": 0.0, "maximumSpeed": 0.0 }, …] }, …] }, …]
```

- `variant` is one of these: `primary` (174), `hr-alternative` (65, slots W11D1…W57D3) or `fixed-fallback` (21, slots W23/W28/W33/W38/W43/W48/W53 D1–D3, where the primary is HR-guided).
- The `selectionRule` of an alternative is always `Use instead of <slot>; never perform both variants`.
- Every variant still ends with the legacy two-row stop tail: 60 s at 1.0 km/h, then a row at 0 km/h. The materialiser removes it.
- Rows with `forceSpeed: false` always carry `zone` 1–3, `minimumSpeed: 4.0` and `maximumSpeed: 10.0`. No row carries BPM values.

The rules that turn these rows into the definitions in `templates/5k-to-10k-distance-first-58.json` are in 04, section 7.3. A Kotlin test must rebuild the template from this file and match every `definitionSha256`.

## Provenance and licensing

- The 15 generated templates were authored independently for TreadmillRunner. They are parametric; the generator is specified in 04, section 7.2.
- The 58-week plan is a deterministic, sanitised derivative of an owner-provided WalkingPad/QDomyos source snapshot: a `workout_index.csv` plus one QDomyos v4 XML workout per indexed row. Personal weight, BPM values, sensor IDs, gait preferences, machine paths and account data were not copied. The private source files are not redistributed; only this normalised derivative is.
- None of the plans is an official export from Horizon, Garmin, QDomyos, WalkingPad or any other provider. They make no medical or rehabilitation claims.

Full provenance and regeneration notes are in 04, section 7.6.
