using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class WorkoutDefinitionTests
{
  [Fact]
  public void Definition_supports_time_distance_hr_ramps_repeats_and_cues()
  {
    var definition = new WorkoutDefinition(
        1,
        "Intervals",
        "Mixed targets",
        [
          new WorkoutStep(
              new TimeGoal(TimeSpan.FromMinutes(5)),
              new SpeedRamp(8, 10),
              new FixedIncline(1),
              "Warm up",
              null),
          new WorkoutRepeat(2,
          [
            new WorkoutStep(
                new DistanceGoal(0.4),
                new HeartRateSpeed(145, 155, 10, 8, 12),
                new InclineRamp(1, 3),
                "Work",
                "Hold form")
          ])
        ]);

    Assert.Equal(3, definition.ExpandedStepCount);
    Assert.Null(definition.KnownDuration);
  }

  [Fact]
  public void Definition_supports_a_profile_heart_rate_zone_target()
  {
    var definition = new WorkoutDefinition(
        1,
        "Zone two",
        null,
        [
          new WorkoutStep(
              new TimeGoal(TimeSpan.FromMinutes(30)),
              new HeartRateZoneSpeed(2, 9, 7, 11),
              new FixedIncline(1))
        ]);

    var json = WorkoutDefinitionCanonicalizer.Serialize(definition);

    Assert.Contains("\"kind\":\"heartRateZone\"", json, StringComparison.Ordinal);
    Assert.Contains("\"zoneNumber\":2", json, StringComparison.Ordinal);
    Assert.Equal(TimeSpan.FromMinutes(30), definition.KnownDuration);
  }

  [Theory]
  [InlineData(0)]
  [InlineData(11)]
  public void Profile_heart_rate_zone_target_rejects_invalid_zone(int zoneNumber)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new HeartRateZoneSpeed(zoneNumber, 9, 7, 11));
  }

  [Fact]
  public void Definition_supports_an_open_speed_directive_without_guessing_a_target()
  {
    var definition = new WorkoutDefinition(
        1,
        "Incline only",
        null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(10)), new OpenSpeed(), new InclineRamp(0, 4))]);

    var json = WorkoutDefinitionCanonicalizer.Serialize(definition);

    Assert.Contains("\"speed\":{\"kind\":\"open\"}", json, StringComparison.Ordinal);
  }

  [Fact]
  public void Definition_rejects_more_than_twelve_hours()
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutDefinition(
        1,
        "Too long",
        null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromHours(12) + TimeSpan.FromSeconds(1)), new FixedSpeed(8), new FixedIncline(0))]));
  }

  [Fact]
  public void Definition_rejects_more_than_ten_thousand_expanded_steps()
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutDefinition(
        1,
        "Too many",
        null,
        [new WorkoutRepeat(10_001, [new WorkoutStep(new DistanceGoal(0.1), new FixedSpeed(8), new FixedIncline(0))])]));
  }

  [Theory]
  [InlineData(double.NaN)]
  [InlineData(double.PositiveInfinity)]
  public void Definition_rejects_non_finite_values(double speed)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutDefinition(
        1,
        "Invalid",
        null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(speed), new FixedIncline(0))]));
  }

  [Fact]
  public void Canonical_json_and_hash_are_deterministic_and_semantic()
  {
    var first = CreateSimpleDefinition("Easy");
    var equivalent = CreateSimpleDefinition("Easy");
    var changed = CreateSimpleDefinition("Steady");

    var json = WorkoutDefinitionCanonicalizer.Serialize(first);

    Assert.Equal(json, WorkoutDefinitionCanonicalizer.Serialize(equivalent));
    Assert.Equal(WorkoutDefinitionCanonicalizer.ComputeSha256(first), WorkoutDefinitionCanonicalizer.ComputeSha256(equivalent));
    Assert.NotEqual(WorkoutDefinitionCanonicalizer.ComputeSha256(first), WorkoutDefinitionCanonicalizer.ComputeSha256(changed));
    Assert.StartsWith("{\"schemaVersion\":1,\"title\":\"Easy\"", json, StringComparison.Ordinal);
    Assert.Equal(64, WorkoutDefinitionCanonicalizer.ComputeSha256(first).Length);
  }

  [Fact]
  public void Canonical_hash_normalizes_negative_zero()
  {
    var positiveZero = new WorkoutDefinition(
        1,
        "Zero",
        null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(0), new FixedIncline(0))]);
    var negativeZero = new WorkoutDefinition(
        1,
        "Zero",
        null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(-0d), new FixedIncline(-0d))]);

    Assert.Equal(
        WorkoutDefinitionCanonicalizer.ComputeSha256(positiveZero),
        WorkoutDefinitionCanonicalizer.ComputeSha256(negativeZero));
  }

  [Fact]
  public void Huge_repeat_fails_with_the_documented_limit_instead_of_overflowing()
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutDefinition(
        1,
        "Huge",
        null,
        [new WorkoutRepeat(int.MaxValue, [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), new OpenSpeed(), new FixedIncline(0))])]));
  }

  private static WorkoutDefinition CreateSimpleDefinition(string title) => new(
      1,
      title,
      null,
      [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(30)), new FixedSpeed(9), new FixedIncline(1), "Begin", null)]);
}
