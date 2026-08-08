using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Live;

public enum HeartRateSource
{
  None,
  PolarH10,
  GarminBleBroadcast,
  Simulated,
  BluetoothHeartRate,
}

public enum PreflightCheckStatus
{
  Ready,
  NotRequired,
  Waiting,
  Blocked,
}

public sealed record PreflightCheck
{
  public PreflightCheck(string id, string label, PreflightCheckStatus status, string? detail = null)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(id);
    ArgumentException.ThrowIfNullOrWhiteSpace(label);
    Id = id.Trim();
    Label = label.Trim();
    Status = status;
    Detail = string.IsNullOrWhiteSpace(detail) ? null : detail.Trim();
  }

  public string Id { get; }
  public string Label { get; }
  public PreflightCheckStatus Status { get; }
  public string? Detail { get; }
}

public sealed record PreflightSnapshot
{
  public PreflightSnapshot(
      DateTimeOffset capturedAt,
      Guid userProfileId,
      string userProfileName,
      Guid workoutRevisionId,
      string workoutTitle,
      TimeSpan? expectedDuration,
      string intensityLabel,
      bool requiresHeartRate,
      HeartRateSource selectedHeartRateSource,
      IReadOnlyList<PreflightCheck> checks,
      bool canStartRemotely = false,
      bool canStopRemotely = false,
      double? minimumStartSpeedKph = null,
      bool canSetSpeedRemotely = false,
      bool canSetInclineRemotely = false,
      bool canPauseRemotely = false,
      TreadmillOperatingRange? speedRange = null,
      TreadmillOperatingRange? inclineRange = null,
      HeartRateAutomationMode heartRateAutomationMode = HeartRateAutomationMode.Disabled,
      string? heartRateAutomationReason = null,
      IReadOnlyList<WorkoutTargetEvaluation>? targetEvaluations = null)
  {
    RequireId(userProfileId, nameof(userProfileId));
    RequireId(workoutRevisionId, nameof(workoutRevisionId));
    ArgumentException.ThrowIfNullOrWhiteSpace(userProfileName);
    ArgumentException.ThrowIfNullOrWhiteSpace(workoutTitle);
    ArgumentException.ThrowIfNullOrWhiteSpace(intensityLabel);
    ArgumentNullException.ThrowIfNull(checks);
    if (expectedDuration < TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(expectedDuration));
    }

    ActiveWorkoutStep.RequireNullableFinite(minimumStartSpeedKph, nameof(minimumStartSpeedKph), true);

    if (checks.Any(static check => check is null) ||
        checks.GroupBy(static check => check.Id, StringComparer.Ordinal).Any(static group => group.Count() != 1))
    {
      throw new ArgumentException("Preflight checks must be non-null and have unique IDs.", nameof(checks));
    }

    CapturedAt = capturedAt;
    UserProfileId = userProfileId;
    UserProfileName = userProfileName.Trim();
    WorkoutRevisionId = workoutRevisionId;
    WorkoutTitle = workoutTitle.Trim();
    ExpectedDuration = expectedDuration;
    IntensityLabel = intensityLabel.Trim();
    RequiresHeartRate = requiresHeartRate;
    SelectedHeartRateSource = selectedHeartRateSource;
    Checks = Array.AsReadOnly(checks.ToArray());
    CanStartRemotely = canStartRemotely;
    CanStopRemotely = canStopRemotely;
    MinimumStartSpeedKph = minimumStartSpeedKph;
    CanSetSpeedRemotely = canSetSpeedRemotely;
    CanSetInclineRemotely = canSetInclineRemotely;
    CanPauseRemotely = canPauseRemotely;
    SpeedRange = speedRange;
    InclineRange = inclineRange;
    HeartRateAutomationMode = heartRateAutomationMode;
    HeartRateAutomationReason = string.IsNullOrWhiteSpace(heartRateAutomationReason)
      ? null
      : heartRateAutomationReason.Trim();
    TargetEvaluations = Array.AsReadOnly((targetEvaluations ?? []).ToArray());
  }

  public DateTimeOffset CapturedAt { get; }
  public Guid UserProfileId { get; }
  public string UserProfileName { get; }
  public Guid WorkoutRevisionId { get; }
  public string WorkoutTitle { get; }
  public TimeSpan? ExpectedDuration { get; }
  public string IntensityLabel { get; }
  public bool RequiresHeartRate { get; }
  public HeartRateSource SelectedHeartRateSource { get; }
  public IReadOnlyList<PreflightCheck> Checks { get; }
  public bool CanStartRemotely { get; }
  public bool CanStopRemotely { get; }
  public double? MinimumStartSpeedKph { get; }
  public bool CanSetSpeedRemotely { get; }
  public bool CanSetInclineRemotely { get; }
  public bool CanPauseRemotely { get; }
  public TreadmillOperatingRange? SpeedRange { get; }
  public TreadmillOperatingRange? InclineRange { get; }
  public HeartRateAutomationMode HeartRateAutomationMode { get; }
  public string? HeartRateAutomationReason { get; }
  public IReadOnlyList<WorkoutTargetEvaluation> TargetEvaluations { get; }
  public bool IsReady => Checks.All(static check => check.Status is PreflightCheckStatus.Ready or PreflightCheckStatus.NotRequired);
  public IReadOnlyList<PreflightCheck> ReadinessBlockers => Array.AsReadOnly(
      Checks.Where(static check => check.Status is not (PreflightCheckStatus.Ready or PreflightCheckStatus.NotRequired)).ToArray());

  private static void RequireId(Guid value, string parameterName)
  {
    if (value == Guid.Empty)
    {
      throw new ArgumentException("ID cannot be empty.", parameterName);
    }
  }
}

public enum SessionControlAccess
{
  Observer,
  Controller,
}

public enum SessionConnectionPhase
{
  Ready,
  Reconnecting,
  Recovered,
  NeedsAttention,
}

public enum SessionRecoveryState
{
  None,
  TelemetryGap,
  Reconciling,
  Recovered,
  AwaitingResume,
  RestartTracking,
}

public sealed record ActiveWorkoutStep
{
  public ActiveWorkoutStep(
      int index,
      int totalStepCount,
      string? cue,
      string? notes,
      double progressFraction,
      double? plannedSpeedKph,
      double? plannedInclinePercent)
  {
    if (index < 0 || totalStepCount < 1 || index >= totalStepCount)
    {
      throw new ArgumentOutOfRangeException(nameof(index));
    }

    if (!double.IsFinite(progressFraction) || progressFraction is < 0 or > 1)
    {
      throw new ArgumentOutOfRangeException(nameof(progressFraction));
    }

    RequireNullableFinite(plannedSpeedKph, nameof(plannedSpeedKph), requireNonNegative: true);
    RequireNullableFinite(plannedInclinePercent, nameof(plannedInclinePercent), requireNonNegative: false);
    Index = index;
    TotalStepCount = totalStepCount;
    Cue = string.IsNullOrWhiteSpace(cue) ? null : cue.Trim();
    Notes = string.IsNullOrWhiteSpace(notes) ? null : notes.Trim();
    ProgressFraction = progressFraction;
    PlannedSpeedKph = plannedSpeedKph;
    PlannedInclinePercent = plannedInclinePercent;
  }

  public int Index { get; }
  public int TotalStepCount { get; }
  public string? Cue { get; }
  public string? Notes { get; }
  public double ProgressFraction { get; }
  public double? PlannedSpeedKph { get; }
  public double? PlannedInclinePercent { get; }

  internal static void RequireNullableFinite(double? value, string parameterName, bool requireNonNegative)
  {
    if (value is { } present && (!double.IsFinite(present) || (requireNonNegative && present < 0)))
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }
}

public sealed record WorkoutPlanPoint
{
  public WorkoutPlanPoint(TimeSpan elapsed, double? speedKph, double? inclinePercent)
  {
    if (elapsed < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(elapsed));
    ActiveWorkoutStep.RequireNullableFinite(speedKph, nameof(speedKph), true);
    ActiveWorkoutStep.RequireNullableFinite(inclinePercent, nameof(inclinePercent), false);
    Elapsed = elapsed;
    SpeedKph = speedKph;
    InclinePercent = inclinePercent;
  }

  public TimeSpan Elapsed { get; }
  public double? SpeedKph { get; }
  public double? InclinePercent { get; }
}

public sealed record ActiveSessionSnapshot
{
  public ActiveSessionSnapshot(
      Guid sessionId,
      Guid userProfileId,
      string userProfileName,
      Guid workoutRevisionId,
      string workoutTitle,
      LiveSnapshot live,
      long version,
      ActiveWorkoutStep? currentStep,
      ActiveWorkoutStep? nextStep,
      TimeSpan? remaining,
      double? plannedSpeedKph,
      double requestedSpeedKph,
      double? plannedInclinePercent,
      double requestedInclinePercent,
      HeartRateTarget? heartRateTarget,
      HeartRateSource heartRateSource,
      TimeSpan? heartRateAge,
      SessionControlAccess controlAccess,
      DateTimeOffset? leaseExpiresAt,
      IReadOnlyList<string> warnings,
      TreadmillCommandResult? lastCommandResult = null,
      bool canStartRemotely = false,
      bool canStopRemotely = false,
      double? minimumStartSpeedKph = null,
      bool canSetSpeedRemotely = false,
      bool canSetInclineRemotely = false,
      bool canPauseRemotely = false,
      TreadmillOperatingRange? speedRange = null,
      TreadmillOperatingRange? inclineRange = null,
      HeartRateAutomationMode heartRateAutomationMode = HeartRateAutomationMode.Disabled,
      string? heartRateAutomationReason = null,
      IReadOnlyList<WorkoutPlanPoint>? workoutPlan = null,
      SessionConnectionPhase connectionPhase = SessionConnectionPhase.Ready,
      Guid? serviceInstanceId = null,
      SessionRecoveryState recoveryState = SessionRecoveryState.None,
      string? commandsSuspendedReason = null,
      DateTimeOffset? telemetryGapStartedAtUtc = null,
      bool canResumePlannedControls = false,
      DateTimeOffset? lastReconciledAtUtc = null,
      TimeSpan workoutElapsed = default)
  {
    RequireId(sessionId, nameof(sessionId));
    RequireId(userProfileId, nameof(userProfileId));
    RequireId(workoutRevisionId, nameof(workoutRevisionId));
    ArgumentException.ThrowIfNullOrWhiteSpace(userProfileName);
    ArgumentException.ThrowIfNullOrWhiteSpace(workoutTitle);
    ArgumentNullException.ThrowIfNull(live);
    ArgumentNullException.ThrowIfNull(warnings);
    if (version < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(version));
    }

    if (remaining < TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(remaining));
    }

    if (heartRateAge < TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(heartRateAge));
    }

    if (workoutElapsed < TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(workoutElapsed));
    }

    ActiveWorkoutStep.RequireNullableFinite(plannedSpeedKph, nameof(plannedSpeedKph), true);
    ActiveWorkoutStep.RequireNullableFinite(plannedInclinePercent, nameof(plannedInclinePercent), false);
    ActiveWorkoutStep.RequireNullableFinite(requestedSpeedKph, nameof(requestedSpeedKph), true);
    ActiveWorkoutStep.RequireNullableFinite(requestedInclinePercent, nameof(requestedInclinePercent), false);
    ActiveWorkoutStep.RequireNullableFinite(minimumStartSpeedKph, nameof(minimumStartSpeedKph), true);

    SessionId = sessionId;
    UserProfileId = userProfileId;
    UserProfileName = userProfileName.Trim();
    WorkoutRevisionId = workoutRevisionId;
    WorkoutTitle = workoutTitle.Trim();
    Live = live;
    Version = version;
    CurrentStep = currentStep;
    NextStep = nextStep;
    Remaining = remaining;
    PlannedSpeedKph = plannedSpeedKph;
    RequestedSpeedKph = requestedSpeedKph;
    PlannedInclinePercent = plannedInclinePercent;
    RequestedInclinePercent = requestedInclinePercent;
    HeartRateTarget = heartRateTarget;
    HeartRateSource = heartRateSource;
    HeartRateAge = heartRateAge;
    ControlAccess = controlAccess;
    LeaseExpiresAt = leaseExpiresAt;
    Warnings = Array.AsReadOnly(warnings.Where(static warning => !string.IsNullOrWhiteSpace(warning)).Select(static warning => warning.Trim()).ToArray());
    LastCommandResult = lastCommandResult;
    CanStartRemotely = canStartRemotely;
    CanStopRemotely = canStopRemotely;
    MinimumStartSpeedKph = minimumStartSpeedKph;
    CanSetSpeedRemotely = canSetSpeedRemotely;
    CanSetInclineRemotely = canSetInclineRemotely;
    CanPauseRemotely = canPauseRemotely;
    SpeedRange = speedRange;
    InclineRange = inclineRange;
    HeartRateAutomationMode = heartRateAutomationMode;
    HeartRateAutomationReason = string.IsNullOrWhiteSpace(heartRateAutomationReason)
      ? null
      : heartRateAutomationReason.Trim();
    WorkoutPlan = Array.AsReadOnly((workoutPlan ?? []).ToArray());
    ConnectionPhase = connectionPhase;
    ServiceInstanceId = serviceInstanceId;
    RecoveryState = recoveryState;
    CommandsSuspendedReason = string.IsNullOrWhiteSpace(commandsSuspendedReason) ? null : commandsSuspendedReason.Trim();
    TelemetryGapStartedAtUtc = telemetryGapStartedAtUtc;
    CanResumePlannedControls = canResumePlannedControls;
    LastReconciledAtUtc = lastReconciledAtUtc;
    WorkoutElapsed = workoutElapsed;
  }

  public Guid SessionId { get; }
  public Guid UserProfileId { get; }
  public string UserProfileName { get; }
  public Guid WorkoutRevisionId { get; }
  public string WorkoutTitle { get; }
  public LiveSnapshot Live { get; }
  public long Version { get; }
  public ActiveWorkoutStep? CurrentStep { get; }
  public ActiveWorkoutStep? NextStep { get; }
  public TimeSpan? Remaining { get; }
  public double? PlannedSpeedKph { get; }
  public double RequestedSpeedKph { get; }
  public double? PlannedInclinePercent { get; }
  public double RequestedInclinePercent { get; }
  public HeartRateTarget? HeartRateTarget { get; }
  public HeartRateSource HeartRateSource { get; }
  public TimeSpan? HeartRateAge { get; }
  public SessionControlAccess ControlAccess { get; }
  public DateTimeOffset? LeaseExpiresAt { get; }
  public IReadOnlyList<string> Warnings { get; }
  public TreadmillCommandResult? LastCommandResult { get; }
  public bool CanStartRemotely { get; }
  public bool CanStopRemotely { get; }
  public double? MinimumStartSpeedKph { get; }
  public bool CanSetSpeedRemotely { get; }
  public bool CanSetInclineRemotely { get; }
  public bool CanPauseRemotely { get; }
  public TreadmillOperatingRange? SpeedRange { get; }
  public TreadmillOperatingRange? InclineRange { get; }
  public HeartRateAutomationMode HeartRateAutomationMode { get; }
  public string? HeartRateAutomationReason { get; }
  public IReadOnlyList<WorkoutPlanPoint> WorkoutPlan { get; }
  public SessionConnectionPhase ConnectionPhase { get; }
  public Guid? ServiceInstanceId { get; }
  public SessionRecoveryState RecoveryState { get; }
  public string? CommandsSuspendedReason { get; }
  public DateTimeOffset? TelemetryGapStartedAtUtc { get; }
  public bool CanResumePlannedControls { get; }
  public DateTimeOffset? LastReconciledAtUtc { get; }
  public TimeSpan WorkoutElapsed { get; }

  private static void RequireId(Guid value, string parameterName)
  {
    if (value == Guid.Empty)
    {
      throw new ArgumentException("ID cannot be empty.", parameterName);
    }
  }
}
