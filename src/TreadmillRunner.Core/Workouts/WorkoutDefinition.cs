namespace TreadmillRunner.Core.Workouts;

public static class WorkoutDefinitionLimits
{
  public const int MaximumExpandedSteps = 10_000;

  public const int MaximumNestingDepth = 32;

  public static readonly TimeSpan MaximumKnownDuration = TimeSpan.FromHours(12);
}

public abstract record WorkoutBlock;

public sealed record WorkoutStep : WorkoutBlock
{
  public WorkoutStep(StepGoal goal, SpeedDirective speed, InclineDirective incline, string? cue = null, string? notes = null)
  {
    Goal = goal ?? throw new ArgumentNullException(nameof(goal));
    Speed = speed ?? throw new ArgumentNullException(nameof(speed));
    Incline = incline ?? throw new ArgumentNullException(nameof(incline));
    Cue = NormalizeOptional(cue);
    Notes = NormalizeOptional(notes);
  }

  public StepGoal Goal { get; }

  public SpeedDirective Speed { get; }

  public InclineDirective Incline { get; }

  public string? Cue { get; }

  public string? Notes { get; }

  private static string? NormalizeOptional(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

public sealed record WorkoutRepeat : WorkoutBlock
{
  public WorkoutRepeat(int repetitions, IReadOnlyList<WorkoutBlock> blocks)
  {
    if (repetitions < 1)
    {
      throw new ArgumentOutOfRangeException(nameof(repetitions), "Repeat count must be positive.");
    }

    ArgumentNullException.ThrowIfNull(blocks);
    if (blocks.Count == 0 || blocks.Any(static block => block is null))
    {
      throw new ArgumentException("A repeat must contain at least one block.", nameof(blocks));
    }

    Repetitions = repetitions;
    Blocks = Array.AsReadOnly(blocks.ToArray());
  }

  public int Repetitions { get; }

  public IReadOnlyList<WorkoutBlock> Blocks { get; }
}

public abstract record StepGoal;

public sealed record TimeGoal : StepGoal
{
  public TimeGoal(TimeSpan duration)
  {
    if (duration <= TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(duration), "Duration must be positive.");
    }

    Duration = duration;
  }

  public TimeSpan Duration { get; }
}

public sealed record DistanceGoal : StepGoal
{
  public DistanceGoal(double kilometers)
  {
    WorkoutValueValidation.RequirePositiveFinite(kilometers, nameof(kilometers));
    Kilometers = kilometers;
  }

  public double Kilometers { get; }
}

public abstract record SpeedDirective;

public sealed record OpenSpeed : SpeedDirective;

public sealed record FixedSpeed : SpeedDirective
{
  public FixedSpeed(double kilometersPerHour)
  {
    WorkoutValueValidation.RequireNonNegativeFinite(kilometersPerHour, nameof(kilometersPerHour));
    KilometersPerHour = WorkoutValueValidation.NormalizeZero(kilometersPerHour);
  }

  public double KilometersPerHour { get; }
}

public sealed record SpeedRamp : SpeedDirective
{
  public SpeedRamp(double startKilometersPerHour, double endKilometersPerHour)
  {
    WorkoutValueValidation.RequireNonNegativeFinite(startKilometersPerHour, nameof(startKilometersPerHour));
    WorkoutValueValidation.RequireNonNegativeFinite(endKilometersPerHour, nameof(endKilometersPerHour));
    StartKilometersPerHour = WorkoutValueValidation.NormalizeZero(startKilometersPerHour);
    EndKilometersPerHour = WorkoutValueValidation.NormalizeZero(endKilometersPerHour);
  }

  public double StartKilometersPerHour { get; }

  public double EndKilometersPerHour { get; }
}

public sealed record HeartRateSpeed : SpeedDirective
{
  public HeartRateSpeed(
      ushort minimumBpm,
      ushort maximumBpm,
      double initialKilometersPerHour,
      double minimumKilometersPerHour,
      double maximumKilometersPerHour)
  {
    if (minimumBpm == 0 || maximumBpm > 250 || minimumBpm > maximumBpm)
    {
      throw new ArgumentOutOfRangeException(nameof(minimumBpm), "Heart-rate bounds must be ordered and between 1 and 250 bpm.");
    }

    HeartRateSpeedValidation.ValidateSpeeds(
        initialKilometersPerHour,
        minimumKilometersPerHour,
        maximumKilometersPerHour);

    MinimumBpm = minimumBpm;
    MaximumBpm = maximumBpm;
    InitialKilometersPerHour = WorkoutValueValidation.NormalizeZero(initialKilometersPerHour);
    MinimumKilometersPerHour = WorkoutValueValidation.NormalizeZero(minimumKilometersPerHour);
    MaximumKilometersPerHour = WorkoutValueValidation.NormalizeZero(maximumKilometersPerHour);
  }

  public ushort MinimumBpm { get; }

  public ushort MaximumBpm { get; }

  public double InitialKilometersPerHour { get; }

  public double MinimumKilometersPerHour { get; }

  public double MaximumKilometersPerHour { get; }
}

public sealed record HeartRateZoneSpeed : SpeedDirective
{
  public HeartRateZoneSpeed(
      int zoneNumber,
      double initialKilometersPerHour,
      double minimumKilometersPerHour,
      double maximumKilometersPerHour)
  {
    if (zoneNumber is < 1 or > 10)
    {
      throw new ArgumentOutOfRangeException(nameof(zoneNumber), "Zone number must be between 1 and 10.");
    }

    HeartRateSpeedValidation.ValidateSpeeds(
        initialKilometersPerHour,
        minimumKilometersPerHour,
        maximumKilometersPerHour);

    ZoneNumber = zoneNumber;
    InitialKilometersPerHour = WorkoutValueValidation.NormalizeZero(initialKilometersPerHour);
    MinimumKilometersPerHour = WorkoutValueValidation.NormalizeZero(minimumKilometersPerHour);
    MaximumKilometersPerHour = WorkoutValueValidation.NormalizeZero(maximumKilometersPerHour);
  }

  public int ZoneNumber { get; }

  public double InitialKilometersPerHour { get; }

  public double MinimumKilometersPerHour { get; }

  public double MaximumKilometersPerHour { get; }
}

public abstract record InclineDirective;

public sealed record FixedIncline : InclineDirective
{
  public FixedIncline(double percent)
  {
    WorkoutValueValidation.RequireFinite(percent, nameof(percent));
    Percent = WorkoutValueValidation.NormalizeZero(percent);
  }

  public double Percent { get; }
}

public sealed record InclineRamp : InclineDirective
{
  public InclineRamp(double startPercent, double endPercent)
  {
    WorkoutValueValidation.RequireFinite(startPercent, nameof(startPercent));
    WorkoutValueValidation.RequireFinite(endPercent, nameof(endPercent));
    StartPercent = WorkoutValueValidation.NormalizeZero(startPercent);
    EndPercent = WorkoutValueValidation.NormalizeZero(endPercent);
  }

  public double StartPercent { get; }

  public double EndPercent { get; }
}

public sealed class WorkoutDefinition
{
  public WorkoutDefinition(int schemaVersion, string title, string? description, IReadOnlyList<WorkoutBlock> blocks)
  {
    if (schemaVersion != 1)
    {
      throw new ArgumentOutOfRangeException(nameof(schemaVersion), "Only workout schema version 1 is supported.");
    }

    ArgumentException.ThrowIfNullOrWhiteSpace(title);
    ArgumentNullException.ThrowIfNull(blocks);
    if (blocks.Count == 0 || blocks.Any(static block => block is null))
    {
      throw new ArgumentException("A workout must contain at least one block.", nameof(blocks));
    }

    var totals = Count(blocks, 1);
    if (totals.Steps > WorkoutDefinitionLimits.MaximumExpandedSteps)
    {
      throw new ArgumentOutOfRangeException(nameof(blocks), $"A workout can expand to at most {WorkoutDefinitionLimits.MaximumExpandedSteps} steps.");
    }

    if (totals.Duration > WorkoutDefinitionLimits.MaximumKnownDuration)
    {
      throw new ArgumentOutOfRangeException(nameof(blocks), "Known workout duration cannot exceed 12 hours.");
    }

    SchemaVersion = schemaVersion;
    Title = title.Trim();
    Description = string.IsNullOrWhiteSpace(description) ? null : description.Trim();
    Blocks = Array.AsReadOnly(blocks.ToArray());
    ExpandedStepCount = (int)totals.Steps;
    KnownDuration = totals.HasDistanceGoal ? null : totals.Duration;
  }

  public int SchemaVersion { get; }

  public string Title { get; }

  public string? Description { get; }

  public IReadOnlyList<WorkoutBlock> Blocks { get; }

  public int ExpandedStepCount { get; }

  public TimeSpan? KnownDuration { get; }

  private static WorkoutTotals Count(IReadOnlyList<WorkoutBlock> blocks, int depth)
  {
    if (depth > WorkoutDefinitionLimits.MaximumNestingDepth)
    {
      throw new ArgumentOutOfRangeException(nameof(blocks), $"Workout nesting cannot exceed {WorkoutDefinitionLimits.MaximumNestingDepth} levels.");
    }

    long steps = 0;
    long durationTicks = 0;
    var hasDistanceGoal = false;
    foreach (var block in blocks)
    {
      switch (block)
      {
        case WorkoutStep step:
          steps++;
          if (step.Goal is TimeGoal time)
          {
            durationTicks = checked(durationTicks + time.Duration.Ticks);
          }
          else
          {
            hasDistanceGoal = true;
          }

          break;
        case WorkoutRepeat repeat:
          var nested = Count(repeat.Blocks, depth + 1);
          steps = SaturatingAdd(steps, SaturatingMultiply(nested.Steps, repeat.Repetitions, WorkoutDefinitionLimits.MaximumExpandedSteps + 1L), WorkoutDefinitionLimits.MaximumExpandedSteps + 1L);
          durationTicks = SaturatingAdd(
              durationTicks,
              SaturatingMultiply(nested.Duration.Ticks, repeat.Repetitions, WorkoutDefinitionLimits.MaximumKnownDuration.Ticks + 1),
              WorkoutDefinitionLimits.MaximumKnownDuration.Ticks + 1);
          hasDistanceGoal |= nested.HasDistanceGoal;
          break;
        default:
          throw new ArgumentException($"Unsupported workout block type '{block.GetType().Name}'.", nameof(blocks));
      }

      if (steps > WorkoutDefinitionLimits.MaximumExpandedSteps || durationTicks > WorkoutDefinitionLimits.MaximumKnownDuration.Ticks)
      {
        return new WorkoutTotals(steps, TimeSpan.FromTicks(Math.Min(durationTicks, TimeSpan.MaxValue.Ticks)), hasDistanceGoal);
      }
    }

    return new WorkoutTotals(steps, TimeSpan.FromTicks(durationTicks), hasDistanceGoal);
  }

  private readonly record struct WorkoutTotals(long Steps, TimeSpan Duration, bool HasDistanceGoal);

  private static long SaturatingMultiply(long value, int multiplier, long limit)
  {
    return value > limit / multiplier ? limit : value * multiplier;
  }

  private static long SaturatingAdd(long left, long right, long limit)
  {
    return left > limit - right ? limit : left + right;
  }
}

internal static class HeartRateSpeedValidation
{
  public static void ValidateSpeeds(
      double initialKilometersPerHour,
      double minimumKilometersPerHour,
      double maximumKilometersPerHour)
  {
    WorkoutValueValidation.RequireNonNegativeFinite(initialKilometersPerHour, nameof(initialKilometersPerHour));
    WorkoutValueValidation.RequireNonNegativeFinite(minimumKilometersPerHour, nameof(minimumKilometersPerHour));
    WorkoutValueValidation.RequireNonNegativeFinite(maximumKilometersPerHour, nameof(maximumKilometersPerHour));
    if (minimumKilometersPerHour > maximumKilometersPerHour || initialKilometersPerHour < minimumKilometersPerHour || initialKilometersPerHour > maximumKilometersPerHour)
    {
      throw new ArgumentException("Initial HR speed must be inside the ordered minimum and maximum speeds.");
    }
  }
}

internal static class WorkoutValueValidation
{
  public static double NormalizeZero(double value) => value == 0 ? 0 : value;

  public static void RequireFinite(double value, string parameterName)
  {
    if (!double.IsFinite(value))
    {
      throw new ArgumentOutOfRangeException(parameterName, "Value must be finite.");
    }
  }

  public static void RequirePositiveFinite(double value, string parameterName)
  {
    RequireFinite(value, parameterName);
    if (value <= 0)
    {
      throw new ArgumentOutOfRangeException(parameterName, "Value must be greater than zero.");
    }
  }

  public static void RequireNonNegativeFinite(double value, string parameterName)
  {
    RequireFinite(value, parameterName);
    if (value < 0)
    {
      throw new ArgumentOutOfRangeException(parameterName, "Value cannot be negative.");
    }
  }
}
