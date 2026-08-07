namespace TreadmillRunner.Core.Household;

public enum LiveDisplayStyle
{
  Balanced,
  LargeText,
  HighContrast,
}

public enum LiveMetric
{
  Speed,
  Incline,
  HeartRate,
  ElapsedTime,
  Distance,
  Calories,
}

public sealed record RunCuePreferences
{
  public static RunCuePreferences Default { get; } = new(true, true, true, true, true, 60);

  public RunCuePreferences(
    bool stepChanges,
    bool heartRateDeparture,
    bool halfway,
    bool connectionProblems,
    bool completion,
    int volumePercent)
  {
    if (volumePercent is < 0 or > 100)
      throw new ArgumentOutOfRangeException(nameof(volumePercent));

    StepChanges = stepChanges;
    HeartRateDeparture = heartRateDeparture;
    Halfway = halfway;
    ConnectionProblems = connectionProblems;
    Completion = completion;
    VolumePercent = volumePercent;
  }

  public bool StepChanges { get; }
  public bool HeartRateDeparture { get; }
  public bool Halfway { get; }
  public bool ConnectionProblems { get; }
  public bool Completion { get; }
  public int VolumePercent { get; }
}

public sealed record RunnerExperiencePreferences
{
  public static RunnerExperiencePreferences Default { get; } = new(
    LiveDisplayStyle.Balanced,
    [LiveMetric.Speed, LiveMetric.HeartRate, LiveMetric.ElapsedTime],
    RunCuePreferences.Default);

  public RunnerExperiencePreferences(
    LiveDisplayStyle displayStyle,
    IReadOnlyList<LiveMetric> primaryMetrics,
    RunCuePreferences cues)
  {
    ArgumentNullException.ThrowIfNull(primaryMetrics);
    ArgumentNullException.ThrowIfNull(cues);
    if (!Enum.IsDefined(displayStyle))
      throw new ArgumentOutOfRangeException(nameof(displayStyle));
    if (primaryMetrics.Count is < 2 or > 3 || primaryMetrics.Distinct().Count() != primaryMetrics.Count)
      throw new ArgumentException("Choose two or three distinct primary metrics.", nameof(primaryMetrics));
    if (primaryMetrics.Any(metric => !Enum.IsDefined(metric)))
      throw new ArgumentOutOfRangeException(nameof(primaryMetrics));

    DisplayStyle = displayStyle;
    PrimaryMetrics = Array.AsReadOnly(primaryMetrics.ToArray());
    Cues = cues;
  }

  public LiveDisplayStyle DisplayStyle { get; }
  public IReadOnlyList<LiveMetric> PrimaryMetrics { get; }
  public RunCuePreferences Cues { get; }
}

public sealed record RunReadinessInput(
  bool ProfileSelected,
  bool WorkoutSelected,
  bool DatabaseReady,
  bool TreadmillFresh,
  bool HeartRateRequired,
  bool HeartRateFresh,
  bool ManualControlAvailable,
  bool PlannedAutomationAllowed,
  bool HasUnknownCommandOutcome);

public sealed record RunReadinessAssessment(
  bool CanRecord,
  bool CanUseManualControl,
  bool CanUsePlannedAutomation,
  string RecordingReason,
  string ManualControlReason,
  string PlannedAutomationReason);

public static class RunReadinessClassifier
{
  public static RunReadinessAssessment Classify(RunReadinessInput input)
  {
    ArgumentNullException.ThrowIfNull(input);
    bool canRecord = input.ProfileSelected && input.WorkoutSelected && input.DatabaseReady;
    string recordReason = canRecord
      ? "Runner, workout, and local database are ready to record."
      : MissingRecordingReason(input);

    bool canManual = canRecord && input.TreadmillFresh && input.ManualControlAvailable && !input.HasUnknownCommandOutcome;
    string manualReason = canManual
      ? "Fresh treadmill telemetry and accepted manual controls are available."
      : input.HasUnknownCommandOutcome
        ? "A command outcome is unknown; use the physical console and Stop."
        : !canRecord
          ? "Recording readiness must be resolved first."
          : !input.TreadmillFresh
            ? "Fresh treadmill telemetry is required for manual control."
            : "Manual control is not accepted for this device state.";

    bool heartRateReady = !input.HeartRateRequired || input.HeartRateFresh;
    bool canPlanned = canManual && input.PlannedAutomationAllowed && heartRateReady;
    string plannedReason = canPlanned
      ? "Planned automation has fresh required inputs and accepted capabilities."
      : !canManual
        ? "Manual-control readiness must be resolved first."
        : !heartRateReady
          ? "Fresh heart rate is required for this workout's planned automation."
          : "Planned automation is not enabled for this workout or device state.";

    return new(canRecord, canManual, canPlanned, recordReason, manualReason, plannedReason);
  }

  private static string MissingRecordingReason(RunReadinessInput input) =>
    !input.ProfileSelected ? "Select a runner."
      : !input.WorkoutSelected ? "Select a workout."
      : "The local database is not ready.";
}

public enum SessionCompletion
{
  Completed,
  Interrupted,
  Missed,
}

public enum ProgressionAction
{
  Maintain,
  Repeat,
  Reduce,
  Advance,
  Reschedule,
}

public sealed record ProgressionEvidence(
  Guid ProfileId,
  Guid SessionId,
  SessionCompletion Completion,
  double AdherencePercentage,
  int? PerceivedExertion,
  double HeartRateCoveragePercentage,
  bool TelemetryComplete,
  bool WasInterrupted,
  int MissedScheduledSessions);

public sealed record ProgressionRecommendation(
  ProgressionAction Action,
  string Reason,
  string AlgorithmVersion,
  bool RequiresConfirmation = true);

public static class LocalProgressionAdviser
{
  public const string AlgorithmVersion = "local-progression-v1";

  public static ProgressionRecommendation Recommend(ProgressionEvidence evidence)
  {
    ArgumentNullException.ThrowIfNull(evidence);
    if (evidence.ProfileId == Guid.Empty || evidence.SessionId == Guid.Empty)
      throw new ArgumentException("Profile and session identifiers are required.");
    if (!double.IsFinite(evidence.AdherencePercentage) || evidence.AdherencePercentage is < 0 or > 100)
      throw new ArgumentOutOfRangeException(nameof(evidence.AdherencePercentage));
    if (!double.IsFinite(evidence.HeartRateCoveragePercentage) || evidence.HeartRateCoveragePercentage is < 0 or > 100)
      throw new ArgumentOutOfRangeException(nameof(evidence.HeartRateCoveragePercentage));
    if (evidence.PerceivedExertion is < 1 or > 10)
      throw new ArgumentOutOfRangeException(nameof(evidence.PerceivedExertion));

    if (evidence.Completion == SessionCompletion.Missed || evidence.MissedScheduledSessions > 0)
      return Result(ProgressionAction.Reschedule, "A scheduled session was missed; choose a new date before changing training load.");
    if (!evidence.TelemetryComplete || evidence.WasInterrupted || evidence.Completion == SessionCompletion.Interrupted)
      return Result(ProgressionAction.Repeat, "The session evidence is incomplete or interrupted, so progression is not inferred.");
    if (evidence.PerceivedExertion is >= 9 || evidence.AdherencePercentage < 70)
      return Result(ProgressionAction.Reduce, "Very high effort or low adherence suggests reducing the next session after confirmation.");
    if (evidence.PerceivedExertion is <= 6 && evidence.AdherencePercentage >= 90 && evidence.HeartRateCoveragePercentage >= 80)
      return Result(ProgressionAction.Advance, "Comfortable effort, strong adherence, and sufficient heart-rate coverage support advancing after confirmation.");
    return Result(ProgressionAction.Maintain, "The completed session supports keeping the current progression.");
  }

  private static ProgressionRecommendation Result(ProgressionAction action, string reason) =>
    new(action, reason, AlgorithmVersion);
}

public enum LocalSessionOrigin
{
  Hardware,
  Imported,
  Simulator,
  SystemTest,
}

public sealed record LocalSessionFact(
  Guid SessionId,
  Guid ProfileId,
  LocalSessionOrigin Origin,
  SessionCompletion Completion,
  DateTimeOffset EndedAtUtc,
  TimeSpan Duration,
  double DistanceKilometers,
  int? AverageHeartRateBpm,
  double? AdherencePercentage,
  bool TelemetryComplete);

public sealed record LocalTrendSummary(
  Guid ProfileId,
  int CompletedSessions,
  TimeSpan Duration,
  double DistanceKilometers,
  int IncompleteTelemetrySessions,
  double LongestDistanceKilometers,
  TimeSpan LongestDuration,
  int? HighestAverageHeartRateBpm);

public static class LocalTrendCalculator
{
  public static LocalTrendSummary Calculate(Guid profileId, IEnumerable<LocalSessionFact> facts)
  {
    if (profileId == Guid.Empty) throw new ArgumentException("Profile ID is required.", nameof(profileId));
    ArgumentNullException.ThrowIfNull(facts);
    LocalSessionFact[] included = facts
      .Where(fact => fact.ProfileId == profileId
        && fact.Completion == SessionCompletion.Completed
        && fact.Origin is not LocalSessionOrigin.Simulator and not LocalSessionOrigin.SystemTest)
      .ToArray();
    if (included.Any(fact => !double.IsFinite(fact.DistanceKilometers) || fact.DistanceKilometers < 0 || fact.Duration < TimeSpan.Zero))
      throw new ArgumentException("Session facts must have non-negative finite duration and distance.", nameof(facts));

    return new(
      profileId,
      included.Length,
      TimeSpan.FromTicks(included.Sum(static fact => fact.Duration.Ticks)),
      included.Sum(static fact => fact.DistanceKilometers),
      included.Count(static fact => !fact.TelemetryComplete),
      included.Select(static fact => fact.DistanceKilometers).DefaultIfEmpty().Max(),
      TimeSpan.FromTicks(included.Select(static fact => fact.Duration.Ticks).DefaultIfEmpty().Max()),
      included.Where(static fact => fact.AverageHeartRateBpm.HasValue)
        .Select(static fact => fact.AverageHeartRateBpm!.Value)
        .Cast<int?>()
        .DefaultIfEmpty()
        .Max());
  }
}

public sealed record AssignedHeartRateObservation(
  Guid ProfileId,
  string SensorLabel,
  bool IsFresh,
  DateTimeOffset ObservedAtUtc);

public sealed record QuickStartSuggestion(Guid? SuggestedProfileId, string Reason, bool RequiresConfirmation);

public static class RunnerQuickStartAdvisor
{
  public static QuickStartSuggestion Suggest(
    Guid? activeSessionProfileId,
    IEnumerable<AssignedHeartRateObservation> observations)
  {
    ArgumentNullException.ThrowIfNull(observations);
    if (activeSessionProfileId is { } active && active != Guid.Empty)
      return new(null, "An active session owns its runner; automatic profile switching is disabled.", false);

    AssignedHeartRateObservation[] fresh = observations
      .Where(static item => item.IsFresh && item.ProfileId != Guid.Empty)
      .GroupBy(static item => item.ProfileId)
      .Select(static group => group.OrderByDescending(item => item.ObservedAtUtc).First())
      .ToArray();
    return fresh.Length switch
    {
      1 => new(fresh[0].ProfileId, $"Fresh assigned sensor {fresh[0].SensorLabel} matches one runner.", true),
      0 => new(null, "No fresh assigned heart-rate sensor identifies a runner.", false),
      _ => new(null, "Multiple fresh assigned sensors are present; select a runner manually.", false),
    };
  }
}

public sealed record LocalBackupPolicy
{
  public LocalBackupPolicy(string destinationPath, int intervalHours, int retentionCount, bool enabled)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(destinationPath);
    if (destinationPath.Contains("://", StringComparison.Ordinal) || !Path.IsPathFullyQualified(destinationPath))
      throw new ArgumentException("Backup destination must be an absolute local or UNC path.", nameof(destinationPath));
    if (intervalHours is < 1 or > 168) throw new ArgumentOutOfRangeException(nameof(intervalHours));
    if (retentionCount is < 2 or > 60) throw new ArgumentOutOfRangeException(nameof(retentionCount));

    DestinationPath = destinationPath.Trim();
    IntervalHours = intervalHours;
    RetentionCount = retentionCount;
    Enabled = enabled;
  }

  public string DestinationPath { get; }
  public int IntervalHours { get; }
  public int RetentionCount { get; }
  public bool Enabled { get; }
}

public enum LocalHealthState
{
  Healthy,
  Degraded,
  ActionRequired,
}

public sealed record LocalHealthComponent(string Id, LocalHealthState State, string Detail);
public sealed record OperationsHealthSummary(LocalHealthState State, IReadOnlyList<LocalHealthComponent> Components);

public static class OperationsHealthAggregator
{
  public static OperationsHealthSummary Combine(IEnumerable<LocalHealthComponent> components)
  {
    ArgumentNullException.ThrowIfNull(components);
    LocalHealthComponent[] values = components.ToArray();
    if (values.Any(static item => string.IsNullOrWhiteSpace(item.Id) || string.IsNullOrWhiteSpace(item.Detail)))
      throw new ArgumentException("Health components require identifiers and details.", nameof(components));
    LocalHealthState state = values.Select(static item => item.State).DefaultIfEmpty(LocalHealthState.Healthy).Max();
    return new(state, Array.AsReadOnly(values));
  }
}
