namespace TreadmillRunner.Core.Sessions;

public enum WorkoutSelectionSource
{
  Legacy,
  Manual,
  Library,
  Calendar,
  Program,
}

public enum SessionOrigin
{
  Legacy,
  Hardware,
  Simulator,
  SystemTest,
}

public sealed record WorkoutSessionSelection(
  WorkoutSelectionSource Source,
  Guid? ProgramRunId = null,
  Guid? ProgramItemId = null)
{
  public static WorkoutSessionSelection Library { get; } = new(WorkoutSelectionSource.Library);
}

public sealed record NewWorkoutSession
{
  public NewWorkoutSession(
      Guid sessionId,
      Guid userProfileId,
      string userProfileName,
      Guid workoutRevisionId,
      string workoutTitle,
      DateTimeOffset armedAt,
      string controllerConfigurationJson,
      string metricAlgorithmVersion,
      WorkoutSessionSelection? selection = null,
      SessionOrigin origin = SessionOrigin.Legacy)
  {
    SessionContractValidation.RequireId(sessionId, nameof(sessionId));
    SessionContractValidation.RequireId(userProfileId, nameof(userProfileId));
    ArgumentException.ThrowIfNullOrWhiteSpace(userProfileName);
    SessionContractValidation.RequireId(workoutRevisionId, nameof(workoutRevisionId));
    ArgumentException.ThrowIfNullOrWhiteSpace(workoutTitle);
    SessionContractValidation.RequireUtc(armedAt, nameof(armedAt));
    ArgumentException.ThrowIfNullOrWhiteSpace(controllerConfigurationJson);
    ArgumentException.ThrowIfNullOrWhiteSpace(metricAlgorithmVersion);
    selection ??= WorkoutSessionSelection.Library;
    if ((selection.ProgramRunId is null) != (selection.ProgramItemId is null) ||
        (selection.Source == WorkoutSelectionSource.Program) != (selection.ProgramRunId is not null))
    {
      throw new ArgumentException("Program selections require both a program run and program item.", nameof(selection));
    }

    SessionId = sessionId;
    UserProfileId = userProfileId;
    UserProfileName = userProfileName.Trim();
    WorkoutRevisionId = workoutRevisionId;
    WorkoutTitle = workoutTitle.Trim();
    ArmedAt = armedAt;
    ControllerConfigurationJson = controllerConfigurationJson.Trim();
    MetricAlgorithmVersion = metricAlgorithmVersion.Trim();
    Selection = selection;
    Origin = origin;
  }

  public Guid SessionId { get; }
  public Guid UserProfileId { get; }
  public string UserProfileName { get; }
  public Guid WorkoutRevisionId { get; }
  public string WorkoutTitle { get; }
  public DateTimeOffset ArmedAt { get; }
  public string ControllerConfigurationJson { get; }
  public string MetricAlgorithmVersion { get; }
  public WorkoutSessionSelection Selection { get; }
  public SessionOrigin Origin { get; }
}

public sealed record StoredWorkoutSession(
    NewWorkoutSession Definition,
    SessionState State,
    DateTimeOffset? StartedAt,
    DateTimeOffset? EndedAt,
    TimeSpan Duration,
    double DistanceKilometers,
    double EstimatedKilocalories,
    double? AverageHeartRateBpm,
    ushort? MaximumHeartRateBpm,
    double AverageSpeedKph,
    double AverageInclinePercent,
    SessionDebrief? Debrief,
    IReadOnlyList<SessionSample> Samples,
    IReadOnlyList<SessionEvent> Events);

public sealed record HistoryDeletionPreview(
  Guid SessionId,
  Guid UserProfileId,
  string WorkoutTitle,
  SessionState State,
  SessionOrigin Origin,
  int SampleCount,
  int EventCount,
  double DistanceKilometers,
  double MaintenanceDistanceImpactKilometers,
  bool IsProgramLinked,
  string? GarminStatus,
  bool CanDelete,
  string Reason,
  string Revision,
  bool GarminRemoteActivityMayRemain);

public sealed record DeleteHistorySessionOperation(
  Guid OperationId,
  Guid SessionId,
  Guid UserProfileId,
  string ExpectedRevision,
  string RequestFingerprint,
  DateTimeOffset RequestedAtUtc);

public sealed record HistoryDeletionResult(
  Guid SessionId,
  bool Deleted,
  int DeletedSampleCount,
  int DeletedEventCount,
  string? DeletedGarminStatus,
  double RemovedMaintenanceDistanceKilometers,
  bool GarminRemoteActivityMayRemain,
  DateTimeOffset DeletedAtUtc);

public interface ISessionStore
{
  Task CreateAsync(NewWorkoutSession session, CancellationToken cancellationToken = default);

  Task MarkRunningAsync(
    Guid sessionId,
    DateTimeOffset startedAt,
    CancellationToken cancellationToken = default);

  Task AppendSampleAsync(SessionSample sample, CancellationToken cancellationToken = default);

  Task AppendEventAsync(
    Guid sessionId,
    SessionEvent sessionEvent,
    CancellationToken cancellationToken = default);

  Task FinalizeAsync(SessionSummary summary, CancellationToken cancellationToken = default);

  Task SaveDebriefAsync(SessionDebrief debrief, CancellationToken cancellationToken = default);

  Task<StoredWorkoutSession?> FindAsync(
    Guid sessionId,
    CancellationToken cancellationToken = default);

  Task<IReadOnlyList<SessionSummary>> ListSummariesAsync(
    Guid userProfileId,
    int take = 50,
    CancellationToken cancellationToken = default,
    bool includeSystemTests = false);

  Task<HistoryDeletionPreview?> PreviewDeletionAsync(
    Guid sessionId,
    Guid userProfileId,
    CancellationToken cancellationToken = default);

  Task<HistoryDeletionResult> DeleteAsync(
    DeleteHistorySessionOperation operation,
    CancellationToken cancellationToken = default);

  Task<int> InterruptUnfinishedAsync(
    DateTimeOffset interruptedAt,
    string reason,
    CancellationToken cancellationToken = default);
}
