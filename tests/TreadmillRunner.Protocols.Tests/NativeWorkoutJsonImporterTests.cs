using System.Text;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class NativeWorkoutJsonImporterTests
{
  private readonly NativeWorkoutJsonImporter importer = new();

  [Fact]
  public async Task Imports_time_distance_hr_ramp_and_repeat_blocks()
  {
    const string json = """
        {
          "schema": "treadmillrunner.workout/v1",
          "name": "Progression",
          "description": "A safe fixture",
          "blocks": [
            {
              "type": "step",
              "name": "Warm up",
              "durationSeconds": 300,
              "speedStartKph": 5.0,
              "speedEndKph": 7.0,
              "inclineStartPercent": 1.0,
              "cue": "Relax"
            },
            {
              "type": "repeat",
              "count": 3,
              "blocks": [
                {
                  "type": "step",
                  "distanceMeters": 400,
                  "heartRateZone": 3,
                  "minimumSpeedKph": 7.0,
                  "maximumSpeedKph": 10.0
                }
              ]
            }
          ]
        }
        """;

    WorkoutImportResult result = await ImportAsync(json);

    Assert.Equal(WorkoutImportFormat.NativeJson, result.Format);
    Assert.Equal("Progression", result.Definition.Title);
    Assert.Equal(4, result.ExpandedStepCount);
    Assert.Null(result.TotalDuration);
    WorkoutStep warmup = Assert.IsType<WorkoutStep>(result.Definition.Blocks[0]);
    SpeedRamp ramp = Assert.IsType<SpeedRamp>(warmup.Speed);
    Assert.Equal(5, ramp.StartKilometersPerHour);
    Assert.Equal(7, ramp.EndKilometersPerHour);
    WorkoutRepeat repeat = Assert.IsType<WorkoutRepeat>(result.Definition.Blocks[1]);
    Assert.Equal(3, repeat.Repetitions);
    HeartRateZoneSpeed zone = Assert.IsType<HeartRateZoneSpeed>(Assert.IsType<WorkoutStep>(repeat.Blocks[0]).Speed);
    Assert.Equal(3, zone.ZoneNumber);
  }

  [Fact]
  public async Task Rejects_unknown_schema_and_corrupt_json()
  {
    WorkoutImportException schemaError = await Assert.ThrowsAsync<WorkoutImportException>(
        () => ImportAsync("""{"schema":"future/v2","name":"x","blocks":[]}"""));
    Assert.Contains("Unsupported", schemaError.Message, StringComparison.Ordinal);

    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync("{"));
  }

  [Fact]
  public async Task Rejects_excessive_repeat_expansion_and_duration()
  {
    const string tooMany = """
        {"schema":"treadmillrunner.workout/v1","name":"x","blocks":[
          {"type":"repeat","count":10000,"blocks":[
            {"type":"repeat","count":2,"blocks":[{"type":"step","durationSeconds":1}]}
          ]}
        ]}
        """;
    const string tooLong = """
        {"schema":"treadmillrunner.workout/v1","name":"x","blocks":[
          {"type":"step","durationSeconds":43201}
        ]}
        """;

    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(tooMany));
    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(tooLong));
  }

  [Fact]
  public async Task Warns_when_unknown_fields_are_ignored()
  {
    const string json = """
        {"schema":"treadmillrunner.workout/v1","name":"x","future":true,"blocks":[
          {"type":"step","durationSeconds":60,"futureStep":42}
        ]}
        """;

    WorkoutImportResult result = await ImportAsync(json);

    Assert.Equal(2, result.Warnings.Count(warning => warning.Code == "native.unknown-field"));
  }

  [Fact]
  public async Task Imports_core_canonical_json_for_round_trip()
  {
    WorkoutDefinition original = new(
        1,
        "Canonical",
        "Round trip",
        [
          new WorkoutStep(
              new TimeGoal(TimeSpan.FromMinutes(2)),
              new FixedSpeed(8.5),
              new InclineRamp(0, 2),
              "Build"),
        ]);
    string json = WorkoutDefinitionCanonicalizer.Serialize(original);

    WorkoutImportResult result = await ImportAsync(json);

    Assert.Equal(
        WorkoutDefinitionCanonicalizer.ComputeSha256(original),
        WorkoutDefinitionCanonicalizer.ComputeSha256(result.Definition));
  }

  [Fact]
  public async Task Canonical_json_warns_when_nested_fields_are_ignored()
  {
    const string json = """
        {"schemaVersion":1,"title":"Future fields","description":null,"blocks":[
          {"kind":"step",
           "goal":{"kind":"time","durationTicks":600000000,"futureGoal":1},
           "speed":{"kind":"fixed","kilometersPerHour":8.0,"futureSpeed":2},
           "incline":{"kind":"fixed","percent":1.0,"futureIncline":3},
           "cue":null,"notes":null}
        ]}
        """;

    WorkoutImportResult result = await ImportAsync(json);

    Assert.Collection(
        result.Warnings.Where(warning => warning.Code == "native.unknown-field"),
        warning => Assert.Contains("futureGoal", warning.Message, StringComparison.Ordinal),
        warning => Assert.Contains("futureSpeed", warning.Message, StringComparison.Ordinal),
        warning => Assert.Contains("futureIncline", warning.Message, StringComparison.Ordinal));
  }

  private async Task<WorkoutImportResult> ImportAsync(string text)
  {
    await using MemoryStream stream = new(Encoding.UTF8.GetBytes(text));
    return await importer.ImportAsync(stream, "workout.json");
  }
}
