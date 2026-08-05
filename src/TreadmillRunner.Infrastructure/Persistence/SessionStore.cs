using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed class SessionStore(
    IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : ISessionStore
{
  private static readonly JsonSerializerOptions EventJsonOptions = new(JsonSerializerDefaults.Web);

  public async Task CreateAsync(
    NewWorkoutSession session,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(session);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = session.SessionId,
      UserProfileId = session.UserProfileId,
      UserProfileName = session.UserProfileName,
      WorkoutRevisionId = session.WorkoutRevisionId,
      WorkoutProgramRunId = session.Selection.ProgramRunId,
      WorkoutProgramItemId = session.Selection.ProgramItemId,
      SelectionSource = session.Selection.Source.ToString(),
      WorkoutTitle = session.WorkoutTitle,
      State = SessionState.ArmedWaitingForPhysicalStart.ToString(),
      ArmedAtUtc = session.ArmedAt,
      MetricAlgorithmVersion = session.MetricAlgorithmVersion,
      ControllerConfigurationJson = session.ControllerConfigurationJson,
    });
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkRunningAsync(
    Guid sessionId,
    DateTimeOffset startedAt,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    RequireUtc(startedAt, nameof(startedAt));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, sessionId, cancellationToken);
    if (ParseState(session.State) != SessionState.ArmedWaitingForPhysicalStart)
    {
      throw new InvalidOperationException($"Session {sessionId} is not waiting for physical movement.");
    }

    if (startedAt < session.ArmedAtUtc)
    {
      throw new ArgumentOutOfRangeException(nameof(startedAt));
    }

    session.State = SessionState.Running.ToString();
    session.StartedAtUtc = startedAt;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task AppendSampleAsync(
    SessionSample sample,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(sample);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, sample.SessionId, cancellationToken);
    SessionState state = ParseState(session.State);
    if (IsTerminal(state))
    {
      throw new InvalidOperationException($"Cannot append a sample to terminal session {sample.SessionId}.");
    }

    if (!string.Equals(
      session.MetricAlgorithmVersion,
      sample.MetricAlgorithmVersion,
      StringComparison.Ordinal))
    {
      throw new InvalidOperationException("The sample metric algorithm must match the session definition.");
    }

    context.SessionSamples.Add(new SessionSampleEntity
    {
      WorkoutSessionId = sample.SessionId,
      Sequence = sample.Sequence,
      CapturedAtUtc = sample.CapturedAt,
      ElapsedMilliseconds = sample.Elapsed.TotalMilliseconds,
      PlannedSpeedKph = sample.PlannedSpeedKph,
      RequestedSpeedKph = sample.RequestedSpeedKph,
      MeasuredSpeedKph = sample.MeasuredSpeedKph,
      PlannedInclinePercent = sample.PlannedInclinePercent,
      RequestedInclinePercent = sample.RequestedInclinePercent,
      MeasuredInclinePercent = sample.MeasuredInclinePercent,
      HeartRateBpm = sample.HeartRateBpm,
      DistanceKilometers = sample.DistanceKilometers,
      EstimatedCalories = sample.EstimatedKilocalories,
      TelemetryAgeMilliseconds = sample.TelemetryAge.TotalMilliseconds,
      MetricAlgorithmVersion = sample.MetricAlgorithmVersion,
    });
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task AppendEventAsync(
    Guid sessionId,
    SessionEvent sessionEvent,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    ArgumentNullException.ThrowIfNull(sessionEvent);
    RequireUtc(sessionEvent.OccurredAt, nameof(sessionEvent));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, sessionId, cancellationToken);
    if (IsTerminal(ParseState(session.State)))
    {
      throw new InvalidOperationException($"Cannot append an event to terminal session {sessionId}.");
    }

    context.SessionEvents.Add(CreateEventEntity(sessionId, sessionEvent));
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task FinalizeAsync(
    SessionSummary summary,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(summary);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, summary.SessionId, cancellationToken);
    if (session.UserProfileId != summary.UserProfileId ||
        session.WorkoutRevisionId != summary.WorkoutRevisionId)
    {
      throw new InvalidOperationException("The summary does not match the persisted profile and workout revision.");
    }

    if (IsTerminal(ParseState(session.State)))
    {
      throw new InvalidOperationException($"Session {summary.SessionId} is already terminal.");
    }

    session.State = summary.Status.ToString();
    session.StartedAtUtc = summary.StartedAt;
    session.EndedAtUtc = summary.EndedAt;
    session.DurationSeconds = summary.Duration.TotalSeconds;
    session.DistanceKilometers = summary.DistanceKilometers;
    session.EstimatedCalories = summary.EstimatedKilocalories;
    session.AverageHeartRateBpm = summary.AverageHeartRateBpm;
    session.MaximumHeartRateBpm = summary.MaximumHeartRateBpm;
    session.AverageSpeedKph = summary.AverageSpeedKph;
    session.AverageInclinePercent = summary.AverageInclinePercent;
    await context.SaveChangesAsync(cancellationToken);
    if (summary.Status == SessionState.Completed &&
        session.WorkoutProgramRunId is { } runId &&
        session.WorkoutProgramItemId is not null)
    {
      WorkoutProgramRunEntity run = await context.WorkoutProgramRuns
        .SingleAsync(candidate => candidate.Id == runId, cancellationToken);
      if (run.Status != nameof(WorkoutProgramRunStatus.Active))
      {
        throw new InvalidOperationException("The linked workout program run is not active.");
      }

      int totalItems = await context.WorkoutProgramItems.CountAsync(
        item => item.WorkoutProgramRevisionId == run.WorkoutProgramRevisionId,
        cancellationToken);
      int completedItems = await context.WorkoutSessions.AsNoTracking()
        .Where(candidate => candidate.WorkoutProgramRunId == runId &&
          candidate.State == nameof(SessionState.Completed))
        .Select(candidate => candidate.WorkoutProgramItemId)
        .Distinct()
        .CountAsync(cancellationToken);
      if (completedItems == totalItems)
      {
        run.Status = nameof(WorkoutProgramRunStatus.Completed);
        run.EndedAtUtc = summary.EndedAt;
        run.Version++;
        await context.SaveChangesAsync(cancellationToken);
      }
    }

    await transaction.CommitAsync(cancellationToken);
  }

  public async Task SaveDebriefAsync(
    SessionDebrief debrief,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(debrief);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, debrief.SessionId, cancellationToken);
    if (!IsTerminal(ParseState(session.State)))
    {
      throw new InvalidOperationException("A debrief can be saved only after the session ends.");
    }

    session.PerceivedExertion = debrief.PerceivedExertion;
    session.DebriefNote = debrief.Note;
    session.DebriefUpdatedAtUtc = debrief.UpdatedAt;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<StoredWorkoutSession?> FindAsync(
    Guid sessionId,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity? session = await context.WorkoutSessions.AsNoTracking()
      .Include(candidate => candidate.Samples)
      .Include(candidate => candidate.Events)
      .SingleOrDefaultAsync(candidate => candidate.Id == sessionId, cancellationToken);
    return session is null ? null : Map(session);
  }

  public async Task<IReadOnlyList<SessionSummary>> ListSummariesAsync(
    Guid userProfileId,
    int take = 50,
    CancellationToken cancellationToken = default)
  {
    RequireId(userProfileId, nameof(userProfileId));
    if (take is < 1 or > 5_000)
    {
      throw new ArgumentOutOfRangeException(nameof(take));
    }

    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    // SQLite stores DateTimeOffset as its canonical UTC text representation but EF cannot
    // translate ORDER BY for DateTimeOffset. Keep the query bounded in SQLite and order that
    // canonical representation directly rather than loading an unbounded history to the client.
    WorkoutSessionEntity[] sessions = await context.WorkoutSessions
      .FromSqlInterpolated($"""
        SELECT * FROM "WorkoutSessions"
        WHERE "UserProfileId" = {userProfileId}
          AND "StartedAtUtc" IS NOT NULL
          AND "EndedAtUtc" IS NOT NULL
          AND "State" IN ('Completed', 'Stopped', 'Interrupted', 'Faulted')
        ORDER BY "EndedAtUtc" DESC
        LIMIT {take}
        """)
      .AsNoTracking()
      .ToArrayAsync(cancellationToken);
    return sessions.Select(MapSummary).ToArray();
  }

  public async Task<int> InterruptUnfinishedAsync(
    DateTimeOffset interruptedAt,
    string reason,
    CancellationToken cancellationToken = default)
  {
    RequireUtc(interruptedAt, nameof(interruptedAt));
    ArgumentException.ThrowIfNullOrWhiteSpace(reason);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity[] unfinished = await context.WorkoutSessions
      .Where(session => session.State != nameof(SessionState.Completed) &&
        session.State != nameof(SessionState.Stopped) &&
        session.State != nameof(SessionState.Interrupted) &&
        session.State != nameof(SessionState.Faulted))
      .ToArrayAsync(cancellationToken);

    foreach (WorkoutSessionEntity session in unfinished)
    {
      session.State = SessionState.Interrupted.ToString();
      session.EndedAtUtc = interruptedAt;
      if (session.StartedAtUtc is { } startedAt)
      {
        session.DurationSeconds = Math.Max(session.DurationSeconds, (interruptedAt - startedAt).TotalSeconds);
      }

      context.SessionEvents.Add(CreateEventEntity(
        session.Id,
        new SessionInterruptedEvent(reason.Trim(), interruptedAt)));
    }

    await context.SaveChangesAsync(cancellationToken);
    return unfinished.Length;
  }

  private static WorkoutSessionEntity FindRequiredEntity(
    WorkoutSessionEntity? session,
    Guid sessionId) => session ?? throw new KeyNotFoundException($"Session {sessionId} was not found.");

  private static async Task<WorkoutSessionEntity> FindRequiredAsync(
    TreadmillRunnerDbContext context,
    Guid sessionId,
    CancellationToken cancellationToken) => FindRequiredEntity(
      await context.WorkoutSessions.SingleOrDefaultAsync(
        candidate => candidate.Id == sessionId,
        cancellationToken),
      sessionId);

  private static StoredWorkoutSession Map(WorkoutSessionEntity entity)
  {
    SessionDebrief? debrief = entity.DebriefUpdatedAtUtc is { } updatedAt
      ? new SessionDebrief(entity.Id, entity.PerceivedExertion, entity.DebriefNote, updatedAt)
      : null;
    return new StoredWorkoutSession(
      new NewWorkoutSession(
        entity.Id,
        entity.UserProfileId,
        entity.UserProfileName,
        entity.WorkoutRevisionId,
        entity.WorkoutTitle,
        entity.ArmedAtUtc,
        entity.ControllerConfigurationJson,
        entity.MetricAlgorithmVersion,
        new WorkoutSessionSelection(
          Enum.Parse<WorkoutSelectionSource>(entity.SelectionSource),
          entity.WorkoutProgramRunId,
          entity.WorkoutProgramItemId)),
      ParseState(entity.State),
      entity.StartedAtUtc,
      entity.EndedAtUtc,
      TimeSpan.FromSeconds(entity.DurationSeconds),
      entity.DistanceKilometers,
      entity.EstimatedCalories,
      entity.AverageHeartRateBpm,
      entity.MaximumHeartRateBpm,
      entity.AverageSpeedKph,
      entity.AverageInclinePercent,
      debrief,
      entity.Samples.OrderBy(sample => sample.Sequence).Select(MapSample).ToArray(),
      entity.Events.OrderBy(sessionEvent => sessionEvent.OccurredAtUtc).Select(MapEvent).ToArray());
  }

  private static SessionSummary MapSummary(WorkoutSessionEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.UserProfileName,
    entity.WorkoutRevisionId,
    entity.WorkoutTitle,
    ParseState(entity.State),
    entity.StartedAtUtc!.Value,
    entity.EndedAtUtc!.Value,
    TimeSpan.FromSeconds(entity.DurationSeconds),
    entity.DistanceKilometers,
    entity.EstimatedCalories,
    entity.AverageHeartRateBpm,
    entity.MaximumHeartRateBpm,
    entity.AverageSpeedKph,
    entity.AverageInclinePercent);

  private static SessionSample MapSample(SessionSampleEntity entity) => new(
    entity.WorkoutSessionId,
    entity.Sequence,
    entity.CapturedAtUtc,
    TimeSpan.FromMilliseconds(entity.ElapsedMilliseconds),
    entity.PlannedSpeedKph,
    entity.RequestedSpeedKph,
    entity.MeasuredSpeedKph,
    entity.PlannedInclinePercent,
    entity.RequestedInclinePercent,
    entity.MeasuredInclinePercent,
    entity.HeartRateBpm,
    entity.DistanceKilometers,
    entity.EstimatedCalories,
    TimeSpan.FromMilliseconds(entity.TelemetryAgeMilliseconds),
    entity.MetricAlgorithmVersion);

  private static SessionEventEntity CreateEventEntity(Guid sessionId, SessionEvent sessionEvent) => new()
  {
    Id = Guid.NewGuid(),
    WorkoutSessionId = sessionId,
    OccurredAtUtc = sessionEvent.OccurredAt,
    Kind = sessionEvent.EventType,
    DetailsJson = JsonSerializer.Serialize(sessionEvent, sessionEvent.GetType(), EventJsonOptions),
  };

  private static SessionEvent MapEvent(SessionEventEntity entity) => entity.Kind switch
  {
    "manual-speed-override" => DeserializeEvent<ManualSpeedOverrideEvent>(entity),
    "manual-incline-override" => DeserializeEvent<ManualInclineOverrideEvent>(entity),
    "workout-step-transition" => DeserializeEvent<WorkoutStepTransitionEvent>(entity),
    "session-paused" => DeserializeEvent<SessionPausedEvent>(entity),
    "session-resumed" => DeserializeEvent<SessionResumedEvent>(entity),
    "device-disconnected" => DeserializeEvent<DeviceDisconnectedEvent>(entity),
    "device-reconnected" => DeserializeEvent<DeviceReconnectedEvent>(entity),
    "session-warning" => DeserializeEvent<SessionWarningEvent>(entity),
    "control-lease" => DeserializeEvent<ControlLeaseEvent>(entity),
    "session-completed" => DeserializeEvent<SessionCompletedEvent>(entity),
    "session-stopped" => DeserializeEvent<SessionStoppedEvent>(entity),
    "session-interrupted" => DeserializeEvent<SessionInterruptedEvent>(entity),
    "session-faulted" => DeserializeEvent<SessionFaultedEvent>(entity),
    _ => throw new InvalidOperationException($"Stored session event kind '{entity.Kind}' is unsupported."),
  };

  private static TEvent DeserializeEvent<TEvent>(SessionEventEntity entity)
    where TEvent : SessionEvent =>
    JsonSerializer.Deserialize<TEvent>(entity.DetailsJson, EventJsonOptions)
      ?? throw new InvalidOperationException($"Stored event {entity.Id} is invalid.");

  private static SessionState ParseState(string value) =>
    Enum.TryParse(value, ignoreCase: false, out SessionState state)
      ? state
      : throw new InvalidOperationException($"Stored session state '{value}' is invalid.");

  private static bool IsTerminal(SessionState state) => state is
    SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted;

  private static void RequireId(Guid value, string parameterName)
  {
    if (value == Guid.Empty)
    {
      throw new ArgumentException("ID cannot be empty.", parameterName);
    }
  }

  private static void RequireUtc(DateTimeOffset value, string parameterName)
  {
    if (value.Offset != TimeSpan.Zero)
    {
      throw new ArgumentException("Timestamp must be UTC.", parameterName);
    }
  }
}
