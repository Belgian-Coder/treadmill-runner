using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

public sealed class PolarH10MemoryOptions
{
  public const string SectionName = "Features:PolarH10Memory";
  public bool Enabled { get; set; }
  public int LeaseSeconds { get; set; } = 120;
  public int PollSeconds { get; set; } = 5;
}

public sealed class PolarH10MemoryWorker(
  IServiceScopeFactory scopeFactory,
  IOptionsMonitor<PolarH10MemoryOptions> options,
  TimeProvider timeProvider,
  IGarminActivityUploadWakeSignal garminWorker,
  PolarH10OperationGate operationGate,
  ILogger<PolarH10MemoryWorker> logger) : BackgroundService
{
  private readonly SemaphoreSlim _wake = new(0, 1);

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
    DateTimeOffset now = timeProvider.GetUtcNow();
    PolarH10RecordingJob? job = await store.LeaseNextAsync(now, TimeSpan.FromSeconds(Math.Clamp(options.CurrentValue.LeaseSeconds, 30, 900)), cancellationToken).ConfigureAwait(false);
    if (job is null) return;
    try
    {
      if (job.Outcome == PolarH10RecordingOutcome.Downloaded)
      {
        PolarH10RecordingOutcome merged = await store.MergeDownloadedAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
        if (merged == PolarH10RecordingOutcome.Merged) Wake();
      }
      else if (job.Outcome == PolarH10RecordingOutcome.StartPending ||
          (job.Outcome == PolarH10RecordingOutcome.Retryable && job.StopRequestedAtUtc is null))
      {
        bool activeAutomaticSession = job.Origin != "Automatic" ||
          job.SessionId is { } sessionId && await store.IsSessionActiveAsync(sessionId, cancellationToken).ConfigureAwait(false);
        if (options.CurrentValue.Enabled && activeAutomaticSession)
          await StartExactAsync(store, client, job, cancellationToken).ConfigureAwait(false);
        else
          await ResolveDisallowedStartAsync(store, client, job, options.CurrentValue.Enabled, cancellationToken).ConfigureAwait(false);
      }
      else
        await RecoverExactAsync(store, client, job, cancellationToken).ConfigureAwait(false);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogWarning(exception, "Polar H10 memory operation failed for {JobId}.", job.Id);
      PolarH10RecordingOutcome outcome = job.Outcome is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending
        ? PolarH10RecordingOutcome.RemovalPending
        : job.AttemptCount >= 5
          ? PolarH10RecordingOutcome.ReviewRequired
          : job.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending
            ? job.Outcome
            : PolarH10RecordingOutcome.Retryable;
      await store.MarkOutcomeAsync(job.Id, outcome, "The exact H10 recording could not be reconciled. Keep the sensor nearby and retry.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    }
  }

  private async Task ResolveDisallowedStartAsync(
    IPolarH10RecordingStore store,
    IPolarH10MemoryClient client,
    PolarH10RecordingJob job,
    bool featureEnabled,
    CancellationToken cancellationToken)
  {
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    if (status.IsRecording && string.Equals(status.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
    {
      await store.QueueStopByIdAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      Wake();
      return;
    }
    if (status.IsRecording)
    {
      await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.ReviewRequired,
        "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      return;
    }
    await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.NotStarted,
      featureEnabled
        ? "The workout ended before the H10 recording start could be confirmed."
        : "The memory feature was disabled before the H10 recording started.",
      timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    garminWorker.Wake();
  }

  private async Task StartExactAsync(IPolarH10RecordingStore store, IPolarH10MemoryClient client, PolarH10RecordingJob job, CancellationToken cancellationToken)
  {
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    if (status.IsRecording)
    {
      if (!string.Equals(status.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
      {
        await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.ReviewRequired,
          "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
        return;
      }
      await store.MarkRecordingAsync(job.Id, job.StartRequestedAtUtc ?? timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      return;
    }

    await client.StartAsync(job.DeviceEnrollmentId, job.ExerciseId, job.SampleType, job.IntervalSeconds, cancellationToken).ConfigureAwait(false);
    PolarH10DeviceRecordingStatus confirmed = await client.GetStatusAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    if (!confirmed.IsRecording || !string.Equals(confirmed.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
      throw new InvalidOperationException("The H10 did not confirm the exact requested exercise.");
    await store.MarkRecordingAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
  }

  private async Task RecoverExactAsync(IPolarH10RecordingStore store, IPolarH10MemoryClient client, PolarH10RecordingJob job, CancellationToken cancellationToken)
  {
    string exactPath = $"/{job.ExerciseId}/SAMPLES.BPB";
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    if (status.IsRecording)
    {
      if (!string.Equals(status.ExerciseId, job.ExerciseId, StringComparison.Ordinal))
      {
        await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.ReviewRequired,
          "The H10 is recording a different exercise. It was left untouched.", timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
        return;
      }
      await client.StopAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    }

    IReadOnlyList<PolarH10RemoteRecording> remote = await client.ListAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false);
    PolarH10RemoteRecording? exact = remote.SingleOrDefault(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal));
    if (job.Outcome is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending)
    {
      if (exact is not null) await client.DeleteAsync(job.DeviceEnrollmentId, exactPath, cancellationToken).ConfigureAwait(false);
      if ((await client.ListAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false))
        .Any(recording => string.Equals(recording.RemotePath, exactPath, StringComparison.Ordinal)))
        throw new InvalidOperationException("The exact H10 recording still exists after removal.");
      await store.MarkRemoteRemovedAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      garminWorker.Wake();
      return;
    }
    if (job.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
    {
      if (exact is not null)
      {
        await client.DeleteAsync(job.DeviceEnrollmentId, exactPath, cancellationToken).ConfigureAwait(false);
        if ((await client.ListAsync(job.DeviceEnrollmentId, cancellationToken).ConfigureAwait(false))
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
      await store.MarkOutcomeAsync(job.Id, missing,
        missing == PolarH10RecordingOutcome.NotStarted ? "The H10 recording was never started." : "The exact recording is not yet visible on the H10.",
        timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      if (missing == PolarH10RecordingOutcome.NotStarted) garminWorker.Wake();
      return;
    }

    DateTimeOffset startedAt = job.StartConfirmedAtUtc ?? job.StartRequestedAtUtc ?? timeProvider.GetUtcNow();
    PolarH10MemoryRecord recording = await client.FetchAsync(job.DeviceEnrollmentId, exactPath, startedAt, cancellationToken).ConfigureAwait(false);
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
      await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.Retained, null, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
      return;
    }
    PolarH10RecordingOutcome merge = await store.MergeDownloadedAsync(job.Id, timeProvider.GetUtcNow(), cancellationToken).ConfigureAwait(false);
    if (merge != PolarH10RecordingOutcome.Merged) return;
    garminWorker.Wake();
    Wake();
  }
}
