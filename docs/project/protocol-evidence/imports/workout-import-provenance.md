---
title: Workout Import Provenance and Supported Mappings
type: protocol-evidence
status: reviewed
owner: project
audience: agent-and-developer
updated: 2026-08-02
---

# Workout import provenance

This record covers only workout-file interpretation. It does not provide evidence for Bluetooth control, treadmill capabilities, or remote commands. In particular, a QDomyos `forcespeed` value is never translated into a TreadmillRunner device capability.

## Native JSON

- Schema identifier: `treadmillrunner.workout/v1`.
- Implementation: independently defined TreadmillRunner format using `System.Text.Json` with a maximum depth and the shared 10 MB, 10,000-expanded-step, and 12-hour limits.
- Unknown fields produce preview warnings. Unknown schemas and block discriminators fail closed.
- Numeric units are canonical metric values: km/h, metres, percent incline, bpm, and seconds.

## QDomyos treadmill XML

The importer was independently authored from declarative examples and format documentation only. QDomyos implementation code was not copied or translated.

Evidence snapshot:

- External reference commit: `99f27b2cb5360ce925c19c40d5a4ddff29ef4057`.
- Reviewed evidence files: `train-programs-examples/README.md`, `train-programs-examples/hrzones_example.xml`, `train-programs-examples/hrzones_1h_treadmill.xml`, and `train-programs-examples/calorie-barbara.xml` in the sibling external reference.
- Mapped elements/fields: `rows`, `row`, `repeat times`, `duration`, `distance`, `speed`, `speedfrom`, `speedto`, `inclination`, `zonehr`, `hrmin`, `hrmax`, `minspeed`, `maxspeed`, and nested `textevent message`.
- Security boundary: DTDs and entity declarations are prohibited, the XML resolver is disabled, input bytes and XML characters are bounded, repeat nesting is bounded, and expansion is validated by the Core workout model.
- Unit limitation: QDomyos speed units depend on external application settings and are not reliably self-described by the XML. Imports explicitly warn that km/h was assumed. Distance is interpreted as kilometres according to the reviewed format guide.
- Unsupported bike-oriented fields such as resistance, power, cadence, Peloton resistance, and fan speed are ignored with visible warnings. Unknown elements and attributes are also warned.
- `looptimehr` is ignored because TreadmillRunner records and applies its own safety controller settings.
- `forcespeed` produces a warning and is ignored for capability purposes.
- A row combining fixed/ramp speed and an HR target retains the explicit speed and warns that HR automation was not enabled for that contradictory row.
- HR rows without an explicit initial speed conservatively use the declared minimum speed. Missing bounds become zero/minimum and must be edited before execution; both cases produce warnings.

## Garmin FIT Workout

The importer uses the official `Garmin.FIT.Sdk` NuGet package owned by Garmin, pinned to version `21.205.0` with lock-file content hash `jjpY3rO4M9/jduX6k5LSO7ACK/A5Yi0Mwh6m7wnksNkE2WEuc8puzNCT6l/2qScz17daR8eNdN7IFoseFMIqVQ==`.

Primary specifications:

- [Garmin Workout file type](https://developer.garmin.com/fit/file-types/workout/)
- [Garmin workout encoding cookbook](https://developer.garmin.com/fit/cookbook/encoding-workout-files/)
- [Garmin FIT SDK](https://developer.garmin.com/fit/get-the-sdk/)

The package includes Garmin's FIT Protocol License Agreement. The project remains private; any redistribution or licensing change requires a deliberate license review.

Supported FIT mappings:

| FIT field | Native mapping |
|---|---|
| Workout name/description | Workout title/description |
| Time, time-only, repetition-time duration | Time goal |
| Distance duration | Distance goal |
| Exact custom speed where low equals high | Fixed speed, converted from m/s to km/h |
| HR predefined zone 1-10 | HR-zone speed with zero speed bounds and mandatory review warning |
| Absolute custom HR range | Explicit HR range after applying FIT's +100 bpm encoding offset, with zero speed bounds and warning |
| Repeat-until-steps-complete | Finite repeat block referencing the prior message index |
| Step name/notes | Cue/notes |

Explicitly unsupported FIT mappings:

| FIT construct | Behavior |
|---|---|
| Speed target range | Left as open speed with warning; a target range is not misrepresented as a ramp or midpoint |
| Predefined speed zone/incomplete custom speed | Left as open speed with warning |
| Percent-of-maximum custom HR | Left as open speed with warning; it is not guessed into a profile zone |
| Power, cadence, grade, resistance, 3s/10s/30s/lap targets, and swim stroke | Left as open speed with target-specific warning |
| Secondary targets | Ignored with warning |
| Calories, open/lap-button, HR threshold, power threshold, repetition-count, TrainingPeaks TSS, and other non-time/distance end conditions | Step skipped with warning |
| Conditional repeat-until time, distance, calorie, HR, power, lap-power, or TSS | Repeat marker skipped with warning; preceding steps remain single-pass |
| Non-running workout sport | Treadmill-relevant fields are parsed with a non-running-sport warning |

FIT imports require a valid FIT header, CRC, File Id type `Workout`, a Workout message, unique indexed Workout Step messages, and at least one representable time or distance step.

## Deterministic fixtures

- Native JSON and QDomyos XML fixtures are inline, synthetic, and contain no user or device data.
- FIT fixtures are generated in memory using the pinned official SDK, then decoded by the importer. Corrupt CRC and non-FIT cases are also covered.
- The focused verifier is:

```powershell
dotnet test tests\TreadmillRunner.Protocols.Tests\TreadmillRunner.Protocols.Tests.csproj --configuration Release
```
