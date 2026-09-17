using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

/// <summary>
/// Prepares the exact enrolled H10 memory recorder as part of arming a workout.
/// The operation is deliberately synchronous: Prepare only succeeds after the
/// workout-specific recording is confirmed and normal live HR is fresh again.
/// </summary>
public sealed class PolarH10AutomaticPreparationService(
  IOptions<PolarH10MemoryOptions> options,
  IPolarH10RecordingStore store,
  IPolarH10MemoryClient client,
  IPolarH10MemoryAccessCoordinator accessCoordinator,
  IReadOnlyDeviceCoordinator deviceCoordinator,
  PolarH10OperationGate operationGate,
  IPolarH10MemoryWakeSignal worker,
  TimeProvider timeProvider,
  ILogger<PolarH10AutomaticPreparationService> logger)
{
  private static readonly TimeSpan LiveHeartRateRecoveryTimeout = TimeSpan.FromSeconds(15);

  public async Task PrepareAsync(
    Guid sessionId,
    Guid profileId,
    Guid enrollmentId,
    CancellationToken cancellationToken = default)
  {
    if (!options.Value.Enabled)
      throw new InvalidOperationException("Polar H10 memory recording is disabled.");

    string exerciseId = $"tr-{sessionId:N}";
    PolarH10RecordingJob? job = null;
    try
    {
      await using IAsyncDisposable operationLease = await operationGate.EnterAsync(cancellationToken).ConfigureAwait(false);
      await using (IPolarH10MemoryAccessLease access = await accessCoordinator
        .AcquireAsync(enrollmentId, cancellationToken)
        .ConfigureAwait(false))
      {
        job = await store.EnqueueAsync(
          sessionId,
          profileId,
          exerciseId,
          enrollmentId,
          "Automatic",
          PolarH10SampleType.HeartRate,
          1,
          timeProvider.GetUtcNow(),
          cancellationToken).ConfigureAwait(false);

        await using IPolarH10MemorySession memory = await client
          .OpenAsync(access.EnrollmentId, cancellationToken)
          .ConfigureAwait(false);

        PolarH10StartResult start = await memory
          .StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1, cancellationToken)
          .ConfigureAwait(false);
        if (start.Status.IsRecording &&
            !string.Equals(start.Status.ExerciseId, exerciseId, StringComparison.Ordinal))
        {
          await RemoveSupersededRecordingAsync(memory, job, start.Status, cancellationToken).ConfigureAwait(false);
          start = await memory
            .StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1, cancellationToken)
            .ConfigureAwait(false);
        }

        if (!start.Status.IsRecording ||
            !string.Equals(start.Status.ExerciseId, exerciseId, StringComparison.Ordinal))
          throw new InvalidOperationException("The H10 did not confirm the workout-specific memory recording.");

        DateTimeOffset confirmedAt = start.StartIssued
          ? start.StartIssuedAtUtc ?? throw new InvalidOperationException("The H10 start time was not captured.")
          : job.StartRequestedAtUtc ?? timeProvider.GetUtcNow();
        await store.MarkRecordingAsync(job.Id, confirmedAt, CancellationToken.None).ConfigureAwait(false);
      }

      await WaitForLiveHeartRateAsync(profileId, enrollmentId, cancellationToken).ConfigureAwait(false);
    }
    catch (Exception exception)
    {
      if (job is not null)
      {
        await store.QueueDiscardCleanupAsync(sessionId, timeProvider.GetUtcNow(), CancellationToken.None).ConfigureAwait(false);
        worker.Wake();
      }

      if (exception is OperationCanceledException && cancellationToken.IsCancellationRequested)
        throw;
      throw new InvalidOperationException(
        "The workout was not prepared because Polar H10 memory recording or live HR recovery could not be confirmed.",
        exception);
    }
  }

  private async Task RemoveSupersededRecordingAsync(
    IPolarH10MemorySession memory,
    PolarH10RecordingJob requestedJob,
    PolarH10DeviceRecordingStatus current,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(current.ExerciseId))
      throw new InvalidOperationException("The H10 reported an active recording without an exact exercise identifier.");

    string staleExerciseId = current.ExerciseId;
    string stalePath = $"/{staleExerciseId}/SAMPLES.BPB";
    await memory.StopAsync(cancellationToken).ConfigureAwait(false);
    PolarH10DeviceRecordingStatus stopped = await memory.GetStatusAsync(cancellationToken).ConfigureAwait(false);
    if (stopped.IsRecording)
      throw new InvalidOperationException("The existing H10 recording did not stop; the new workout recording was not started.");

    PolarH10RecordingJob? staleJob = await store
      .FindByExerciseAsync(requestedJob.DeviceEnrollmentId, staleExerciseId, CancellationToken.None)
      .ConfigureAwait(false);
    PolarH10RemoteRecording? staleRemote = (await memory.ListAsync(cancellationToken).ConfigureAwait(false))
      .SingleOrDefault(item => string.Equals(item.RemotePath, stalePath, StringComparison.Ordinal));
    if (staleRemote is not null &&
        staleJob is not null &&
        staleJob.Id != requestedJob.Id &&
        staleJob.Outcome != PolarH10RecordingOutcome.DiscardCleanupPending &&
        string.IsNullOrWhiteSpace(staleJob.PayloadSha256))
    {
      DateTimeOffset startedAt = staleJob.StartConfirmedAtUtc ?? staleJob.StartRequestedAtUtc
        ?? throw new InvalidOperationException("The superseded H10 recording has no safe start-time anchor.");
      PolarH10MemoryRecord recording = await memory.FetchAsync(stalePath, startedAt, cancellationToken).ConfigureAwait(false);
      await store.StoreDownloadedAsync(staleJob.Id, recording, timeProvider.GetUtcNow(), CancellationToken.None).ConfigureAwait(false);
    }
    if (staleRemote is not null)
      await memory.DeleteAsync(stalePath, cancellationToken).ConfigureAwait(false);
    if ((await memory.ListAsync(cancellationToken).ConfigureAwait(false))
      .Any(item => string.Equals(item.RemotePath, stalePath, StringComparison.Ordinal)))
      throw new InvalidOperationException("The superseded H10 recording still exists after exact-path removal.");

    if (staleJob is not null && staleJob.Id != requestedJob.Id)
    {
      if (staleJob.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
        await store.CompleteDiscardCleanupAsync(staleJob.Id, CancellationToken.None).ConfigureAwait(false);
      else
        worker.Wake();
    }
  }

  private async Task WaitForLiveHeartRateAsync(
    Guid profileId,
    Guid enrollmentId,
    CancellationToken cancellationToken)
  {
    DateTimeOffset deadline = timeProvider.GetUtcNow() + LiveHeartRateRecoveryTimeout;
    while (timeProvider.GetUtcNow() < deadline)
    {
      HeartRateSourceSnapshot? source = deviceCoordinator.CurrentForProfile(profileId).HeartRateSources?
        .SingleOrDefault(candidate => candidate.EnrollmentId == enrollmentId);
      if (source is not null &&
          source.IsFresh(timeProvider.GetUtcNow(), TimeSpan.FromSeconds(5)) &&
          source.ContactState != HeartRateContactState.NotDetected)
        return;

      await Task.Delay(TimeSpan.FromMilliseconds(250), timeProvider, cancellationToken).ConfigureAwait(false);
    }

    logger.LogWarning(
      "The H10 memory recording was confirmed, but live HR did not recover within {TimeoutSeconds} seconds.",
      LiveHeartRateRecoveryTimeout.TotalSeconds);
    throw new TimeoutException("Normal live H10 heart-rate telemetry did not recover after memory preparation.");
  }
}
