using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class WorkoutCapabilityPolicyTests
{
  private static readonly TreadmillOperatingRange Speed = new(0.8m, 20m, 0.1m, TreadmillCapabilityEvidence.HardwareVerified);
  private static readonly TreadmillOperatingRange Incline = new(0m, 12m, 0.1m, TreadmillCapabilityEvidence.HardwareVerified);

  [Fact]
  public void Evaluate_aligns_down_without_increasing_intensity()
  {
    var workout = Workout(new FixedSpeed(7.56), new FixedIncline(1.06));

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, Speed, Incline, 12);

    Assert.True(result.IsValid);
    Assert.All(result.Targets, target => Assert.Equal(WorkoutTargetDisposition.Normalized, target.Disposition));
    var step = Assert.IsType<WorkoutStep>(Assert.Single(result.Definition.Blocks));
    Assert.Equal(7.5, Assert.IsType<FixedSpeed>(step.Speed).KilometersPerHour, 6);
    Assert.Equal(1.0, Assert.IsType<FixedIncline>(step.Incline).Percent, 6);
  }

  [Fact]
  public void Evaluate_rejects_profile_and_hardware_outliers_without_clamping_them()
  {
    var workout = Workout(new SpeedRamp(7, 13), new InclineRamp(1, 13));

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, Speed, Incline, 12);

    Assert.False(result.IsValid);
    Assert.Equal(2, result.Rejected.Count);
    Assert.Contains(result.Rejected, target => target.Path.EndsWith("speed.end", StringComparison.Ordinal));
    Assert.Contains(result.Rejected, target => target.Path.EndsWith("incline.end", StringComparison.Ordinal));
  }

  [Fact]
  public void Evaluate_normalizes_nested_heart_rate_bounds_and_preserves_order()
  {
    var step = new WorkoutStep(
      new TimeGoal(TimeSpan.FromMinutes(5)),
      new HeartRateSpeed(120, 140, 6.06, 5.06, 8.06),
      new FixedIncline(0));
    var workout = new WorkoutDefinition(1, "HR", null, [new WorkoutRepeat(2, [step])]);

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, Speed, Incline, 10);

    Assert.True(result.IsValid);
    var repeat = Assert.IsType<WorkoutRepeat>(Assert.Single(result.Definition.Blocks));
    var normalized = Assert.IsType<HeartRateSpeed>(Assert.IsType<WorkoutStep>(Assert.Single(repeat.Blocks)).Speed);
    Assert.Equal(5.0, normalized.MinimumKilometersPerHour, 6);
    Assert.Equal(6.0, normalized.InitialKilometersPerHour, 6);
    Assert.Equal(8.0, normalized.MaximumKilometersPerHour, 6);
  }

  [Fact]
  public void Evaluate_normalizes_heart_rate_zone_speed_without_losing_the_zone()
  {
    var workout = Workout(new HeartRateZoneSpeed(3, 6.06, 5.06, 8.06), new FixedIncline(0));

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, Speed, Incline, 10);

    Assert.True(result.IsValid);
    var step = Assert.IsType<WorkoutStep>(Assert.Single(result.Definition.Blocks));
    var normalized = Assert.IsType<HeartRateZoneSpeed>(step.Speed);
    Assert.Equal(3, normalized.ZoneNumber);
    Assert.Equal(5.0, normalized.MinimumKilometersPerHour, 6);
    Assert.Equal(6.0, normalized.InitialKilometersPerHour, 6);
    Assert.Equal(8.0, normalized.MaximumKilometersPerHour, 6);
  }

  [Fact]
  public void Evaluate_enforces_profile_maximum_when_hardware_range_is_unknown()
  {
    var workout = Workout(new FixedSpeed(12.1), new FixedIncline(0));

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, null, null, 12);

    WorkoutTargetEvaluation rejected = Assert.Single(result.Rejected);
    Assert.Equal("blocks[0].speed", rejected.Path);
    Assert.Contains("profile maximum", rejected.Reason, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Evaluate_rejects_profile_maximum_below_treadmill_minimum_for_open_workout()
  {
    var workout = Workout(new OpenSpeed(), new FixedIncline(0));

    WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(workout, Speed, Incline, 0.5);

    WorkoutTargetEvaluation rejected = Assert.Single(result.Rejected);
    Assert.Equal("profile.maximumSpeed", rejected.Path);
  }

  private static WorkoutDefinition Workout(SpeedDirective speed, InclineDirective incline) => new(
    1,
    "Capability test",
    null,
    [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), speed, incline)]);
}
