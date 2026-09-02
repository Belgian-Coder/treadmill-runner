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
    WHERE "LastIndex" = 0 OR
      CAST(("RowIndex" * 239 + "LastIndex" - 1) / "LastIndex" AS INTEGER) <
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

    await AppendSamplesAndRecoveryCheckpointAsync([sample], checkpoint, cancellationToken);
  }

  public async Task AppendSamplesAndRecoveryCheckpointAsync(
    IReadOnlyList<SessionSample> samples,
    SessionRecoveryCheckpoint checkpoint,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(samples);
    ArgumentNullException.ThrowIfNull(checkpoint);
    if (samples.Count == 0)
      throw new ArgumentException("At least one sample is required.", nameof(samples));
    if (samples.Any(sample => sample is null))
      throw new ArgumentException("Samples cannot contain null values.", nameof(samples));
    if (samples.Any(sample => sample.SessionId != checkpoint.SessionId))
      throw new ArgumentException("All samples and the recovery checkpoint must belong to the same session.", nameof(samples));

    RequireId(checkpoint.SessionId, nameof(checkpoint.SessionId));
    RequireUtc(checkpoint.SavedAtUtc, nameof(checkpoint.SavedAtUtc));
    // Serialize and validate before attaching either entity so an invalid checkpoint
    // cannot leave samples tracked for a later caller on this context.
    string checkpointJson = SerializeRecoveryCheckpoint(checkpoint);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    WorkoutSessionEntity session = await FindRequiredAsync(context, checkpoint.SessionId, cancellationToken);
    SessionState state = ParseState(session.State);
    if (IsTerminal(state))
    {
      throw new InvalidOperationException($"Cannot append a sample to terminal session {checkpoint.SessionId}.");
    }

    if (samples.Any(sample => !string.Equals(
      session.MetricAlgorithmVersion,
      sample.MetricAlgorithmVersion,
      StringComparison.Ordinal)))
    {
      throw new InvalidOperationException("The sample metric algorithm must match the session definition.");
    }

    long[] sequences = samples.Select(static sample => sample.Sequence).ToArray();
    Dictionary<long, SessionSampleEntity> existing = await context.SessionSamples
      .Where(sample => sample.WorkoutSessionId == checkpoint.SessionId && sequences.Contains(sample.Sequence))
      .ToDictionaryAsync(sample => sample.Sequence, cancellationToken);
    SessionSample? previous = null;
    foreach (SessionSample sample in samples)
    {
      if (previous is not null &&
          (sample.Sequence <= previous.Sequence ||
           sample.CapturedAt < previous.CapturedAt ||
           sample.Elapsed < previous.Elapsed))
      {
        throw new InvalidOperationException("A sample batch must have increasing sequence, capture time, and elapsed time.");
      }
      if (existing.TryGetValue(sample.Sequence, out SessionSampleEntity? persisted))
      {
        if (MapSample(persisted) != sample)
          throw new InvalidOperationException($"Sample sequence {sample.Sequence} already exists with different telemetry.");
      }
      else
      {
        context.SessionSamples.Add(CreateSampleEntity(sample));
      }
      previous = sample;
    }
    // Keep sample inserts and the conditional checkpoint promotion atomic. The
    // SQL predicate prevents a slower writer with stale state from overwriting a
    // checkpoint committed by another context after this session was loaded.
    await context.SaveChangesAsync(cancellationToken);
    await UpdateRecoveryCheckpointIfNewerAsync(context, checkpoint, checkpointJson, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
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

    SessionSampleEntity[] persistedSamples = await context.SessionSamples.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == summary.SessionId)
      .OrderBy(candidate => candidate.Sequence)
      .ToArrayAsync(cancellationToken);
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      persistedSamples.Select(MapSample).ToArray(),
      SessionCalorieCalculator.ReadWeightKilograms(session.ControllerConfigurationJson));

    session.State = summary.Status.ToString();
    session.StartedAtUtc = summary.StartedAt;
    session.EndedAtUtc = summary.EndedAt;
    session.DurationSeconds = summary.Duration.TotalSeconds;
    session.DistanceKilometers = summary.DistanceKilometers;
    session.EstimatedCalories = statistics.EstimatedKilocalories ?? summary.EstimatedKilocalories;
    session.AverageHeartRateBpm = statistics.AverageHeartRateBpm ?? summary.AverageHeartRateBpm;
    session.MaximumHeartRateBpm = statistics.MaximumHeartRateBpm ?? summary.MaximumHeartRateBpm;
    session.AverageSpeedKph = summary.AverageSpeedKph;
    session.AverageInclinePercent = statistics.AverageInclinePercent ?? summary.AverageInclinePercent;
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

  public async Task<SessionSampleStatistics?> CalculateSampleStatisticsAsync(
    Guid sessionId,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    bool exists = await context.WorkoutSessions.AsNoTracking()
      .AnyAsync(candidate => candidate.Id == sessionId, cancellationToken);
    if (!exists)
    {
      return null;
    }

    WorkoutSessionEntity session = await context.WorkoutSessions.AsNoTracking()
      .SingleAsync(candidate => candidate.Id == sessionId, cancellationToken);
    SessionSampleEntity[] samples = await context.SessionSamples.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == sessionId)
      .OrderBy(candidate => candidate.Sequence)
      .ToArrayAsync(cancellationToken);
    return SessionSampleStatisticsCalculator.Calculate(
      samples.Select(MapSample).ToArray(),
      SessionCalorieCalculator.ReadWeightKilograms(session.ControllerConfigurationJson));
  }

  public async Task<SessionHistoryDetails?> GetHistoryDetailsAsync(
    Guid sessionId,
    IReadOnlyList<HeartRateZone>? heartRateZones = null,
    CancellationToken cancellationToken = default)
  {
    RequireId(sessionId, nameof(sessionId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutSessionEntity? entity = await context.WorkoutSessions.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.Id == sessionId, cancellationToken);
    if (entity is null) return null;
    HeartRateZone[] effectiveZones = heartRateZones?.ToArray() ?? ReadProfileHeartRateZones(entity.ControllerConfigurationJson);

    SessionEventEntity[] events = await context.SessionEvents
      .FromSqlInterpolated($"""
        SELECT *
        FROM "SessionEvents"
        WHERE "WorkoutSessionId" = {sessionId}
        ORDER BY "OccurredAtUtc", "Id"
        """)
      .AsNoTracking()
      .ToArrayAsync(cancellationToken);
    SessionSampleEntity[] samples = await context.SessionSamples.AsNoTracking()
      .Where(candidate => candidate.WorkoutSessionId == sessionId)
      .OrderBy(candidate => candidate.Sequence)
      .ToArrayAsync(cancellationToken);

    entity.Events = events.ToList();
    entity.Samples = samples.ToList();
    StoredWorkoutSession full = Map(entity);
    SessionSample[] mappedSamples = full.Samples.ToArray();
    SessionEvent[] mappedEvents = full.Events.ToArray();
    SessionAnalytics analytics = SessionAnalyticsCalculator.Calculate(
      sessionId,
      mappedSamples,
      mappedEvents,
      effectiveZones);
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      mappedSamples,
      SessionCalorieCalculator.ReadWeightKilograms(entity.ControllerConfigurationJson));
    StoredWorkoutSession displaySession = full with
    {
      Samples = SelectDisplaySamples(mappedSamples),
    };
    return new SessionHistoryDetails(
      new StoredWorkoutSessionDisplay(displaySession, mappedSamples.Length),
      analytics,
      statistics);
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
          "PerceivedExertion", "DebriefNote", "DebriefUpdatedAtUtc", "ActiveSessionKey"
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
    Guid[] legacySessionIds = sessions
      .Where(static session => session.MetricAlgorithmVersion != SessionMetricAlgorithms.EstimatedCaloriesV2 &&
        SessionCalorieCalculator.ReadWeightKilograms(session.ControllerConfigurationJson).HasValue)
      .Select(static session => session.Id)
      .ToArray();
    Dictionary<Guid, double> recalculatedCalories = [];
    if (legacySessionIds.Length > 0)
    {
      SessionSampleEntity[] legacySamples = await context.SessionSamples.AsNoTracking()
        .Where(sample => legacySessionIds.Contains(sample.WorkoutSessionId))
        .OrderBy(sample => sample.WorkoutSessionId)
        .ThenBy(sample => sample.Sequence)
        .ToArrayAsync(cancellationToken);
      foreach (WorkoutSessionEntity session in sessions.Where(candidate => legacySessionIds.Contains(candidate.Id)))
      {
        SessionSample[] samples = legacySamples
          .Where(sample => sample.WorkoutSessionId == session.Id)
          .Select(MapSample)
          .ToArray();
        if (samples.Length > 0 && SessionCalorieCalculator.ReadWeightKilograms(session.ControllerConfigurationJson) is { } weight)
        {
          recalculatedCalories[session.Id] = SessionCalorieCalculator.Calculate(samples, weight);
        }
      }
    }

    return sessions.Select(session => MapSummary(
      session,
      recalculatedCalories.GetValueOrDefault(session.Id, session.EstimatedCalories))).ToArray();
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
    await UpdateRecoveryCheckpointIfNewerAsync(context, checkpoint, json, cancellationToken);
  }

  public async Task<RecoverableWorkoutSession?> FindRecoverableAsync(
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    // EF/SQLite cannot translate DateTimeOffset ordering. SQLite stores these
    // UTC values as canonical text, so the bounded SQL order remains stable.
    WorkoutSessionEntity? entity = await context.WorkoutSessions
      .FromSqlRaw("""
        SELECT *
        FROM "WorkoutSessions"
        WHERE "State" = 'Running'
          AND "SessionOrigin" = 'Hardware'
          AND "RecoveryCheckpointJson" IS NOT NULL
        ORDER BY "RecoveryCheckpointUpdatedAtUtc" DESC, "Id" DESC
        LIMIT 1
        """)
      .AsNoTracking()
      .SingleOrDefaultAsync(cancellationToken);
    if (entity?.RecoveryCheckpointJson is null) return null;
    entity.Samples = await context.SessionSamples.AsNoTracking()
      .Where(sample => sample.WorkoutSessionId == entity.Id)
      .OrderBy(sample => sample.Sequence)
      .ToListAsync(cancellationToken);
    entity.Events = await context.SessionEvents
      .FromSqlInterpolated($"""
        SELECT *
        FROM "SessionEvents"
        WHERE "WorkoutSessionId" = {entity.Id}
        ORDER BY "OccurredAtUtc", "Id"
        """)
      .AsNoTracking()
      .ToListAsync(cancellationToken);
    SessionRecoveryCheckpoint? checkpoint = JsonSerializer.Deserialize<SessionRecoveryCheckpoint>(
      entity.RecoveryCheckpointJson, EventJsonOptions);
    if (checkpoint is null || checkpoint.SessionId != entity.Id)
      throw new InvalidOperationException("The active session recovery checkpoint is invalid.");
    return new RecoverableWorkoutSession(Map(entity), checkpoint);
  }

  public async Task<int> ReconcileActiveSessionsAsync(
    DateTimeOffset reconciledAtUtc,
    CancellationToken cancellationToken = default)
  {
    RequireUtc(reconciledAtUtc, nameof(reconciledAtUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    List<WorkoutSessionEntity> active = await context.WorkoutSessions
      .FromSqlRaw("""
        SELECT *
        FROM "WorkoutSessions"
        WHERE "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume')
        ORDER BY "ArmedAtUtc" DESC, "Id" DESC
        """)
      .ToListAsync(cancellationToken);
    if (active.Count <= 1)
    {
      await transaction.CommitAsync(cancellationToken);
      return 0;
    }

    foreach (WorkoutSessionEntity stale in active.Skip(1))
    {
      stale.State = nameof(SessionState.Interrupted);
      stale.EndedAtUtc = reconciledAtUtc;
      if (stale.StartedAtUtc is { } startedAt)
        stale.DurationSeconds = Math.Max(stale.DurationSeconds, (reconciledAtUtc - startedAt).TotalSeconds);
      context.SessionEvents.Add(CreateEventEntity(
        stale.Id,
        new SessionInterruptedEvent(
          "A newer active session was found during gateway reconciliation.",
          reconciledAtUtc)));
    }
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return active.Count - 1;
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
          .Select(job => new { job.Id, job.Status, job.OperationPhase, job.UpdatedAtUtc })
          .SingleOrDefault(),
      })
      .SingleOrDefaultAsync(cancellationToken);
    if (data is null) return null;

    SessionState state = ParseState(data.Session.State);
    SessionOrigin origin = ParseOrigin(data.Session.SessionOrigin);
    bool terminal = IsTerminal(state) && data.Session.StartedAtUtc is not null && data.Session.EndedAtUtc is not null;
    bool linked = data.Session.WorkoutProgramRunId is not null || data.Session.WorkoutProgramItemId is not null;
    string? garminStatus = data.Garmin?.Status;
    bool pendingReadOnlyWatchSearch = garminStatus == "Pending" && data.Garmin?.OperationPhase == "WatchSearch";
    bool garminSettled = pendingReadOnlyWatchSearch || garminStatus is null or "Confirmed" or "FoundInGarmin" or "Dismissed" or "Failed" or "ReviewRequired";
    bool canDelete = terminal && garminSettled;
    string reason = !terminal
      ? "Only a terminal session can be permanently deleted."
      : !garminSettled
        ? "Wait for the Garmin upload to finish, or acknowledge its unknown outcome, before deleting it."
        : pendingReadOnlyWatchSearch
          ? "This local session can be deleted. Its pending read-only Garmin watch search will be canceled; no remote activity is deleted."
        : garminStatus is "Confirmed" or "FoundInGarmin" or "Dismissed" or "ReviewRequired"
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
      data.Garmin?.OperationPhase ?? string.Empty,
      data.Garmin?.UpdatedAtUtc.ToString("O") ?? string.Empty);
    string revision = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(revisionMaterial)));
    bool remoteMayRemain = garminStatus is "Confirmed" or "FoundInGarmin" or "Dismissed" or "ReviewRequired";
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

  private static IReadOnlyList<SessionSample> SelectDisplaySamples(IReadOnlyList<SessionSample> samples)
  {
    if (samples.Count <= SessionDisplayLimits.MaximumSamples) return samples;
    var selected = new SessionSample[SessionDisplayLimits.MaximumSamples];
    long lastIndex = samples.Count - 1L;
    long lastSlot = SessionDisplayLimits.MaximumSamples - 1L;
    for (var slot = 0; slot < selected.Length; slot++)
      selected[slot] = samples[checked((int)((slot * lastIndex) / lastSlot))];
    return selected;
  }

  private static HeartRateZone[] ReadProfileHeartRateZones(string configurationJson)
  {
    try
    {
      SessionExecutionConfiguration? configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
        configurationJson,
        EventJsonOptions);
      return configuration?.Profile?.HeartRateZones
        .Select(static zone => zone.ToHeartRateZone())
        .ToArray() ?? [];
    }
    catch (JsonException)
    {
      return [];
    }
  }

  private static SessionSummary MapSummary(WorkoutSessionEntity entity, double? estimatedCalories = null) => new(
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
    estimatedCalories ?? entity.EstimatedCalories,
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

  private static Task<int> UpdateRecoveryCheckpointIfNewerAsync(
    TreadmillRunnerDbContext context,
    SessionRecoveryCheckpoint checkpoint,
    string checkpointJson,
    CancellationToken cancellationToken) =>
    context.Database.ExecuteSqlInterpolatedAsync($"""
      UPDATE "WorkoutSessions"
      SET "RecoveryCheckpointJson" = {checkpointJson},
          "RecoveryCheckpointUpdatedAtUtc" = {checkpoint.SavedAtUtc}
      WHERE "Id" = {checkpoint.SessionId}
        AND "State" NOT IN ('Completed', 'Stopped', 'Interrupted', 'Faulted')
        AND (
          "RecoveryCheckpointUpdatedAtUtc" IS NULL OR
          "RecoveryCheckpointUpdatedAtUtc" < {checkpoint.SavedAtUtc} OR
          (
            "RecoveryCheckpointUpdatedAtUtc" = {checkpoint.SavedAtUtc} AND
            COALESCE(CAST(json_extract("RecoveryCheckpointJson", '$.sessionVersion') AS INTEGER), -1) < {checkpoint.SessionVersion}
          )
        )
      """, cancellationToken);

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
