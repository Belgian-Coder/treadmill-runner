using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

public sealed class PolarH10MemoryOptions
{
  public const string SectionName = "Features:PolarH10Memory";
  public bool Enabled { get; set; } = true;
  public int LeaseSeconds { get; set; } = 120;
  public int PollSeconds { get; set; } = 5;
}

public interface IPolarH10MemoryWakeSignal
{
  void Wake();
}

public sealed class PolarH10MemoryWorker(
  IServiceScopeFactory scopeFactory,
  IOptionsMonitor<PolarH10MemoryOptions> options,
  TimeProvider timeProvider,
  IGarminActivityUploadWakeSignal garminWorker,
  PolarH10OperationGate operationGate,
  ILogger<PolarH10MemoryWorker> logger) : BackgroundService, IPolarH10MemoryWakeSignal
{
  private static readonly TimeSpan DefaultAutomaticStartTimeout = TimeSpan.FromSeconds(20);
  private readonly SemaphoreSlim _wake = new(0, 1);
  private DateTimeOffset? _lastRetentionSweepUtc;

  internal TimeSpan AutomaticStartTimeout { get; init; } = DefaultAutomaticStartTimeout;

  public void Wake()
  {
    try { _wake.Release(); }
    catch (SemaphoreFullException)
    {
      logger.LogTrace("A Polar H10 worker wake is already pending.");
    }
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      try { await DrainOneAsync(stoppingToken).ConfigureAwait(false); }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
      catch (Exception exception) { logger.LogError(exception, "Polar H10 memory worker pass failed."); }
      try { await _wake.WaitAsync(TimeSpan.FromSeconds(Math.Clamp(options.CurrentValue.PollSeconds, 1, 60)), stoppingToken).ConfigureAwait(false); }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
    }
  }

  internal async Task DrainOneAsync(CancellationToken cancellationToken)
  {
    await using IAsyncDisposable operationLease = await operationGate.EnterAsync(cancellationToken).ConfigureAwait(false);
    using IServiceScope scope = scopeFactory.CreateScope();
    IPolarH10RecordingStore store = scope.ServiceProvider.GetRequiredService<IPolarH10RecordingStore>();
    IPolarH10MemoryClient client = scope.ServiceProvider.GetRequiredService<IPolarH10MemoryClient>();
    IPolarH10MemoryAccessCoordinator accessCoordinator = scope.ServiceProvider.GetRequiredService<IPolarH10MemoryAccessCoordinator>();
    DateTimeOffset now = timeProvider.GetUtcNow();
    if (_lastRetentionSweepUtc is null || now - _lastRetentionSweepUtc >= TimeSpan.FromHours(1))
    {
      await store.DeleteExpiredRetainedAsync(now.AddDays(-14), cancellationToken).ConfigureAwait(false);
      _lastRetentionSweepUtc = now;
    }
    PolarH10RecordingJob? job = await store.LeaseNextAsync(now, TimeSpan.FromSeconds(Math.Clamp(options.CurrentValue.LeaseSeconds, 30, 900)), cancellationToken).ConfigureAwait(false);
    if (job is null) return;

    bool downloadedWork = job.PayloadSha256 is not null &&
      job.Outcome is PolarH10RecordingOutcome.Downloaded or PolarH10RecordingOutcome.Retryable;
    bool startWork = job.Outcome == PolarH10RecordingOutcome.StartPending ||
      (job.Outcome == PolarH10RecordingOutcome.Retryable && job.StopRequestedAtUtc is null && !downloadedWork);
    bool activeAutomaticSession = job.Origin != "Automatic" ||
      job.SessionId is { } sessionId && await store.IsSessionActiveAsync(sessionId, cancellationToken).ConfigureAwait(false);
    bool previouslyAttemptedAutomaticStart = job.Outcome == PolarH10RecordingOutcome.Retryable ||
      job.Outcome == PolarH10RecordingOutcome.StartPending && job.AttemptCount > 1;
    if (job.Origin == "Automatic" && previouslyAttemptedAutomaticStart &&
        job.StartConfirmedAtUtc is null && job.StopRequestedAtUtc is null && activeAutomaticSession)
    {
      await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, PolarH10RecordingOutcome.ReviewRequired,
        "The H10 recording start was not confirmed. Automatic retries are deferred until the workout ends to preserve live heart-rate telemetry.",
        timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      return;
    }

    if (downloadedWork)
    {
      try
      {
        PolarH10RecordingOutcome merged = await store.MergeDownloadedAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
        if (merged == PolarH10RecordingOutcome.Merged)
          Wake();
        else if (merged == PolarH10RecordingOutcome.DiscardCleanupPending)
          Wake();
        else
        {
          PolarH10RecordingJob unmatched = await store.FindByIdAsync(job.Id, cancellationToken).ConfigureAwait(false)
            ?? throw new InvalidOperationException("The downloaded H10 recording disappeared before retention.");
          await QueueUnmatchedRemovalAsync(store, unmatched, cancellationToken).ConfigureAwait(false);
        }
      }
      catch (Exception exception) when (!cancellationToken.IsCancellationRequested)
      {
        logger.LogWarning(exception, "The downloaded H10 recording could not be merged for {JobId}.", job.Id);
        PolarH10RecordingOutcome outcome = job.AttemptCount >= 5
          ? PolarH10RecordingOutcome.ReviewRequired
          : PolarH10RecordingOutcome.Retryable;
        await store.MarkOutcomeIfVersionAsync(
          job.Id,
          job.Version,
          job.AttemptCount,
          outcome,
          "The verified H10 copy could not be merged into workout History. Retry without reconnecting the sensor.",
          timeProvider.GetUtcNow(),
          cancellationToken).ConfigureAwait(false);
      }
      return;
    }

    bool automaticStartAttempt = startWork && job.Origin == "Automatic" &&
      options.CurrentValue.Enabled && activeAutomaticSession;
    try
    {
      await using IPolarH10MemoryAccessLease access = await accessCoordinator
        .AcquireAsync(job.DeviceEnrollmentId, cancellationToken)
        .ConfigureAwait(false);
      await using IPolarH10MemorySession session = await client
        .OpenAsync(access.EnrollmentId, cancellationToken)
        .ConfigureAwait(false);
      if (startWork)
      {
        if (options.CurrentValue.Enabled && activeAutomaticSession)
        {
          if (automaticStartAttempt)
          {
            using var automaticStartCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            automaticStartCancellation.CancelAfter(AutomaticStartTimeout);
            await StartExactAsync(store, session, job, automaticStartCancellation.Token, cancellationToken).ConfigureAwait(false);
          }
          else
            await StartExactAsync(store, session, job, cancellationToken, cancellationToken).ConfigureAwait(false);
        }
        else
          await ResolveDisallowedStartAsync(store, session, job, options.CurrentValue.Enabled, cancellationToken).ConfigureAwait(false);
      }
      else
        await RecoverExactAsync(store, session, job, cancellationToken).ConfigureAwait(false);
    }
    catch (Exception exception) when (!cancellationToken.IsCancellationRequested)
    {
      logger.LogWarning(exception, "Polar H10 memory operation failed for {JobId}.", job.Id);
      PolarH10RecordingOutcome outcome = automaticStartAttempt
        ? PolarH10RecordingOutcome.ReviewRequired
        : job.Outcome is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending
        ? PolarH10RecordingOutcome.RemovalPending
        : job.AttemptCount >= 5
          ? PolarH10RecordingOutcome.ReviewRequired
          : job.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending
            ? job.Outcome
            : PolarH10RecordingOutcome.Retryable;
      string? error = automaticStartAttempt
        ? "The H10 recording start was not confirmed. Automatic retries are deferred until the workout ends to preserve live heart-rate telemetry."
        : outcome == PolarH10RecordingOutcome.RemovalPending
          ? job.LastError
          : "The exact H10 recording could not be reconciled. Keep the sensor nearby and retry.";
      await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, outcome, error,
        timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    }
  }

  private async Task ResolveDisallowedStartAsync(
    IPolarH10RecordingStore store,
    IPolarH10MemorySession session,
    PolarH10RecordingJob job,
    bool featureEnabled,
    CancellationToken cancellationToken)
  {
    PolarH10DeviceRecordingStatus status = await session.GetStatusAsync(cancellationToken).ConfigureAwait(false);
    if (status.IsRecording && string.Equals(status.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
    {
      await store.QueueStopByIdIfVersionAsync(job.Id, job.Version, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      Wake();
      return;
    }
    if (status.IsRecording)
    {
      await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, PolarH10RecordingOutcome.ReviewRequired,
        "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      return;
    }
    await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, PolarH10RecordingOutcome.NotStarted,
      featureEnabled
        ? "The workout ended before the H10 recording start could be confirmed."
        : "The memory feature was disabled before the H10 recording started.",
      timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    garminWorker.Wake();
  }

  private async Task StartExactAsync(
    IPolarH10RecordingStore store,
    IPolarH10MemorySession session,
    PolarH10RecordingJob job,
    CancellationToken clientCancellationToken,
    CancellationToken outcomeCancellationToken)
  {
    PolarH10StartResult start = await session.StartAsync(
      job.ExerciseId, job.SampleType, job.IntervalSeconds, clientCancellationToken).ConfigureAwait(false);
    PolarH10DeviceRecordingStatus confirmed = start.Status;
    if (!confirmed.IsRecording || !string.Equals(confirmed.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
    {
      if (confirmed.IsRecording)
      {
        await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, PolarH10RecordingOutcome.ReviewRequired,
          "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), outcomeCancellationToken).ConfigureAwait(false);
        return;
      }
      throw new InvalidOperationException("The H10 did not confirm the exact requested exercise.");
    }
    DateTimeOffset confirmedAt = start.StartIssued
      ? start.StartIssuedAtUtc ?? throw new InvalidOperationException("The H10 start time was not captured.")
      : job.StartRequestedAtUtc ?? timeProvider.GetUtcNow();
    await store.MarkRecordingAsync(job.Id, confirmedAt, outcomeCancellationToken).ConfigureAwait(false);
  }

  private async Task RecoverExactAsync(IPolarH10RecordingStore store, IPolarH10MemorySession session, PolarH10RecordingJob job, CancellationToken cancellationToken)
  {
    string exactPath = $"/{job.ExerciseId}/SAMPLES.BPB";
    PolarH10DeviceRecordingStatus status = await session.GetStatusAsync(cancellationToken).ConfigureAwait(false);
    if (status.IsRecording)
    {
      if (!string.Equals(status.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
      {
        await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, PolarH10RecordingOutcome.ReviewRequired,
          "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
        return;
      }
      await session.StopAsync(cancellationToken).ConfigureAwait(false);
    }

    IReadOnlyList<PolarH10RemoteRecording> remote = await session.ListAsync(cancellationToken).ConfigureAwait(false);
    PolarH10RemoteRecording? exact = remote.SingleOrDefault(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal));
    if (job.Outcome is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending)
    {
      if (exact is not null) await session.DeleteAsync(exactPath, cancellationToken).ConfigureAwait(false);
      if ((await session.ListAsync(cancellationToken).ConfigureAwait(false))
        .Any(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal)))
        throw new InvalidOperationException("The exact H10 recording still exists after removal.");
      bool removed = await store.MarkRemoteRemovedIfVersionAsync(job.Id, job.Version, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      if (removed) garminWorker.Wake();
      else Wake();
      return;
    }
    if (job.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
    {
      if (exact is not null)
      {
        await session.DeleteAsync(exactPath, cancellationToken).ConfigureAwait(false);
        if ((await session.ListAsync(cancellationToken).ConfigureAwait(false))
          .Any(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal)))
          throw new InvalidOperationException("The discarded H10 recording still exists after removal.");
      }
      await store.CompleteDiscardCleanupAsync(job.Id, cancellationToken).ConfigureAwait(false);
      garminWorker.Wake();
      return;
    }
    if (exact is null)
    {
      PolarH10RecordingOutcome missing = job.StartConfirmedAtUtc is null ? PolarH10RecordingOutcome.NotStarted : PolarH10RecordingOutcome.Retryable;
      await store.MarkOutcomeIfVersionAsync(job.Id, job.Version, job.AttemptCount, missing,
        missing == PolarH10RecordingOutcome.NotStarted ? "The H10 recording was never started." : "The exact recording is not yet visible on the H10.",
        timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      if (missing == PolarH10RecordingOutcome.NotStarted) garminWorker.Wake();
      return;
    }

    DateTimeOffset startedAt = job.StartConfirmedAtUtc ?? job.StartRequestedAtUtc ?? timeProvider.GetUtcNow();
    PolarH10MemoryRecord recording = await session.FetchAsync(exactPath, startedAt, cancellationToken).ConfigureAwait(false);
    await store.StoreDownloadedAsync(job.Id, recording, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    PolarH10RecordingJob current = await store.FindByIdAsync(job.Id, cancellationToken).ConfigureAwait(false)
      ?? throw new InvalidOperationException("The durable H10 recording disappeared during recovery.");
    if (current.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
    {
      Wake();
      return;
    }
    if (job.Origin == "Manual")
    {
      await QueueUnmatchedRemovalAsync(store, current, cancellationToken).ConfigureAwait(false);
      await TryRemoveVerifiedRemoteAsync(store, session, current.Id, exactPath, cancellationToken).ConfigureAwait(false);
      return;
    }
    PolarH10RecordingOutcome merge = await store.MergeDownloadedAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    if (merge == PolarH10RecordingOutcome.DiscardCleanupPending)
    {
      Wake();
      return;
    }
    if (merge != PolarH10RecordingOutcome.Merged)
    {
      PolarH10RecordingJob unmatched = await store.FindByIdAsync(job.Id, cancellationToken).ConfigureAwait(false)
        ?? throw new InvalidOperationException("The downloaded H10 recording disappeared before retention.");
      await QueueUnmatchedRemovalAsync(store, unmatched, cancellationToken).ConfigureAwait(false);
      await TryRemoveVerifiedRemoteAsync(store, session, unmatched.Id, exactPath, cancellationToken).ConfigureAwait(false);
      return;
    }
    await TryRemoveVerifiedRemoteAsync(store, session, job.Id, exactPath, cancellationToken).ConfigureAwait(false);
    garminWorker.Wake();
  }

  private async Task TryRemoveVerifiedRemoteAsync(
    IPolarH10RecordingStore store,
    IPolarH10MemorySession session,
    Guid jobId,
    string exactPath,
    CancellationToken cancellationToken)
  {
    try
    {
      PolarH10RecordingJob current = await store.FindByIdAsync(jobId, cancellationToken).ConfigureAwait(false)
        ?? throw new InvalidOperationException("The H10 recording disappeared before remote cleanup.");
      await session.DeleteAsync(exactPath, cancellationToken).ConfigureAwait(false);
      if ((await session.ListAsync(cancellationToken).ConfigureAwait(false))
        .Any(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal)))
        throw new InvalidOperationException("The exact H10 recording still exists after removal.");
      if (current.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
      {
        await store.CompleteDiscardCleanupAsync(jobId, cancellationToken).ConfigureAwait(false);
        garminWorker.Wake();
      }
      else if (await store.MarkRemoteRemovedIfVersionAsync(
        jobId,
        current.Version,
        timeProvider.GetUtcNow(),
        cancellationToken).ConfigureAwait(false))
        garminWorker.Wake();
      else
        Wake();
    }
    catch (Exception exception) when (!cancellationToken.IsCancellationRequested)
    {
      logger.LogWarning(exception,
        "The verified local H10 copy was retained, but exact remote cleanup remains pending for {JobId}.", jobId);
      Wake();
    }
  }

  private async Task QueueUnmatchedRemovalAsync(
    IPolarH10RecordingStore store,
    PolarH10RecordingJob job,
    CancellationToken cancellationToken)
  {
    await store.MarkOutcomeIfVersionAsync(
      job.Id,
      job.Version,
      job.AttemptCount,
      PolarH10RecordingOutcome.RemovalPending,
      $"{job.LastError ?? "The recording could not be linked to workout History."} The verified local copy is retained for 14 days.",
      timeProvider.GetUtcNow(),
      cancellationToken).ConfigureAwait(false);
    Wake();
  }
}
