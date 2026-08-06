using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class WorkoutProgressionTests
{
  [Fact]
  public void Nested_repeats_are_flattened_in_stable_execution_order()
  {
    var warmup = Step(new TimeGoal(TimeSpan.FromSeconds(10)), new FixedSpeed(8), new FixedIncline(1), "Warm up");
    var interval = Step(new DistanceGoal(0.1), new SpeedRamp(8, 10), new InclineRamp(1, 3), "Work");
    var definition = new WorkoutDefinition(1, "Mixed", null, [warmup, new WorkoutRepeat(2, [interval])]);

    var progression = new WorkoutProgression(definition);

    Assert.Equal(3, progression.TotalStepCount);
    Assert.Same(warmup, progression.CurrentStep);
    Assert.Same(interval, progression.NextStep);
    Assert.Equal(8, progression.PlannedSpeedKph);
    Assert.Equal(1, progression.PlannedInclinePercent);
  }

  [Fact]
  public void Time_and_distance_steps_advance_from_monotonic_authoritative_totals()
  {
    var definition = new WorkoutDefinition(
        1,
        "Mixed",
        null,
        [
          Step(new TimeGoal(TimeSpan.FromSeconds(10)), new FixedSpeed(8), new FixedIncline(1), "Warm up"),
          new WorkoutRepeat(2,
          [
            Step(new DistanceGoal(0.1), new SpeedRamp(8, 10), new InclineRamp(1, 3), "Work")
          ])
        ]);
    var progression = new WorkoutProgression(definition);

    var firstTransition = progression.Advance(TimeSpan.FromSeconds(10), 0.02);

    Assert.Single(firstTransition);
    Assert.Equal(1, progression.CurrentStepIndex);
    Assert.Equal(0, progression.ProgressFraction);

    Assert.Empty(progression.Advance(TimeSpan.FromSeconds(12), 0.07));
    Assert.Equal(0.5, progression.ProgressFraction, 6);
    Assert.Equal(9, progression.PlannedSpeedKph);
    Assert.Equal(2, progression.PlannedInclinePercent);

    Assert.Single(progression.Advance(TimeSpan.FromSeconds(15), 0.12));
    Assert.Equal(2, progression.CurrentStepIndex);
    Assert.Single(progression.Advance(TimeSpan.FromSeconds(20), 0.22));
    Assert.True(progression.IsComplete);
    Assert.Null(progression.CurrentStep);
    Assert.Null(progression.NextStep);
  }

  [Fact]
  public void Progression_rejects_totals_that_move_backwards()
  {
    var progression = new WorkoutProgression(new WorkoutDefinition(
        1,
        "Easy",
        null,
        [Step(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(8), new FixedIncline(1))]));
    progression.Advance(TimeSpan.FromSeconds(10), 0.02);

    Assert.Throws<ArgumentOutOfRangeException>(() => progression.Advance(TimeSpan.FromSeconds(9), 0.02));
    Assert.Throws<ArgumentOutOfRangeException>(() => progression.Advance(TimeSpan.FromSeconds(10), 0.01));
  }

  [Fact]
  public void Heart_rate_and_open_directives_expose_safe_planned_targets()
  {
    var definition = new WorkoutDefinition(
        1,
        "HR",
        null,
        [
          Step(new TimeGoal(TimeSpan.FromMinutes(1)), new HeartRateSpeed(140, 150, 9, 7, 11), new FixedIncline(1)),
          Step(new TimeGoal(TimeSpan.FromMinutes(1)), new OpenSpeed(), new FixedIncline(0))
        ]);
    var progression = new WorkoutProgression(definition);

    Assert.Equal(9, progression.PlannedSpeedKph);
    Assert.Equal(new HeartRateTarget(140, 150, null), progression.HeartRateTarget);

    progression.Advance(TimeSpan.FromMinutes(1), 1);

    Assert.Null(progression.PlannedSpeedKph);
    Assert.Null(progression.HeartRateTarget);
  }

  [Fact]
  public void Large_elapsed_jump_advances_every_completed_timed_step_and_checkpoint_restores_position()
  {
    var definition = new WorkoutDefinition(1, "Reconnect", null,
    [
      Step(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(5), new FixedIncline(0)),
      Step(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(6), new FixedIncline(1)),
      Step(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(7), new FixedIncline(2)),
    ]);
    var progression = new WorkoutProgression(definition);

    IReadOnlyList<WorkoutStepTransition> transitions = progression.Advance(TimeSpan.FromSeconds(130), 0.2);

    Assert.Equal(2, transitions.Count);
    Assert.Equal(2, progression.CurrentStepIndex);
    WorkoutProgressionCheckpoint checkpoint = progression.Capture();
    var restored = new WorkoutProgression(definition);
    restored.Restore(checkpoint);
    Assert.Equal(2, restored.CurrentStepIndex);
    Assert.InRange(restored.ProgressFraction, 0.16, 0.17);
  }

  private static WorkoutStep Step(
      StepGoal goal,
      SpeedDirective speed,
      InclineDirective incline,
      string? cue = null) => new(goal, speed, incline, cue);
}
