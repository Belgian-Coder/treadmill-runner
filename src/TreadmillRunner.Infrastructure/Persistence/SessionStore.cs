using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Core.Control;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed class SessionStore(
    IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : ISessionStore
{
  private static readonly JsonSerializerOptions EventJsonOptions = new(JsonSerializerDefaults.Web);
  private const string DisplaySampleSql = """
    WITH ranked AS (
      SELECT
        "WorkoutSessionId", "Sequence", "CapturedAtUtc", "ElapsedMilliseconds",
        "PlannedSpeedKph", "RequestedSpeedKph", "MeasuredSpeedKph",
        "PlannedInclinePercent", "RequestedInclinePercent", "MeasuredInclinePercent",
        "HeartRateBpm", "DistanceKilometers", "EstimatedCalories",
        "TelemetryAgeMilliseconds", "MetricAlgorithmVersion",
        ROW_NUMBER() OVER (ORDER BY "Sequence") - 1 AS "RowIndex",
        COUNT(*) OVER () - 1 AS "LastIndex"
      FROM "SessionSamples"
      WHERE "WorkoutSessionId" = {0}
    )
    SELECT "WorkoutSessionId", "Sequence", "CapturedAtUtc", "ElapsedMilliseconds",
      "PlannedSpeedKph", "RequestedSpeedKph", "MeasuredSpeedKph",
      "PlannedInclinePercent", "RequestedInclinePercent", "MeasuredInclinePercent",
      "HeartRateBpm", "DistanceKilometers", "EstimatedCalories",
      "TelemetryAgeMilliseconds", "MetricAlgorithmVersion"
    FROM ranked
    WHERE CAST(("RowIndex" * 239 + "LastIndex" - 1) / "LastIndex" AS INTEGER) <
      CAST((("RowIndex" + 1) * 239 + "LastIndex" - 1) / "LastIndex" AS INTEGER)
    ORDER BY "Sequence"
    """;

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
      SessionOrigin = session.Origin.ToString(),
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

    context.SessionSamples.Add(CreateSampleEntity(sample));
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task AppendSampleAndRecoveryCheckpointAsync(
    SessionSample sample,
    SessionRecoveryCheckpoint checkpoint,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(sample);
    ArgumentNullException.ThrowIfNull(checkpoint);
    if (sample.SessionId != checkpoint.SessionId)
    {
      throw new ArgumentException("The sample and recovery checkpoint must belong to the same session.", nameof(checkpoint));
    }

    RequireId(checkpoint.SessionId, nameof(checkpoint.SessionId));
    RequireUtc(checkpoint.SavedAtUtc, nameof(checkpoint.SavedAtUtc));
    // Serialize and validate before attaching either entity so an invalid checkpoint
    // cannot leave a sample tracked for a later caller on this context.
    string checkpointJson = SerializeRecoveryCheckpoint(checkpoint);
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

    context.SessionSamples.Add(CreateSampleEntity(sample));
    session.RecoveryCheckpointJson = checkpointJson;
    session.RecoveryCheckpointUpdatedAtUtc = checkpoint.SavedAtUtc;
    // A single SaveChanges call makes EF Core enlist both inserts/updates in one
    // provider transaction; a constraint failure rolls back both mutations.
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
      .AsSplitQuery()
      .Include(candidate => candidate.Samples)
      .Include(candidate => candidate.Events)
      .SingleOrDefaultAsync(candidate => candidate.Id == sessionId, cancellationToken);
    return session is null ? null : Map(session);
  }

  public async Task<StoredWorkoutSessionDisplay?> FindDisplayAsync(
    Guid sessionId,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity? session = await context.WorkoutSessions.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.Id == sessionId, cancellationToken);
    if (session is null)
    {
      return null;
    }

    SessionEventEntity[] events = await context.SessionEvents.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == sessionId)
      .ToArrayAsync(cancellationToken);
    int totalSampleCount = await context.SessionSamples.AsNoTracking()
      .CountAsync(candidate => candidate.WorkoutSessionId == sessionId, cancellationToken);
    SessionSampleEntity[] samples = totalSampleCount <= SessionDisplayLimits.MaximumSamples
      ? await context.SessionSamples.AsNoTracking()
        .Where(candidate => candidate.WorkoutSessionId == sessionId)
        .OrderBy(candidate => candidate.Sequence)
        .Take(SessionDisplayLimits.MaximumSamples)
        .ToArrayAsync(cancellationToken)
      : await context.SessionSamples
        .FromSqlRaw(DisplaySampleSql, sessionId)
        .AsNoTracking()
        .ToArrayAsync(cancellationToken);

    session.Samples = samples.ToList();
    session.Events = events.ToList();
    return new StoredWorkoutSessionDisplay(Map(session), totalSampleCount);
  }

  public async Task<SessionAnalytics?> CalculateAnalyticsAsync(
    Guid sessionId,
    IReadOnlyList<HeartRateZone> heartRateZones,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    ArgumentNullException.ThrowIfNull(heartRateZones);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    bool exists = await context.WorkoutSessions.AsNoTracking()
      .AnyAsync(candidate => candidate.Id == sessionId, cancellationToken);
    if (!exists)
    {
      return null;
    }

    var zoneTicks = heartRateZones.ToDictionary(static zone => zone.Number, static _ => 0L);
    var eventKinds = await context.SessionEvents.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == sessionId)
      .Select(candidate => candidate.Kind)
      .ToArrayAsync(cancellationToken);
    var previous = default(AnalyticsSampleProjection?);
    int eligible = 0;
    int adherent = 0;
    await foreach (AnalyticsSampleProjection sample in context.SessionSamples.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == sessionId)
      .OrderBy(candidate => candidate.Sequence)
      .Select(candidate => new AnalyticsSampleProjection(
        candidate.Sequence,
        candidate.CapturedAtUtc,
        candidate.ElapsedMilliseconds,
        candidate.PlannedSpeedKph,
        candidate.MeasuredSpeedKph,
        candidate.PlannedInclinePercent,
        candidate.MeasuredInclinePercent,
        candidate.HeartRateBpm))
      .AsAsyncEnumerable()
      .WithCancellation(cancellationToken))
    {
      if (previous is not null &&
          (sample.Sequence <= previous.Sequence ||
           sample.CapturedAtUtc < previous.CapturedAtUtc ||
           sample.ElapsedMilliseconds < previous.ElapsedMilliseconds))
      {
        throw new InvalidOperationException("Stored samples must have increasing sequence, capture time, and elapsed time.");
      }

      if (previous is not null && sample.HeartRateBpm is { } heartRate)
      {
        HeartRateZone? zone = heartRateZones.SingleOrDefault(candidate =>
          heartRate >= candidate.MinimumBpm && heartRate <= candidate.MaximumBpm);
        if (zone is not null)
        {
          long elapsedTicks = TimeSpan.FromMilliseconds(sample.ElapsedMilliseconds).Ticks -
            TimeSpan.FromMilliseconds(previous.ElapsedMilliseconds).Ticks;
          zoneTicks[zone.Number] = checked(zoneTicks[zone.Number] + elapsedTicks);
        }
      }

      if (sample.PlannedSpeedKph is not null || sample.PlannedInclinePercent is not null)
      {
        eligible++;
        bool speedMatches = sample.PlannedSpeedKph is not { } plannedSpeed ||
          Math.Abs(sample.MeasuredSpeedKph - plannedSpeed) <= SessionMetricAlgorithms.SpeedAdherenceToleranceKph;
        bool inclineMatches = sample.PlannedInclinePercent is not { } plannedIncline ||
          Math.Abs(sample.MeasuredInclinePercent - plannedIncline) <= SessionMetricAlgorithms.InclineAdherenceTolerancePercent;
        if (speedMatches && inclineMatches)
        {
          adherent++;
        }
      }

      previous = sample;
    }

    var zoneDurations = heartRateZones
      .OrderBy(static zone => zone.Number)
      .Select(zone => new HeartRateZoneDuration(zone.Number, zone.Name, TimeSpan.FromTicks(zoneTicks[zone.Number])))
      .ToArray();
    var counts = new SessionEventCounts(
      eventKinds.Count(static kind => kind == "manual-speed-override"),
      eventKinds.Count(static kind => kind == "manual-incline-override"),
      eventKinds.Count(static kind => kind == "session-paused"),
      eventKinds.Count(static kind => kind == "device-disconnected"),
      eventKinds.Count(static kind => kind == "session-warning"));
    return new SessionAnalytics(
      sessionId,
      Array.AsReadOnly(zoneDurations),
      eligible == 0 ? 100 : (double)adherent / eligible * 100,
      SessionMetricAlgorithms.AdherenceV1,
      counts);
  }

  public async Task<IReadOnlyList<SessionSummary>> ListSummariesAsync(
    Guid userProfileId,
    int take = 50,
    CancellationToken cancellationToken = default,
    bool includeSystemTests = false)
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
    string originClause = includeSystemTests ? string.Empty : " AND \"SessionOrigin\" <> 'SystemTest'";
    string sql = """
        SELECT "Id", "UserProfileId", "UserProfileName", "WorkoutRevisionId",
          "WorkoutProgramRunId", "WorkoutProgramItemId", "SelectionSource", "SessionOrigin",
          "WorkoutTitle", "State", "ArmedAtUtc", "StartedAtUtc", "EndedAtUtc",
          "DurationSeconds", "DistanceKilometers", "EstimatedCalories", "AverageHeartRateBpm",
          "MaximumHeartRateBpm", "AverageSpeedKph", "AverageInclinePercent", "MetricAlgorithmVersion",
          "ControllerConfigurationJson", "RecoveryCheckpointJson", "RecoveryCheckpointUpdatedAtUtc",
          "PerceivedExertion", "DebriefNote", "DebriefUpdatedAtUtc"
        FROM "WorkoutSessions"
        WHERE "UserProfileId" = {0}
          AND "StartedAtUtc" IS NOT NULL
          AND "EndedAtUtc" IS NOT NULL
          AND "State" IN ('Completed', 'Stopped', 'Interrupted', 'Faulted')
        """ + originClause + """

        ORDER BY "EndedAtUtc" DESC
        LIMIT {1}
        """;
    WorkoutSessionEntity[] sessions = await context.WorkoutSessions
      .FromSqlRaw(sql, userProfileId, take)
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

  public async Task SaveRecoveryCheckpointAsync(
    SessionRecoveryCheckpoint checkpoint,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(checkpoint);
    RequireId(checkpoint.SessionId, nameof(checkpoint.SessionId));
    RequireUtc(checkpoint.SavedAtUtc, nameof(checkpoint.SavedAtUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, checkpoint.SessionId, cancellationToken);
    if (IsTerminal(ParseState(session.State))) return;
    string json = SerializeRecoveryCheckpoint(checkpoint);
    session.RecoveryCheckpointJson = json;
    session.RecoveryCheckpointUpdatedAtUtc = checkpoint.SavedAtUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<RecoverableWorkoutSession?> FindRecoverableAsync(
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    List<WorkoutSessionEntity> candidates = await context.WorkoutSessions.AsNoTracking()
      .AsSplitQuery()
      .Include(candidate => candidate.Samples)
      .Include(candidate => candidate.Events)
      .Where(candidate => candidate.State == nameof(SessionState.Running) &&
        candidate.SessionOrigin == nameof(SessionOrigin.Hardware) &&
        candidate.RecoveryCheckpointJson != null)
      .ToListAsync(cancellationToken);
    WorkoutSessionEntity? entity = candidates
      .OrderByDescending(candidate => candidate.RecoveryCheckpointUpdatedAtUtc)
      .FirstOrDefault();
    if (entity?.RecoveryCheckpointJson is null) return null;
    SessionRecoveryCheckpoint? checkpoint = JsonSerializer.Deserialize<SessionRecoveryCheckpoint>(
      entity.RecoveryCheckpointJson, EventJsonOptions);
    if (checkpoint is null || checkpoint.SessionId != entity.Id)
      throw new InvalidOperationException("The active session recovery checkpoint is invalid.");
    return new RecoverableWorkoutSession(Map(entity), checkpoint);
  }

  public async Task<HistoryDeletionPreview?> PreviewDeletionAsync(
    Guid sessionId,
    Guid userProfileId,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    RequireId(userProfileId, nameof(userProfileId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await BuildDeletionPreviewAsync(context, sessionId, userProfileId, cancellationToken);
  }

  public async Task<HistoryDeletionResult> DeleteAsync(
    DeleteHistorySessionOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(operation);
    RequireId(operation.OperationId, nameof(operation.OperationId));
    RequireId(operation.SessionId, nameof(operation.SessionId));
    RequireId(operation.UserProfileId, nameof(operation.UserProfileId));
    RequireUtc(operation.RequestedAtUtc, nameof(operation.RequestedAtUtc));
    if (string.IsNullOrWhiteSpace(operation.ExpectedRevision) || operation.ExpectedRevision.Length != 64)
      throw new ArgumentException("A valid deletion preview revision is required.", nameof(operation));
    if (string.IsNullOrWhiteSpace(operation.RequestFingerprint) || operation.RequestFingerprint.Length != 64)
      throw new ArgumentException("A valid request fingerprint is required.", nameof(operation));

    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    var receipt = new PersistenceWriteOperation(
      operation.OperationId,
      "history.delete",
      200,
      "{}",
      operation.RequestedAtUtc,
      operation.RequestFingerprint);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, receipt, cancellationToken);

    HistoryDeletionPreview preview = await BuildDeletionPreviewAsync(
      context,
      operation.SessionId,
      operation.UserProfileId,
      cancellationToken) ?? throw new KeyNotFoundException($"Session {operation.SessionId} was not found.");
    if (!string.Equals(preview.Revision, operation.ExpectedRevision, StringComparison.Ordinal))
      throw new DbUpdateConcurrencyException("The session or its Garmin upload state changed after the deletion preview. Review it again.");
    if (!preview.CanDelete)
      throw new InvalidOperationException(preview.Reason);

    WorkoutSessionEntity entity = await context.WorkoutSessions.SingleAsync(
      candidate => candidate.Id == operation.SessionId && candidate.UserProfileId == operation.UserProfileId,
      cancellationToken);
    var result = new HistoryDeletionResult(
      entity.Id,
      true,
      preview.SampleCount,
      preview.EventCount,
      preview.GarminStatus,
      preview.MaintenanceDistanceImpactKilometers,
      preview.GarminRemoteActivityMayRemain,
      operation.RequestedAtUtc);
    if (ParseOrigin(entity.SessionOrigin) == SessionOrigin.Hardware && entity.DistanceKilometers > 0)
    {
      DateTimeOffset includedBy = entity.EndedAtUtc ?? entity.ArmedAtUtc;
      TreadmillMaintenanceEventEntity[] affectedBaselines = (await context.TreadmillMaintenanceEvents
        .ToArrayAsync(cancellationToken))
        .Where(item => item.CreatedAtUtc >= includedBy)
        .ToArray();
      foreach (TreadmillMaintenanceEventEntity maintenanceEvent in affectedBaselines)
      {
        maintenanceEvent.AppDistanceBaselineKilometers = Math.Max(
          0,
          maintenanceEvent.AppDistanceBaselineKilometers - entity.DistanceKilometers);
      }
    }
    context.WorkoutSessions.Remove(entity);
    PersistenceReceipts.Add(context, receipt with { OutcomeJson = JsonSerializer.Serialize(result, EventJsonOptions) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return result;
  }

  private static async Task<HistoryDeletionPreview?> BuildDeletionPreviewAsync(
    TreadmillRunnerDbContext context,
    Guid sessionId,
    Guid userProfileId,
    CancellationToken cancellationToken)
  {
    var data = await context.WorkoutSessions.AsNoTracking()
      .Where(candidate => candidate.Id == sessionId && candidate.UserProfileId == userProfileId)
      .Select(candidate => new
      {
        Session = candidate,
        SampleCount = candidate.Samples.Count,
        EventCount = candidate.Events.Count,
        Garmin = context.GarminActivityUploadJobs.AsNoTracking()
          .Where(job => job.WorkoutSessionId == candidate.Id)
          .Select(job => new { job.Id, job.Status, job.UpdatedAtUtc })
          .SingleOrDefault(),
      })
      .SingleOrDefaultAsync(cancellationToken);
    if (data is null) return null;

    SessionState state = ParseState(data.Session.State);
    SessionOrigin origin = ParseOrigin(data.Session.SessionOrigin);
    bool terminal = IsTerminal(state) && data.Session.StartedAtUtc is not null && data.Session.EndedAtUtc is not null;
    bool linked = data.Session.WorkoutProgramRunId is not null || data.Session.WorkoutProgramItemId is not null;
    string? garminStatus = data.Garmin?.Status;
    bool garminSettled = garminStatus is null or "Confirmed" or "FoundInGarmin" or "Dismissed" or "Failed";
    bool canDelete = terminal && garminSettled;
    string reason = !terminal
      ? "Only a terminal session can be permanently deleted."
      : !garminSettled
        ? "Wait for the Garmin upload to finish, or acknowledge its unknown outcome, before deleting it."
        : garminStatus is "Confirmed" or "FoundInGarmin" or "Dismissed"
          ? "This local session and its settled Garmin upload record can be deleted. The remote Garmin activity is not deleted."
          : linked
            ? "This session can be permanently deleted. Its training-plan progress will be recalculated from the remaining history."
            : "This session can be permanently deleted.";
    string revisionMaterial = string.Join('|',
      data.Session.Id.ToString("D"),
      data.Session.UserProfileId.ToString("D"),
      data.Session.State,
      data.Session.SessionOrigin,
      data.Session.WorkoutProgramRunId?.ToString("D") ?? string.Empty,
      data.Session.WorkoutProgramItemId?.ToString("D") ?? string.Empty,
      data.SampleCount,
      data.EventCount,
      data.Session.DistanceKilometers.ToString("R", System.Globalization.CultureInfo.InvariantCulture),
      data.Garmin?.Id.ToString("D") ?? string.Empty,
      garminStatus ?? string.Empty,
      data.Garmin?.UpdatedAtUtc.ToString("O") ?? string.Empty);
    string revision = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(revisionMaterial)));
    bool remoteMayRemain = garminStatus is "Confirmed" or "FoundInGarmin" or "Dismissed";
    return new HistoryDeletionPreview(
      data.Session.Id,
      data.Session.UserProfileId,
      data.Session.WorkoutTitle,
      state,
      origin,
      data.SampleCount,
      data.EventCount,
      data.Session.DistanceKilometers,
      origin == SessionOrigin.Hardware ? data.Session.DistanceKilometers : 0,
      linked,
      garminStatus,
      canDelete,
      reason,
      revision,
      remoteMayRemain);
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
          entity.WorkoutProgramItemId),
        ParseOrigin(entity.SessionOrigin)),
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
    entity.AverageInclinePercent,
    ParseOrigin(entity.SessionOrigin));

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

  private static SessionSampleEntity CreateSampleEntity(SessionSample sample) => new()
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
  };

  private static string SerializeRecoveryCheckpoint(SessionRecoveryCheckpoint checkpoint)
  {
    string json = JsonSerializer.Serialize(checkpoint, EventJsonOptions);
    if (Encoding.UTF8.GetByteCount(json) > 16_384)
    {
      throw new InvalidOperationException("The bounded session recovery checkpoint is too large.");
    }

    return json;
  }

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
    "workout-progress-reset" => DeserializeEvent<WorkoutProgressResetEvent>(entity),
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

  private sealed record AnalyticsSampleProjection(
    long Sequence,
    DateTimeOffset CapturedAtUtc,
    double ElapsedMilliseconds,
    double? PlannedSpeedKph,
    double MeasuredSpeedKph,
    double? PlannedInclinePercent,
    double MeasuredInclinePercent,
    ushort? HeartRateBpm);

  private static SessionState ParseState(string value) =>
    Enum.TryParse(value, ignoreCase: false, out SessionState state)
      ? state
      : throw new InvalidOperationException($"Stored session state '{value}' is invalid.");

  private static SessionOrigin ParseOrigin(string value) =>
    Enum.TryParse(value, ignoreCase: false, out SessionOrigin origin)
      ? origin
      : SessionOrigin.Legacy;

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
