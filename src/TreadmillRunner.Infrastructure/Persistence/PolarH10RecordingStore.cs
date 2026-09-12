using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record PolarH10RecordingJob(
  Guid Id,
  Guid? SessionId,
  Guid? UserProfileId,
  Guid DeviceEnrollmentId,
  string ExerciseId,
  string Origin,
  PolarH10SampleType SampleType,
  int IntervalSeconds,
  PolarH10RecordingOutcome Outcome,
  int AttemptCount,
  DateTimeOffset? LeaseExpiresAtUtc,
  DateTimeOffset? StartRequestedAtUtc,
  DateTimeOffset? StartConfirmedAtUtc,
  DateTimeOffset? StopRequestedAtUtc,
  string? RemotePath,
  string? PayloadSha256,
  int PayloadBytes,
  int MergeCount,
  int RemovalCount,
  string? LastError);

public sealed record PolarH10LocalRecording(
  PolarH10RecordingJob Job,
  DateTimeOffset? StartedAtUtc,
  DateTimeOffset? EndedAtUtc,
  IReadOnlyList<PolarH10HeartRateSample> Samples,
  IReadOnlyList<uint> RrIntervalsMilliseconds);

public interface IPolarH10RecordingStore
{
  Task<PolarH10RecordingJob> EnqueueAsync(Guid? sessionId, Guid? userProfileId, string exerciseId, Guid enrollmentId, string origin, PolarH10SampleType sampleType, int intervalSeconds, DateTimeOffset queuedAtUtc, CancellationToken cancellationToken = default);
  Task<bool> QueueStopAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> QueueDiscardCleanupAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<PolarH10RecordingJob?> FindAsync(Guid sessionId, CancellationToken cancellationToken = default);
  Task<PolarH10RecordingJob?> FindByIdAsync(Guid id, CancellationToken cancellationToken = default);
  Task<PolarH10RecordingJob?> FindByExerciseAsync(Guid enrollmentId, string exerciseId, CancellationToken cancellationToken = default);
  Task<PolarH10RecordingJob?> FindActiveManualAsync(CancellationToken cancellationToken = default);
  Task<bool> IsSessionActiveAsync(Guid sessionId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<PolarH10LocalRecording>> ListLocalAsync(CancellationToken cancellationToken = default);
  Task<PolarH10RecordingJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default);
  Task MarkRecordingAsync(Guid id, DateTimeOffset confirmedAtUtc, CancellationToken cancellationToken = default);
  Task QueueStopByIdAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task StoreDownloadedAsync(Guid id, PolarH10MemoryRecord recording, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<PolarH10RecordingOutcome> MergeDownloadedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkRemoteRemovedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task CompleteDiscardCleanupAsync(Guid id, CancellationToken cancellationToken = default);
  Task RetryAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkOutcomeAsync(Guid id, PolarH10RecordingOutcome outcome, string? error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
}

public sealed class PolarH10RecordingStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IPolarH10RecordingStore
{
  private const int MaximumPayloadBytes = 8 * 1024 * 1024;
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<PolarH10RecordingJob> EnqueueAsync(
    Guid? sessionId, Guid? userProfileId, string exerciseId, Guid enrollmentId, string origin,
    PolarH10SampleType sampleType, int intervalSeconds, DateTimeOffset queuedAtUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(exerciseId);
    if (exerciseId.Length > 64 || exerciseId.Contains('/') || exerciseId.Contains('\\')) throw new ArgumentOutOfRangeException(nameof(exerciseId));
    if (enrollmentId == Guid.Empty) throw new ArgumentException("An enrolled H10 is required.", nameof(enrollmentId));
    if (origin is not ("Automatic" or "Manual")) throw new ArgumentOutOfRangeException(nameof(origin));
    if (sampleType == PolarH10SampleType.HeartRate && intervalSeconds is not (1 or 5)) throw new ArgumentOutOfRangeException(nameof(intervalSeconds));
    if (sampleType == PolarH10SampleType.RrInterval) intervalSeconds = 1;

    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? existing = await context.PolarH10Recordings
      .SingleOrDefaultAsync(row => row.ExerciseId == exerciseId && row.DeviceEnrollmentId == enrollmentId, cancellationToken);
    if (existing is not null) return Map(existing);
    var row = new PolarH10RecordingEntity
    {
      Id = Guid.NewGuid(),
      WorkoutSessionId = sessionId,
      UserProfileId = userProfileId,
      ExerciseId = exerciseId,
      DeviceEnrollmentId = enrollmentId,
      Origin = origin,
      SampleType = sampleType.ToString(),
      SampleIntervalSeconds = intervalSeconds,
      Status = PolarH10RecordingOutcome.StartPending.ToString(),
      StartRequestedAtUtc = queuedAtUtc,
      QueuedAtUtc = queuedAtUtc,
      AvailableAtUtc = queuedAtUtc,
      UpdatedAtUtc = queuedAtUtc,
    };
    context.PolarH10Recordings.Add(row);
    try { await context.SaveChangesAsync(cancellationToken); }
    catch (DbUpdateException)
    {
      context.ChangeTracker.Clear();
      existing = await context.PolarH10Recordings.AsNoTracking()
        .SingleAsync(candidate => candidate.ExerciseId == exerciseId && candidate.DeviceEnrollmentId == enrollmentId, cancellationToken);
      return Map(existing);
    }
    return Map(row);
  }

  public Task<bool> QueueStopAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    QueueSessionStatusAsync(sessionId, PolarH10RecordingOutcome.StopPending, nowUtc, cancellationToken);

  public async Task<bool> QueueDiscardCleanupAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? row = await context.PolarH10Recordings
      .SingleOrDefaultAsync(candidate => candidate.WorkoutSessionId == sessionId && candidate.Origin == "Automatic", cancellationToken);
    if (row is null) return false;
    if (row.Status is "Completed" or "NotStarted")
    {
      context.PolarH10Recordings.Remove(row);
    }
    else
    {
      row.Status = PolarH10RecordingOutcome.DiscardCleanupPending.ToString();
      row.StopRequestedAtUtc ??= nowUtc; row.AvailableAtUtc = nowUtc;
      row.LeaseExpiresAtUtc = null; row.AttemptCount = 0; row.UpdatedAtUtc = nowUtc;
    }
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  public async Task<PolarH10RecordingJob?> FindAsync(Guid sessionId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? row = await context.PolarH10Recordings.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.WorkoutSessionId == sessionId, cancellationToken);
    return row is null ? null : Map(row);
  }

  public async Task<PolarH10RecordingJob?> FindByIdAsync(Guid id, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? row = await context.PolarH10Recordings.AsNoTracking().SingleOrDefaultAsync(candidate => candidate.Id == id, cancellationToken);
    return row is null ? null : Map(row);
  }

  public async Task<PolarH10RecordingJob?> FindByExerciseAsync(Guid enrollmentId, string exerciseId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? row = await context.PolarH10Recordings.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.DeviceEnrollmentId == enrollmentId && candidate.ExerciseId == exerciseId, cancellationToken);
    return row is null ? null : Map(row);
  }

  public async Task<PolarH10RecordingJob?> FindActiveManualAsync(CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity? row = await context.PolarH10Recordings
      .FromSqlRaw("""
        SELECT * FROM PolarH10Recordings
        WHERE Origin = 'Manual' AND Status NOT IN ('Completed','Retained','Skipped','NotStarted')
        ORDER BY julianday(QueuedAtUtc) DESC, Id DESC LIMIT 1
        """)
      .AsNoTracking()
      .SingleOrDefaultAsync(cancellationToken);
    return row is null ? null : Map(row);
  }

  public async Task<bool> IsSessionActiveAsync(Guid sessionId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.WorkoutSessions.AsNoTracking().AnyAsync(candidate => candidate.Id == sessionId &&
      (candidate.State == "ArmedWaitingForPhysicalStart" || candidate.State == "Running" || candidate.State == "PausedWaitingForPhysicalResume"), cancellationToken);
  }

  public async Task<IReadOnlyList<PolarH10LocalRecording>> ListLocalAsync(CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity[] rows = await context.PolarH10Recordings
      .FromSqlRaw("SELECT * FROM PolarH10Recordings WHERE Origin = 'Manual' AND Payload IS NOT NULL ORDER BY julianday(UpdatedAtUtc) DESC LIMIT 100")
      .AsNoTracking().ToArrayAsync(cancellationToken);
    var results = new List<PolarH10LocalRecording>(rows.Length);
    foreach (PolarH10RecordingEntity row in rows)
    {
      PolarH10RecordingSampleEntity[] samples = await context.PolarH10RecordingSamples.AsNoTracking()
        .Where(sample => sample.PolarH10RecordingId == row.Id).OrderBy(sample => sample.Sequence).ToArrayAsync(cancellationToken);
      results.Add(new(Map(row), row.StartedAtUtc, row.EndedAtUtc,
        samples.Where(sample => sample.BeatsPerMinute is not null).Select(sample => new PolarH10HeartRateSample(sample.CapturedAtUtc, sample.BeatsPerMinute)).ToArray(),
        samples.Where(sample => sample.RrIntervalMilliseconds is not null).Select(sample => sample.RrIntervalMilliseconds!.Value).ToArray()));
    }
    return results;
  }

  public async Task<PolarH10RecordingJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default)
  {
    if (leaseDuration <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(leaseDuration));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await context.Database.ExecuteSqlInterpolatedAsync($"""
      UPDATE PolarH10Recordings
      SET LeaseExpiresAtUtc = NULL, AvailableAtUtc = {nowUtc}, UpdatedAtUtc = {nowUtc}
      WHERE LeaseExpiresAtUtc IS NOT NULL AND julianday(LeaseExpiresAtUtc) <= julianday({nowUtc})
        AND Status IN ('StartPending','Recording','StopPending','AwaitingDevice','Downloading','Downloaded','Merged','RemovalPending','DiscardCleanupPending','Retryable')
      """, cancellationToken);
    for (var contentionAttempt = 0; contentionAttempt < 3; contentionAttempt++)
    {
      PolarH10RecordingEntity? row = await context.PolarH10Recordings.FromSqlInterpolated($"""
        SELECT * FROM PolarH10Recordings
        WHERE (Status IN ('StartPending','StopPending','AwaitingDevice','Downloading','Downloaded','Merged','RemovalPending','DiscardCleanupPending','Retryable')
          OR (Status = 'Recording' AND Origin = 'Automatic' AND (WorkoutSessionId IS NULL OR NOT EXISTS (
            SELECT 1 FROM WorkoutSessions AS session
            WHERE session.Id = PolarH10Recordings.WorkoutSessionId
              AND session.State IN ('ArmedWaitingForPhysicalStart','Running','PausedWaitingForPhysicalResume')))))
          AND AttemptCount < 5 AND julianday(AvailableAtUtc) <= julianday({nowUtc}) AND LeaseExpiresAtUtc IS NULL
        ORDER BY julianday(AvailableAtUtc), julianday(QueuedAtUtc), Id LIMIT 1
        """).AsNoTracking().SingleOrDefaultAsync(cancellationToken);
      if (row is null) return null;
      DateTimeOffset expires = nowUtc.Add(leaseDuration);
      int changed = await context.Database.ExecuteSqlInterpolatedAsync($"""
        UPDATE PolarH10Recordings SET LeaseExpiresAtUtc = {expires}, AttemptCount = {row.AttemptCount + 1}, UpdatedAtUtc = {nowUtc}, Version = {row.Version + 1}
        WHERE Id = {row.Id} AND Version = {row.Version} AND LeaseExpiresAtUtc IS NULL
        """, cancellationToken);
      if (changed != 1) continue;
      row.LeaseExpiresAtUtc = expires; row.AttemptCount++; row.Version++;
      return Map(row);
    }
    return null;
  }

  public async Task MarkRecordingAsync(Guid id, DateTimeOffset confirmedAtUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    row.StartConfirmedAtUtc ??= confirmedAtUtc;
    if (row.Status is not ("StopPending" or "DiscardCleanupPending"))
    {
      row.Status = PolarH10RecordingOutcome.Recording.ToString();
      row.LastError = null; row.AttemptCount = 0;
    }
    row.LeaseExpiresAtUtc = null; row.UpdatedAtUtc = confirmedAtUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task QueueStopByIdAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    row.Status = PolarH10RecordingOutcome.StopPending.ToString(); row.StopRequestedAtUtc = nowUtc;
    row.AvailableAtUtc = nowUtc; row.LeaseExpiresAtUtc = null; row.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task StoreDownloadedAsync(Guid id, PolarH10MemoryRecord recording, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    byte[] payload = recording.Payload.ToArray();
    if (payload.Length > MaximumPayloadBytes) throw new InvalidOperationException("The Polar recording payload exceeds 8 MiB.");
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    if (row.Status == PolarH10RecordingOutcome.DiscardCleanupPending.ToString())
    {
      row.StopConfirmedAtUtc ??= nowUtc; row.LeaseExpiresAtUtc = null; row.UpdatedAtUtc = nowUtc;
      await context.SaveChangesAsync(cancellationToken);
      return;
    }
    row.Status = PolarH10RecordingOutcome.Downloaded.ToString(); row.ExternalRecordingId = recording.RecordingId;
    row.SampleType = recording.SampleType.ToString(); row.SampleIntervalSeconds = recording.IntervalSeconds;
    row.RemotePath = recording.RemotePath; row.Payload = payload; row.PayloadBytes = payload.Length;
    row.PayloadSha256 = Convert.ToHexStringLower(SHA256.HashData(payload));
    row.StartedAtUtc = recording.StartedAtUtc; row.EndedAtUtc = recording.EndedAtUtc;
    row.StopConfirmedAtUtc ??= nowUtc; row.LeaseExpiresAtUtc = null; row.LastError = null; row.AttemptCount = 0; row.UpdatedAtUtc = nowUtc;
    context.PolarH10RecordingSamples.RemoveRange(context.PolarH10RecordingSamples.Where(sample => sample.PolarH10RecordingId == id));
    long sequence = 0;
    foreach (PolarH10HeartRateSample sample in recording.Samples)
      context.PolarH10RecordingSamples.Add(new() { PolarH10RecordingId = id, Sequence = sequence++, CapturedAtUtc = sample.CapturedAtUtc, BeatsPerMinute = sample.BeatsPerMinute });
    DateTimeOffset rrTime = recording.StartedAtUtc;
    foreach (uint rr in recording.RrIntervalsMilliseconds)
    {
      context.PolarH10RecordingSamples.Add(new() { PolarH10RecordingId = id, Sequence = sequence++, CapturedAtUtc = rrTime, RrIntervalMilliseconds = rr });
      rrTime = rrTime.AddMilliseconds(rr);
    }
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<PolarH10RecordingOutcome> MergeDownloadedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    if (row.Status == PolarH10RecordingOutcome.DiscardCleanupPending.ToString()) return PolarH10RecordingOutcome.DiscardCleanupPending;
    if (row.Status == PolarH10RecordingOutcome.Merged.ToString()) return PolarH10RecordingOutcome.Merged;
    if (row.Status != PolarH10RecordingOutcome.Downloaded.ToString())
      throw new InvalidOperationException("Only a verified downloaded H10 payload can enter the history merge.");
    if (row.Origin != "Automatic") return PolarH10RecordingOutcome.Retained;
    if (row.WorkoutSessionId is not { } sessionId)
      return await MarkReviewRequiredAsync(context, row, "The workout was removed before H10 recovery completed.", nowUtc, transaction, cancellationToken);
    WorkoutSessionEntity? session = await context.WorkoutSessions.SingleOrDefaultAsync(candidate => candidate.Id == sessionId, cancellationToken);
    if (session?.StartedAtUtc is null || session.EndedAtUtc is null || row.StartConfirmedAtUtc is null)
      return await MarkReviewRequiredAsync(context, row, "The workout or H10 recording window is incomplete.", nowUtc, transaction, cancellationToken);
    if (Math.Abs((row.StartConfirmedAtUtc.Value - session.StartedAtUtc.Value).TotalSeconds) > 30 ||
        row.EndedAtUtc is null || row.EndedAtUtc < session.StartedAtUtc || row.EndedAtUtc > session.EndedAtUtc.Value.AddMinutes(5))
      return await MarkReviewRequiredAsync(context, row, "The H10 recording window does not safely match this workout.", nowUtc, transaction, cancellationToken);

    PolarH10RecordingSampleEntity[] recorded = await context.PolarH10RecordingSamples.AsNoTracking()
      .Where(sample => sample.PolarH10RecordingId == id && sample.BeatsPerMinute != null).OrderBy(sample => sample.Sequence).ToArrayAsync(cancellationToken);
    SessionSampleEntity[] sessionSamples = await context.SessionSamples
      .Where(sample => sample.WorkoutSessionId == sessionId).OrderBy(sample => sample.Sequence).ToArrayAsync(cancellationToken);
    if (recorded.Length == 0 || sessionSamples.Length == 0)
      return await MarkReviewRequiredAsync(context, row, "There are not enough samples to align the H10 recording.", nowUtc, transaction, cancellationToken);

    (TimeSpan Offset, int Matches)[] candidates = Enumerable.Range(-4, 9)
      .Select(step => TimeSpan.FromMilliseconds(step * 500))
      .Select(offset => (offset, Matches: CountMatchingLiveValues(recorded, sessionSamples, offset)))
      .OrderByDescending(candidate => candidate.Matches).ThenBy(candidate => Math.Abs(candidate.offset.TotalMilliseconds)).ToArray();
    if (candidates[0].Matches < 1 || (candidates.Length > 1 && candidates[1].Matches == candidates[0].Matches))
      return await MarkReviewRequiredAsync(context, row, "The H10 recording alignment is ambiguous and requires review.", nowUtc, transaction, cancellationToken);

    int merged = 0;
    foreach (PolarH10RecordingSampleEntity source in recorded)
    {
      DateTimeOffset targetTime = source.CapturedAtUtc + candidates[0].Offset;
      SessionSampleEntity? target = Nearest(sessionSamples, targetTime, TimeSpan.FromMilliseconds(375));
      if (target is not null && target.HeartRateBpm is null) { target.HeartRateBpm = source.BeatsPerMinute; merged++; }
    }
    ushort[] heartRates = sessionSamples.Where(sample => sample.HeartRateBpm is not null).Select(sample => sample.HeartRateBpm!.Value).ToArray();
    session.AverageHeartRateBpm = heartRates.Length == 0 ? null : heartRates.Average(value => (double)value);
    session.MaximumHeartRateBpm = heartRates.Length == 0 ? null : heartRates.Max();
    row.Status = PolarH10RecordingOutcome.Merged.ToString(); row.MergeCount = merged; row.LeaseExpiresAtUtc = null; row.LastError = null; row.UpdatedAtUtc = nowUtc;
    context.SessionEvents.Add(new SessionEventEntity
    {
      Id = Guid.NewGuid(),
      WorkoutSessionId = sessionId,
      OccurredAtUtc = nowUtc,
      Kind = "session-warning",
      DetailsJson = JsonSerializer.Serialize(new SessionWarningEvent("polar-h10-memory-merged", $"Recovered {merged} missing heart-rate samples from the verified Polar H10 recording.", nowUtc), JsonOptions),
    });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return PolarH10RecordingOutcome.Merged;
  }

  public async Task MarkRemoteRemovedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    row.Status = PolarH10RecordingOutcome.Completed.ToString(); row.RemovalCount++;
    row.LeaseExpiresAtUtc = null; row.LastError = null; row.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteDiscardCleanupAsync(Guid id, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    if (row.Status != PolarH10RecordingOutcome.DiscardCleanupPending.ToString())
      throw new InvalidOperationException("Only a discarded workout recording can be removed without retaining its payload.");
    context.PolarH10Recordings.Remove(row);
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task RetryAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    PolarH10RecordingOutcome current = Enum.Parse<PolarH10RecordingOutcome>(row.Status);
    row.Status = current is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending
      ? PolarH10RecordingOutcome.RemovalPending.ToString()
      : row.WorkoutSessionId is null && row.Origin == "Automatic"
        ? PolarH10RecordingOutcome.DiscardCleanupPending.ToString()
      : row.PayloadSha256 is not null && row.Origin == "Automatic"
        ? PolarH10RecordingOutcome.Downloaded.ToString()
        : PolarH10RecordingOutcome.Retryable.ToString();
    row.AttemptCount = 0; row.LeaseExpiresAtUtc = null; row.AvailableAtUtc = nowUtc;
    row.LastError = null; row.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkOutcomeAsync(Guid id, PolarH10RecordingOutcome outcome, string? error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PolarH10RecordingEntity row = await RequiredAsync(context, id, cancellationToken);
    row.Status = outcome.ToString(); row.LastError = error is { Length: > 1000 } ? error[..1000] : error;
    row.LeaseExpiresAtUtc = null; row.UpdatedAtUtc = nowUtc;
    row.AvailableAtUtc = outcome == PolarH10RecordingOutcome.Retryable ? nowUtc.AddSeconds(Math.Min(60, Math.Max(2, row.AttemptCount * 5))) : row.AvailableAtUtc;
    if (outcome == PolarH10RecordingOutcome.Skipped) row.AttemptCount = 0;
    await context.SaveChangesAsync(cancellationToken);
  }

  private async Task<bool> QueueSessionStatusAsync(Guid sessionId, PolarH10RecordingOutcome outcome, DateTimeOffset nowUtc, CancellationToken cancellationToken)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    int changed = await context.PolarH10Recordings
      .Where(row => row.WorkoutSessionId == sessionId && row.Status != "Completed" && row.Status != "Skipped" && row.Status != "NotStarted")
      .ExecuteUpdateAsync(set => set.SetProperty(row => row.Status, outcome.ToString())
        .SetProperty(row => row.StopRequestedAtUtc, nowUtc).SetProperty(row => row.AvailableAtUtc, nowUtc)
        .SetProperty(row => row.LeaseExpiresAtUtc, (DateTimeOffset?)null).SetProperty(row => row.UpdatedAtUtc, nowUtc), cancellationToken);
    return changed == 1;
  }

  private static int CountMatchingLiveValues(PolarH10RecordingSampleEntity[] recorded, SessionSampleEntity[] session, TimeSpan offset)
  {
    int count = 0;
    foreach (PolarH10RecordingSampleEntity source in recorded)
    {
      SessionSampleEntity? target = Nearest(session, source.CapturedAtUtc + offset, TimeSpan.FromMilliseconds(375));
      if (target?.HeartRateBpm == source.BeatsPerMinute) count++;
    }
    return count;
  }

  private static SessionSampleEntity? Nearest(SessionSampleEntity[] samples, DateTimeOffset timestamp, TimeSpan tolerance)
  {
    SessionSampleEntity? nearest = null;
    TimeSpan distance = TimeSpan.MaxValue;
    foreach (SessionSampleEntity sample in samples)
    {
      TimeSpan current = (sample.CapturedAtUtc - timestamp).Duration();
      if (current < distance) { nearest = sample; distance = current; }
    }
    return distance <= tolerance ? nearest : null;
  }

  private static async Task<PolarH10RecordingOutcome> MarkReviewRequiredAsync(
    TreadmillRunnerDbContext context, PolarH10RecordingEntity row, string error, DateTimeOffset nowUtc,
    Microsoft.EntityFrameworkCore.Storage.IDbContextTransaction transaction, CancellationToken cancellationToken)
  {
    row.Status = PolarH10RecordingOutcome.ReviewRequired.ToString(); row.LastError = error; row.LeaseExpiresAtUtc = null; row.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken); await transaction.CommitAsync(cancellationToken);
    return PolarH10RecordingOutcome.ReviewRequired;
  }

  private static async Task<PolarH10RecordingEntity> RequiredAsync(TreadmillRunnerDbContext context, Guid id, CancellationToken cancellationToken) =>
    await context.PolarH10Recordings.SingleOrDefaultAsync(candidate => candidate.Id == id, cancellationToken)
      ?? throw new KeyNotFoundException($"Polar recording {id} was not found.");

  private static PolarH10RecordingJob Map(PolarH10RecordingEntity row) => new(
    row.Id, row.WorkoutSessionId, row.UserProfileId, row.DeviceEnrollmentId, row.ExerciseId, row.Origin,
    Enum.Parse<PolarH10SampleType>(row.SampleType), row.SampleIntervalSeconds, Enum.Parse<PolarH10RecordingOutcome>(row.Status),
    row.AttemptCount, row.LeaseExpiresAtUtc, row.StartRequestedAtUtc, row.StartConfirmedAtUtc, row.StopRequestedAtUtc,
    row.RemotePath, row.PayloadSha256, row.PayloadBytes, row.MergeCount, row.RemovalCount, row.LastError);
}
