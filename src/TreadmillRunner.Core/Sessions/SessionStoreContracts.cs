namespace TreadmillRunner.Core.Sessions;

public enum WorkoutSelectionSource
{
  Legacy,
  Manual,
  Library,
  Calendar,
  Program,
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
      WorkoutSessionSelection? selection = null)
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
    CancellationToken cancellationToken = default);

  Task<int> InterruptUnfinishedAsync(
    DateTimeOffset interruptedAt,
    string reason,
    CancellationToken cancellationToken = default);
}
