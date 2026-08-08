namespace TreadmillRunner.Core.Workouts;

public sealed record HeartRateTarget(ushort? MinimumBpm, ushort? MaximumBpm, int? ZoneNumber);

public sealed record WorkoutStepTransition(
    int CompletedStepIndex,
    int? CurrentStepIndex,
    TimeSpan Elapsed,
    double DistanceKilometers);

public sealed record WorkoutProgressionCheckpoint(
  int CurrentStepIndex,
  TimeSpan LastElapsed,
  double LastDistanceKilometers,
  TimeSpan StepStartedAtElapsed,
  double StepStartedAtDistanceKilometers,
  TimeSpan ProgressStartedAtElapsed = default);

public sealed class WorkoutProgression
{
  private const double DistanceComparisonEpsilonKilometers = 1e-9;

  private readonly IReadOnlyList<WorkoutStep> _steps;
  private TimeSpan _lastElapsed;
  private double _lastDistanceKilometers;
  private TimeSpan _stepStartedAtElapsed;
  private double _stepStartedAtDistanceKilometers;
  private TimeSpan _progressStartedAtElapsed;

  public WorkoutProgression(WorkoutDefinition definition)
  {
    ArgumentNullException.ThrowIfNull(definition);
    var steps = new List<WorkoutStep>(definition.ExpandedStepCount);
    Expand(definition.Blocks, steps);
    _steps = steps.AsReadOnly();
  }

  public int CurrentStepIndex { get; private set; }

  public int TotalStepCount => _steps.Count;

  public bool IsComplete => CurrentStepIndex >= _steps.Count;

  public TimeSpan ElapsedSinceRestart => _lastElapsed - _progressStartedAtElapsed;

  public WorkoutStep? CurrentStep => IsComplete ? null : _steps[CurrentStepIndex];

  public WorkoutStep? NextStep => CurrentStepIndex + 1 < _steps.Count ? _steps[CurrentStepIndex + 1] : null;

  public double ProgressFraction => CurrentStep is { } step ? CalculateProgress(step) : 1;

  public double? PlannedSpeedKph => CurrentStep?.Speed switch
  {
    FixedSpeed fixedSpeed => fixedSpeed.KilometersPerHour,
    SpeedRamp ramp => Interpolate(ramp.StartKilometersPerHour, ramp.EndKilometersPerHour, ProgressFraction),
    HeartRateSpeed heartRate => heartRate.InitialKilometersPerHour,
    HeartRateZoneSpeed heartRateZone => heartRateZone.InitialKilometersPerHour,
    OpenSpeed => null,
    null => null,
    _ => throw new InvalidOperationException("Unsupported speed directive."),
  };

  public double? PlannedInclinePercent => CurrentStep?.Incline switch
  {
    FixedIncline fixedIncline => fixedIncline.Percent,
    InclineRamp ramp => Interpolate(ramp.StartPercent, ramp.EndPercent, ProgressFraction),
    null => null,
    _ => throw new InvalidOperationException("Unsupported incline directive."),
  };

  public HeartRateTarget? HeartRateTarget => CurrentStep?.Speed switch
  {
    HeartRateSpeed heartRate => new HeartRateTarget(heartRate.MinimumBpm, heartRate.MaximumBpm, null),
    HeartRateZoneSpeed heartRateZone => new HeartRateTarget(null, null, heartRateZone.ZoneNumber),
    _ => null,
  };

  public TimeSpan? RemainingDuration
  {
    get
    {
      if (IsComplete)
      {
        return TimeSpan.Zero;
      }

      long ticks = 0;
      for (var index = CurrentStepIndex; index < _steps.Count; index++)
      {
        if (_steps[index].Goal is not TimeGoal timeGoal)
        {
          return null;
        }

        var duration = index == CurrentStepIndex
            ? timeGoal.Duration - (_lastElapsed - _stepStartedAtElapsed)
            : timeGoal.Duration;
        ticks = checked(ticks + Math.Max(0, duration.Ticks));
      }

      return TimeSpan.FromTicks(ticks);
    }
  }

  public IReadOnlyList<WorkoutStepTransition> Advance(
      TimeSpan elapsed,
      double distanceKilometers)
  {
    if (elapsed < _lastElapsed)
    {
      throw new ArgumentOutOfRangeException(nameof(elapsed), "Elapsed workout time cannot move backwards.");
    }

    if (!double.IsFinite(distanceKilometers) || distanceKilometers < _lastDistanceKilometers)
    {
      throw new ArgumentOutOfRangeException(nameof(distanceKilometers), "Workout distance must be finite and cannot move backwards.");
    }

    _lastElapsed = elapsed;
    _lastDistanceKilometers = distanceKilometers;
    if (IsComplete)
    {
      return Array.Empty<WorkoutStepTransition>();
    }

    var transitions = new List<WorkoutStepTransition>();
    while (CurrentStep is { } step && IsGoalComplete(step.Goal))
    {
      TimeSpan completedAtElapsed = elapsed;
      double completedAtDistance = distanceKilometers;
      if (step.Goal is TimeGoal completedTime)
      {
        completedAtElapsed = _stepStartedAtElapsed + completedTime.Duration;
      }
      else if (step.Goal is DistanceGoal completedDistance)
      {
        completedAtDistance = _stepStartedAtDistanceKilometers + completedDistance.Kilometers;
      }
      var completedStepIndex = CurrentStepIndex;
      CurrentStepIndex++;
      transitions.Add(new WorkoutStepTransition(
          completedStepIndex,
          IsComplete ? null : CurrentStepIndex,
          completedAtElapsed,
          completedAtDistance));
      _stepStartedAtElapsed = completedAtElapsed;
      _stepStartedAtDistanceKilometers = completedAtDistance;
    }

    return transitions.AsReadOnly();
  }

  public WorkoutProgressionCheckpoint Capture() => new(
    CurrentStepIndex,
    _lastElapsed,
    _lastDistanceKilometers,
    _stepStartedAtElapsed,
    _stepStartedAtDistanceKilometers,
    _progressStartedAtElapsed);

  public void Restart(TimeSpan elapsed, double distanceKilometers)
  {
    if (elapsed < _lastElapsed)
      throw new ArgumentOutOfRangeException(nameof(elapsed), "Elapsed workout time cannot move backwards.");
    if (!double.IsFinite(distanceKilometers) || distanceKilometers < _lastDistanceKilometers)
      throw new ArgumentOutOfRangeException(nameof(distanceKilometers), "Workout distance must be finite and cannot move backwards.");

    CurrentStepIndex = 0;
    _lastElapsed = elapsed;
    _lastDistanceKilometers = distanceKilometers;
    _stepStartedAtElapsed = elapsed;
    _stepStartedAtDistanceKilometers = distanceKilometers;
    _progressStartedAtElapsed = elapsed;
  }

  public void Restore(WorkoutProgressionCheckpoint checkpoint)
  {
    ArgumentNullException.ThrowIfNull(checkpoint);
    if (checkpoint.CurrentStepIndex < 0 || checkpoint.CurrentStepIndex > _steps.Count ||
        checkpoint.LastElapsed < TimeSpan.Zero || checkpoint.StepStartedAtElapsed < TimeSpan.Zero ||
        checkpoint.StepStartedAtElapsed > checkpoint.LastElapsed ||
        checkpoint.ProgressStartedAtElapsed < TimeSpan.Zero ||
        checkpoint.ProgressStartedAtElapsed > checkpoint.LastElapsed ||
        !double.IsFinite(checkpoint.LastDistanceKilometers) || checkpoint.LastDistanceKilometers < 0 ||
        !double.IsFinite(checkpoint.StepStartedAtDistanceKilometers) || checkpoint.StepStartedAtDistanceKilometers < 0 ||
        checkpoint.StepStartedAtDistanceKilometers > checkpoint.LastDistanceKilometers)
      throw new ArgumentException("The workout progression checkpoint is invalid.", nameof(checkpoint));
    CurrentStepIndex = checkpoint.CurrentStepIndex;
    _lastElapsed = checkpoint.LastElapsed;
    _lastDistanceKilometers = checkpoint.LastDistanceKilometers;
    _stepStartedAtElapsed = checkpoint.StepStartedAtElapsed;
    _stepStartedAtDistanceKilometers = checkpoint.StepStartedAtDistanceKilometers;
    _progressStartedAtElapsed = checkpoint.ProgressStartedAtElapsed;
  }

  private bool IsGoalComplete(StepGoal goal) => goal switch
  {
    TimeGoal time => _lastElapsed - _stepStartedAtElapsed >= time.Duration,
    DistanceGoal distance => _lastDistanceKilometers - _stepStartedAtDistanceKilometers + DistanceComparisonEpsilonKilometers >= distance.Kilometers,
    _ => throw new InvalidOperationException("Unsupported workout goal."),
  };

  private double CalculateProgress(WorkoutStep step)
  {
    var progress = step.Goal switch
    {
      TimeGoal time => (_lastElapsed - _stepStartedAtElapsed).TotalSeconds / time.Duration.TotalSeconds,
      DistanceGoal distance => (_lastDistanceKilometers - _stepStartedAtDistanceKilometers) / distance.Kilometers,
      _ => throw new InvalidOperationException("Unsupported workout goal."),
    };
    return Math.Clamp(progress, 0, 1);
  }

  private static double Interpolate(double start, double end, double fraction) =>
      start + ((end - start) * fraction);

  private static void Expand(IReadOnlyList<WorkoutBlock> blocks, List<WorkoutStep> target)
  {
    foreach (var block in blocks)
    {
      switch (block)
      {
        case WorkoutStep step:
          target.Add(step);
          break;
        case WorkoutRepeat repeat:
          for (var repetition = 0; repetition < repeat.Repetitions; repetition++)
          {
            Expand(repeat.Blocks, target);
          }

          break;
        default:
          throw new InvalidOperationException("Unsupported workout block.");
      }
    }
  }
}
