using Dynastream.Fit;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Protocols.Tests;

public sealed class GarminFitWorkoutImporterTests
{
  private readonly GarminFitWorkoutImporter importer = new();

  [Fact]
  public async Task Imports_running_time_distance_speed_hr_zone_and_repeat()
  {
    byte[] fit = EncodeWorkout(
        "Intervals",
        Sport.Running,
        step =>
        {
          step.SetMessageIndex(0);
          step.SetWktStepName("Warm up");
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(300);
          step.SetTargetType(WktStepTarget.Speed);
          step.SetCustomTargetSpeedLow(2.5f);
          step.SetCustomTargetSpeedHigh(2.5f);
        },
        step =>
        {
          step.SetMessageIndex(1);
          step.SetWktStepName("Zone work");
          step.SetDurationType(WktStepDuration.Distance);
          step.SetDurationDistance(400);
          step.SetTargetType(WktStepTarget.HeartRate);
          step.SetTargetHrZone(3);
        },
        step =>
        {
          step.SetMessageIndex(2);
          step.SetDurationType(WktStepDuration.RepeatUntilStepsCmplt);
          step.SetDurationStep(0);
          step.SetTargetType(WktStepTarget.Open);
          step.SetRepeatSteps(3);
        });

    WorkoutImportResult result = await ImportAsync(fit);

    Assert.Equal("Intervals", result.Definition.Title);
    Assert.Equal(6, result.ExpandedStepCount);
    Assert.Null(result.TotalDuration);
    WorkoutRepeat repeat = Assert.IsType<WorkoutRepeat>(Assert.Single(result.Definition.Blocks));
    Assert.Equal(3, repeat.Repetitions);
    WorkoutStep warmup = Assert.IsType<WorkoutStep>(repeat.Blocks[0]);
    Assert.Equal(9, Assert.IsType<FixedSpeed>(warmup.Speed).KilometersPerHour, 6);
    WorkoutStep hr = Assert.IsType<WorkoutStep>(repeat.Blocks[1]);
    Assert.Equal(0.4, Assert.IsType<DistanceGoal>(hr.Goal).Kilometers, 6);
    Assert.Equal(3, Assert.IsType<HeartRateZoneSpeed>(hr.Speed).ZoneNumber);
    Assert.Contains(result.Warnings, warning => warning.Code == "fit.hr-speed-bounds-required");
  }

  [Fact]
  public async Task Maps_absolute_hr_and_warns_for_unsupported_targets()
  {
    byte[] fit = EncodeWorkout(
        "Mixed targets",
        Sport.Cycling,
        step =>
        {
          step.SetMessageIndex(0);
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(60);
          step.SetTargetType(WktStepTarget.HeartRate);
          step.SetCustomTargetHeartRateLow(225);
          step.SetCustomTargetHeartRateHigh(240);
        },
        step =>
        {
          step.SetMessageIndex(1);
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(60);
          step.SetTargetType(WktStepTarget.Power);
          step.SetTargetPowerZone(3);
          step.SetSecondaryTargetType(WktStepTarget.Cadence);
        });

    WorkoutImportResult result = await ImportAsync(fit);

    HeartRateSpeed heartRate = Assert.IsType<HeartRateSpeed>(Assert.IsType<WorkoutStep>(result.Definition.Blocks[0]).Speed);
    Assert.Equal((ushort)125, heartRate.MinimumBpm);
    Assert.Equal((ushort)140, heartRate.MaximumBpm);
    Assert.IsType<OpenSpeed>(Assert.IsType<WorkoutStep>(result.Definition.Blocks[1]).Speed);
    Assert.Contains(result.Warnings, warning => warning.Code == "fit.non-running-sport");
    Assert.Contains(result.Warnings, warning => warning.Code == "fit.target-unsupported");
    Assert.Contains(result.Warnings, warning => warning.Code == "fit.secondary-target-unsupported");
  }

  [Fact]
  public async Task Skips_unsupported_duration_with_visible_warning()
  {
    byte[] fit = EncodeWorkout(
        "Partial",
        Sport.Running,
        step =>
        {
          step.SetMessageIndex(0);
          step.SetDurationType(WktStepDuration.Calories);
          step.SetDurationCalories(50);
          step.SetTargetType(WktStepTarget.Open);
        },
        step =>
        {
          step.SetMessageIndex(1);
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(30);
          step.SetTargetType(WktStepTarget.Open);
        });

    WorkoutImportResult result = await ImportAsync(fit);

    Assert.Single(result.Definition.Blocks);
    Assert.Contains(result.Warnings, warning => warning.Code == "fit.duration-unsupported");
  }

  [Fact]
  public async Task Rejects_corrupt_and_non_fit_files()
  {
    await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync([1, 2, 3, 4, 5]));

    byte[] valid = EncodeWorkout(
        "Corrupt",
        Sport.Running,
        step =>
        {
          step.SetMessageIndex(0);
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(60);
          step.SetTargetType(WktStepTarget.Open);
        });
    valid[^1] ^= 0xFF;

    WorkoutImportException error = await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(valid));
    Assert.Contains("CRC", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Rejects_ambiguous_duplicate_workout_messages()
  {
    byte[] fit = EncodeWorkout(
        "Ambiguous",
        Sport.Running,
        true,
        step =>
        {
          step.SetMessageIndex(0);
          step.SetDurationType(WktStepDuration.Time);
          step.SetDurationTime(60);
          step.SetTargetType(WktStepTarget.Open);
        });

    WorkoutImportException error = await Assert.ThrowsAsync<WorkoutImportException>(() => ImportAsync(fit));

    Assert.Contains("Exactly one FIT Workout", error.Message, StringComparison.Ordinal);
  }

  private async Task<WorkoutImportResult> ImportAsync(byte[] bytes)
  {
    await using MemoryStream stream = new(bytes, writable: false);
    return await importer.ImportAsync(stream, "fixture.fit");
  }

  private static byte[] EncodeWorkout(
      string title,
      Sport sport,
      params Action<WorkoutStepMesg>[] configureSteps) =>
    EncodeWorkout(title, sport, false, configureSteps);

  private static byte[] EncodeWorkout(
      string title,
      Sport sport,
      bool includeDuplicateWorkout,
      params Action<WorkoutStepMesg>[] configureSteps)
  {
    using MemoryStream stream = new();
    Encode encoder = new(stream, ProtocolVersion.V20);

    FileIdMesg fileId = new();
    fileId.SetType(Dynastream.Fit.File.Workout);
    fileId.SetManufacturer((ushort)Manufacturer.Development);
    fileId.SetProduct(1);
    fileId.SetSerialNumber(1);
    encoder.Write(fileId);

    WorkoutMesg workout = new();
    workout.SetWktName(title);
    workout.SetSport(sport);
    workout.SetNumValidSteps((ushort)configureSteps.Length);
    encoder.Write(workout);
    if (includeDuplicateWorkout)
    {
      WorkoutMesg duplicate = new();
      duplicate.SetWktName("Different workout");
      duplicate.SetSport(sport);
      duplicate.SetNumValidSteps((ushort)configureSteps.Length);
      encoder.Write(duplicate);
    }

    foreach (Action<WorkoutStepMesg> configure in configureSteps)
    {
      WorkoutStepMesg step = new();
      configure(step);
      encoder.Write(step);
    }

    encoder.Close();
    return stream.ToArray();
  }
}
