using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

/// <summary>
/// Prepares the exact enrolled H10 memory recorder as part of arming a workout.
/// The operation is deliberately synchronous: Prepare only succeeds after the
/// workout-specific recording is confirmed and exclusive memory access is released.
/// </summary>
public sealed class PolarH10AutomaticPreparationService(
  IOptions<PolarH10MemoryOptions> options,
  IPolarH10RecordingStore store,
  IPolarH10MemoryClient client,
  IPolarH10MemoryAccessCoordinator accessCoordinator,
  PolarH10OperationGate operationGate,
  IPolarH10MemoryWakeSignal worker,
  TimeProvider timeProvider)
{
  public async Task PrepareAsync(
    Guid sessionId,
    Guid profileId,
    Guid enrollmentId,
    CancellationToken cancellationToken = default,
    bool replaceExistingRecording = false,
    string? expectedExistingExerciseId = null)
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
        await using IPolarH10MemorySession memory = await client
          .OpenAsync(access.EnrollmentId, cancellationToken)
          .ConfigureAwait(false);

        CancellationToken mutationCancellationToken = cancellationToken;
        PolarH10DeviceRecordingStatus current = await memory.GetStatusAsync(cancellationToken).ConfigureAwait(false);
        if (current.IsRecording &&
            !string.Equals(current.ExerciseId, exerciseId, StringComparison.Ordinal))
        {
          if (!replaceExistingRecording)
            throw new PolarH10ActiveRecordingException(current.ExerciseId);
          if (!string.Equals(current.ExerciseId, expectedExistingExerciseId, StringComparison.Ordinal))
            throw new PolarH10ActiveRecordingException(current.ExerciseId);

          // Once the user confirms this exact recording, finish the mutation transaction even
          // if the browser request disappears. The PFTP transport remains independently bounded,
          // and uncertain mutations are never replayed.
          mutationCancellationToken = CancellationToken.None;

          await RemoveSupersededRecordingAsync(
            memory,
            enrollmentId,
            excludedJobId: null,
            current,
            mutationCancellationToken).ConfigureAwait(false);
        }

        job = await store.EnqueueAsync(
          sessionId,
          profileId,
          exerciseId,
          enrollmentId,
          "Automatic",
          PolarH10SampleType.HeartRate,
          1,
          timeProvider.GetUtcNow(),
          mutationCancellationToken).ConfigureAwait(false);

        PolarH10StartResult start = await memory
          .StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1, mutationCancellationToken)
          .ConfigureAwait(false);
        if (start.Status.IsRecording &&
            !string.Equals(start.Status.ExerciseId, exerciseId, StringComparison.Ordinal))
        {
          if (!replaceExistingRecording)
            throw new PolarH10ActiveRecordingException(start.Status.ExerciseId);
          if (!string.Equals(start.Status.ExerciseId, expectedExistingExerciseId, StringComparison.Ordinal))
            throw new PolarH10ActiveRecordingException(start.Status.ExerciseId);
          mutationCancellationToken = CancellationToken.None;
          await RemoveSupersededRecordingAsync(
            memory,
            job.DeviceEnrollmentId,
            job.Id,
            start.Status,
            mutationCancellationToken).ConfigureAwait(false);
          start = await memory
            .StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1, mutationCancellationToken)
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
      if (exception is PolarH10ActiveRecordingException)
        throw;
      string reason = exception.GetBaseException().Message;
      throw new InvalidOperationException($"Polar H10 memory preparation failed: {reason}", exception);
    }
  }

  private async Task RemoveSupersededRecordingAsync(
    IPolarH10MemorySession memory,
    Guid enrollmentId,
    Guid? excludedJobId,
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
      .FindByExerciseAsync(enrollmentId, staleExerciseId, CancellationToken.None)
      .ConfigureAwait(false);
    PolarH10RemoteRecording? staleRemote = (await memory.ListAsync(cancellationToken).ConfigureAwait(false))
      .SingleOrDefault(item => string.Equals(item.RemotePath, stalePath, StringComparison.Ordinal));
    if (staleRemote is not null &&
        staleJob is not null &&
        staleJob.Id != excludedJobId &&
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

    if (staleJob is not null && staleJob.Id != excludedJobId)
    {
      if (staleJob.Outcome == PolarH10RecordingOutcome.DiscardCleanupPending)
        await store.CompleteDiscardCleanupAsync(staleJob.Id, CancellationToken.None).ConfigureAwait(false);
      else
        worker.Wake();
    }
  }

}

public sealed class PolarH10ActiveRecordingException(string? exerciseId)
  : InvalidOperationException(
    string.IsNullOrWhiteSpace(exerciseId)
      ? "The Polar H10 is already recording an unidentified session."
      : $"The Polar H10 is already recording {exerciseId}.")
{
  public string? ExerciseId { get; } = exerciseId;
}
