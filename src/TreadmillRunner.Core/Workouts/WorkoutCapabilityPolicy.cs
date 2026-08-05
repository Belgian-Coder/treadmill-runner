using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Workouts;

public enum WorkoutTargetDisposition
{
  Accepted,
  Normalized,
  Rejected,
}

public enum WorkoutTargetKind
{
  Speed,
  Incline,
}

public sealed record WorkoutTargetEvaluation(
  string Path,
  WorkoutTargetKind Kind,
  double Requested,
  double? Normalized,
  WorkoutTargetDisposition Disposition,
  string Reason);

public sealed record WorkoutCapabilityResult(
  WorkoutDefinition Definition,
  IReadOnlyList<WorkoutTargetEvaluation> Targets)
{
  public bool IsValid => Targets.All(static target => target.Disposition != WorkoutTargetDisposition.Rejected);
  public IReadOnlyList<WorkoutTargetEvaluation> Rejected => Targets
    .Where(static target => target.Disposition == WorkoutTargetDisposition.Rejected)
    .ToArray();
}

public static class WorkoutCapabilityPolicy
{
  private const double ComparisonTolerance = 0.000_001;

  public static WorkoutCapabilityResult Evaluate(
    WorkoutDefinition definition,
    TreadmillOperatingRange? speedRange,
    TreadmillOperatingRange? inclineRange,
    double? profileMaximumSpeedKph)
  {
    ArgumentNullException.ThrowIfNull(definition);
    var targets = new List<WorkoutTargetEvaluation>();
    if (speedRange is not null && profileMaximumSpeedKph is not null &&
        profileMaximumSpeedKph < (double)speedRange.Minimum - ComparisonTolerance)
    {
      targets.Add(new WorkoutTargetEvaluation(
        "profile.maximumSpeed",
        WorkoutTargetKind.Speed,
        profileMaximumSpeedKph.Value,
        null,
        WorkoutTargetDisposition.Rejected,
        $"Profile maximum is below the treadmill minimum of {speedRange.Minimum:0.###} km/h."));
    }
    IReadOnlyList<WorkoutBlock> blocks = NormalizeBlocks(
      definition.Blocks,
      "blocks",
      speedRange,
      inclineRange,
      profileMaximumSpeedKph,
      targets);
    return new WorkoutCapabilityResult(
      new WorkoutDefinition(definition.SchemaVersion, definition.Title, definition.Description, blocks),
      targets.AsReadOnly());
  }

  private static IReadOnlyList<WorkoutBlock> NormalizeBlocks(
    IReadOnlyList<WorkoutBlock> blocks,
    string path,
    TreadmillOperatingRange? speedRange,
    TreadmillOperatingRange? inclineRange,
    double? profileMaximumSpeedKph,
    List<WorkoutTargetEvaluation> targets)
  {
    var normalized = new WorkoutBlock[blocks.Count];
    for (var index = 0; index < blocks.Count; index++)
    {
      string itemPath = $"{path}[{index}]";
      normalized[index] = blocks[index] switch
      {
        WorkoutStep step => NormalizeStep(step, itemPath, speedRange, inclineRange, profileMaximumSpeedKph, targets),
        WorkoutRepeat repeat => new WorkoutRepeat(
          repeat.Repetitions,
          NormalizeBlocks(repeat.Blocks, $"{itemPath}.blocks", speedRange, inclineRange, profileMaximumSpeedKph, targets)),
        _ => throw new InvalidOperationException($"Unsupported workout block {blocks[index].GetType().Name}."),
      };
    }
    return Array.AsReadOnly(normalized);
  }

  private static WorkoutStep NormalizeStep(
    WorkoutStep step,
    string path,
    TreadmillOperatingRange? speedRange,
    TreadmillOperatingRange? inclineRange,
    double? profileMaximumSpeedKph,
    List<WorkoutTargetEvaluation> targets) => new(
      step.Goal,
      NormalizeSpeed(step.Speed, $"{path}.speed", speedRange, profileMaximumSpeedKph, targets),
      NormalizeIncline(step.Incline, $"{path}.incline", inclineRange, targets),
      step.Cue,
      step.Notes);

  private static SpeedDirective NormalizeSpeed(
    SpeedDirective directive,
    string path,
    TreadmillOperatingRange? range,
    double? profileMaximumSpeedKph,
    List<WorkoutTargetEvaluation> targets) => directive switch
    {
      OpenSpeed => directive,
      FixedSpeed fixedSpeed => new FixedSpeed(EvaluateTarget(
        path,
        WorkoutTargetKind.Speed,
        fixedSpeed.KilometersPerHour,
        range,
        profileMaximumSpeedKph,
        targets)),
      SpeedRamp ramp => new SpeedRamp(
        EvaluateTarget($"{path}.start", WorkoutTargetKind.Speed, ramp.StartKilometersPerHour, range, profileMaximumSpeedKph, targets),
        EvaluateTarget($"{path}.end", WorkoutTargetKind.Speed, ramp.EndKilometersPerHour, range, profileMaximumSpeedKph, targets)),
      HeartRateSpeed heartRate => NormalizeHeartRateSpeed(heartRate, path, range, profileMaximumSpeedKph, targets),
      HeartRateZoneSpeed heartRateZone => NormalizeHeartRateZoneSpeed(heartRateZone, path, range, profileMaximumSpeedKph, targets),
      _ => throw new InvalidOperationException($"Unsupported speed directive {directive.GetType().Name}."),
    };

  private static HeartRateSpeed NormalizeHeartRateSpeed(
    HeartRateSpeed value,
    string path,
    TreadmillOperatingRange? range,
    double? profileMaximumSpeedKph,
    List<WorkoutTargetEvaluation> targets)
  {
    double minimum = EvaluateTarget($"{path}.minimum", WorkoutTargetKind.Speed, value.MinimumKilometersPerHour, range, profileMaximumSpeedKph, targets);
    double maximum = EvaluateTarget($"{path}.maximum", WorkoutTargetKind.Speed, value.MaximumKilometersPerHour, range, profileMaximumSpeedKph, targets);
    double initial = EvaluateTarget($"{path}.initial", WorkoutTargetKind.Speed, value.InitialKilometersPerHour, range, profileMaximumSpeedKph, targets);
    if (minimum > maximum || initial < minimum || initial > maximum)
      return value;
    return new HeartRateSpeed(value.MinimumBpm, value.MaximumBpm, initial, minimum, maximum);
  }

  private static HeartRateZoneSpeed NormalizeHeartRateZoneSpeed(
    HeartRateZoneSpeed value,
    string path,
    TreadmillOperatingRange? range,
    double? profileMaximumSpeedKph,
    List<WorkoutTargetEvaluation> targets)
  {
    double minimum = EvaluateTarget($"{path}.minimum", WorkoutTargetKind.Speed, value.MinimumKilometersPerHour, range, profileMaximumSpeedKph, targets);
    double maximum = EvaluateTarget($"{path}.maximum", WorkoutTargetKind.Speed, value.MaximumKilometersPerHour, range, profileMaximumSpeedKph, targets);
    double initial = EvaluateTarget($"{path}.initial", WorkoutTargetKind.Speed, value.InitialKilometersPerHour, range, profileMaximumSpeedKph, targets);
    if (minimum > maximum || initial < minimum || initial > maximum)
      return value;
    return new HeartRateZoneSpeed(value.ZoneNumber, initial, minimum, maximum);
  }

  private static InclineDirective NormalizeIncline(
    InclineDirective directive,
    string path,
    TreadmillOperatingRange? range,
    List<WorkoutTargetEvaluation> targets) => directive switch
    {
      FixedIncline fixedIncline => new FixedIncline(EvaluateTarget(
        path,
        WorkoutTargetKind.Incline,
        fixedIncline.Percent,
        range,
        null,
        targets)),
      InclineRamp ramp => new InclineRamp(
        EvaluateTarget($"{path}.start", WorkoutTargetKind.Incline, ramp.StartPercent, range, null, targets),
        EvaluateTarget($"{path}.end", WorkoutTargetKind.Incline, ramp.EndPercent, range, null, targets)),
      _ => throw new InvalidOperationException($"Unsupported incline directive {directive.GetType().Name}."),
    };

  private static double EvaluateTarget(
    string path,
    WorkoutTargetKind kind,
    double requested,
    TreadmillOperatingRange? range,
    double? profileMaximum,
    List<WorkoutTargetEvaluation> targets)
  {
    if (range is null)
    {
      if (kind == WorkoutTargetKind.Speed && profileMaximum is not null &&
          requested > profileMaximum.Value + ComparisonTolerance)
      {
        targets.Add(new WorkoutTargetEvaluation(
          path,
          kind,
          requested,
          null,
          WorkoutTargetDisposition.Rejected,
          $"Target exceeds the profile maximum of {profileMaximum.Value:0.###} km/h."));
        return requested;
      }
      targets.Add(new WorkoutTargetEvaluation(path, kind, requested, requested, WorkoutTargetDisposition.Accepted, "No verified hardware range is active."));
      return requested;
    }

    double minimum = (double)range.Minimum;
    double maximum = Math.Min((double)range.Maximum, profileMaximum ?? double.MaxValue);
    if (requested < minimum - ComparisonTolerance || requested > maximum + ComparisonTolerance)
    {
      targets.Add(new WorkoutTargetEvaluation(
        path,
        kind,
        requested,
        null,
        WorkoutTargetDisposition.Rejected,
        $"Target is outside the verified {minimum:0.###}–{maximum:0.###} range."));
      return requested;
    }

    decimal offset = (decimal)requested - range.Minimum;
    decimal steps = decimal.Floor(offset / range.Increment);
    double aligned = (double)(range.Minimum + (steps * range.Increment));
    aligned = Math.Clamp(aligned, minimum, maximum);
    bool changed = Math.Abs(aligned - requested) > ComparisonTolerance;
    targets.Add(new WorkoutTargetEvaluation(
      path,
      kind,
      requested,
      aligned,
      changed ? WorkoutTargetDisposition.Normalized : WorkoutTargetDisposition.Accepted,
      changed
        ? $"Aligned down to the verified {range.Increment:0.###} increment without increasing intensity."
        : "Target is within the verified range and increment."));
    return aligned;
  }
}
