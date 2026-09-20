using System.Diagnostics;
using System.Threading.Channels;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.Gateway.Live;

/// <summary>
/// Serializes live-session telemetry persistence outside the authoritative
/// session lock. Producers never wait for the writer or database: a bounded
/// channel is supplemented by one latest-write slot per session when full.
/// Samples captured in the same UTC second are coalesced into one persistence
/// batch, while the newest checkpoint remains authoritative.
/// </summary>
internal sealed class SessionTelemetryWriter(
  IServiceScopeFactory scopeFactory,
  ILogger logger,
  TimeProvider timeProvider,
  Func<SessionTelemetryWrite, CancellationToken, Task<bool>> isCurrent,
  BleDiagnosticJournal? diagnosticJournal = null,
  TimeSpan? telemetryRetryBudget = null)
{
  private const int QueueCapacity = 256;
  private const int MaximumOverflowSessions = 256;
  private const int MaximumBatchSize = 32;
  private static readonly TimeSpan DefaultTelemetryRetryBudget = TimeSpan.FromSeconds(30);
  private readonly object queueGate = new();
  private readonly Dictionary<Guid, SessionTelemetryWrite> overflow = [];
  private readonly Dictionary<Guid, int> pendingWrites = [];
  private readonly Dictionary<Guid, List<TaskCompletionSource>> flushWaiters = [];
  private readonly Dictionary<Guid, Exception> pendingFailures = [];
  private readonly Dictionary<Guid, Exception> terminalFailures = [];
  private readonly TimeSpan persistenceRetryBudget = ValidateRetryBudget(
    telemetryRetryBudget ?? DefaultTelemetryRetryBudget);
  private readonly Channel<SessionTelemetryWrite> writes = Channel.CreateBounded<SessionTelemetryWrite>(
    new BoundedChannelOptions(QueueCapacity)
    {
      SingleReader = true,
      SingleWriter = false,
      FullMode = BoundedChannelFullMode.Wait,
    });
  private SessionTelemetryWrite? lookahead;
  private bool completed;
  private bool canceled;
  private CancellationToken canceledToken;
  private Exception? fatalFailure;

  /// <summary>
  /// Enqueues without waiting for the writer or database. When the channel is
  /// full, only the newest write for each session is retained in the bounded
  /// overflow slots.
  /// </summary>
  public bool TryEnqueue(SessionTelemetryWrite write)
  {
    lock (queueGate)
    {
      if (completed)
      {
        Record(write, "sample-writer-completed-discarded");
        return false;
      }
      if (writes.Writer.TryWrite(write))
      {
        IncrementPendingLocked(write.SessionId);
        Record(write, "sample-enqueued");
        return true;
      }

      if (overflow.TryGetValue(write.SessionId, out SessionTelemetryWrite? replaced))
      {
        overflow[write.SessionId] = write;
        Record(replaced, "sample-overflow-replaced");
        Record(write, "sample-enqueued");
        return true;
      }

      if (overflow.Count >= MaximumOverflowSessions)
      {
        Guid oldest = overflow
          .OrderBy(static pair => pair.Value.Sample.CapturedAt)
          .Select(static pair => pair.Key)
          .First();
        SessionTelemetryWrite displaced = overflow[oldest];
        overflow.Remove(oldest);
        pendingFailures.TryAdd(
          displaced.SessionId,
          new InvalidOperationException("The coalesced telemetry write was displaced by queue saturation."));
        CompletePendingLocked(displaced.SessionId, 1);
        Record(displaced, "sample-overflow-replaced");
        logger.LogWarning("The live-session telemetry queue is saturated; an older coalesced session write was replaced.");
      }

      overflow[write.SessionId] = write;
      IncrementPendingLocked(write.SessionId);
      Record(write, "sample-enqueued");
      return true;
    }
  }

  public Task FlushSessionAsync(Guid sessionId, CancellationToken cancellationToken = default)
  {
    TaskCompletionSource waiter;
    lock (queueGate)
    {
      if (canceled) return Task.FromCanceled(canceledToken);
      if (fatalFailure is not null) return Task.FromException(fatalFailure);
      if (!pendingWrites.TryGetValue(sessionId, out int count) || count == 0)
      {
        return terminalFailures.TryGetValue(sessionId, out Exception? completedFailure)
          ? Task.FromException(completedFailure)
          : Task.CompletedTask;
      }
      if (terminalFailures.TryGetValue(sessionId, out Exception? earlierFailure))
        pendingFailures.TryAdd(sessionId, earlierFailure);
      waiter = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
      if (!flushWaiters.TryGetValue(sessionId, out List<TaskCompletionSource>? waiters))
      {
        waiters = [];
        flushWaiters[sessionId] = waiters;
      }
      waiters.Add(waiter);
    }
    return AwaitFlushWaiterAsync(sessionId, waiter, cancellationToken);
  }

  private async Task AwaitFlushWaiterAsync(
    Guid sessionId,
    TaskCompletionSource waiter,
    CancellationToken cancellationToken)
  {
    try
    {
      await waiter.Task.WaitAsync(cancellationToken);
    }
    finally
    {
      if (!waiter.Task.IsCompleted)
      {
        lock (queueGate)
        {
          if (flushWaiters.TryGetValue(sessionId, out List<TaskCompletionSource>? waiters))
          {
            waiters.Remove(waiter);
            if (waiters.Count == 0) flushWaiters.Remove(sessionId);
          }
        }
      }
    }
  }

  public void Complete()
  {
    lock (queueGate)
    {
      completed = true;
      writes.Writer.TryComplete();
    }
  }

  public async Task RunAsync(CancellationToken cancellationToken)
  {
    try
    {
      while (true)
      {
        SessionTelemetryWrite? first = await ReadNextAsync(cancellationToken);
        if (first is null) return;

        List<SessionTelemetryWrite> batch = await ReadSameSecondBatchAsync(first, cancellationToken);
        PromoteOverflow();

        foreach (IGrouping<TelemetryMetadata, SessionTelemetryWrite> group in batch
          .GroupBy(static write => new TelemetryMetadata(
            write.SessionId,
            write.SessionVersion,
            write.ConnectionGeneration,
            write.AuthorityId)))
        {
          SessionTelemetryWrite[] ordered = group
            .OrderBy(static write => write.Sample.Sequence)
            .ThenBy(static write => write.Sample.CapturedAt)
            .ToArray();
          if (ordered.Length == 0) continue;

          Exception? failure = await PersistBatchAsync(group.Key, ordered, cancellationToken);
          CompletePending(ordered, failure);
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Shutdown cancellation is bounded by the coordinator. A later session
      // can persist a fresh checkpoint after restart.
      CancelAllPending(cancellationToken);
    }
    catch (Exception exception)
    {
      logger.LogError(exception, "The live-session telemetry writer stopped unexpectedly.");
      FailAllPending(exception);
    }
  }

  private async Task<SessionTelemetryWrite?> ReadNextAsync(CancellationToken cancellationToken)
  {
    while (true)
    {
      if (lookahead is not null)
      {
        SessionTelemetryWrite queued = lookahead!;
        lookahead = null;
        return queued;
      }
      if (writes.Reader.TryRead(out SessionTelemetryWrite? first))
        return first;

      if (!await writes.Reader.WaitToReadAsync(cancellationToken))
      {
        lock (queueGate)
        {
          if (overflow.Count == 0) return null;
          SessionTelemetryWrite next = overflow.Values
            .OrderBy(static write => write.Sample.CapturedAt)
            .First();
          overflow.Remove(next.SessionId);
          return next;
        }
      }
    }
  }

  private async Task<List<SessionTelemetryWrite>> ReadSameSecondBatchAsync(
    SessionTelemetryWrite first,
    CancellationToken cancellationToken)
  {
    var batch = new List<SessionTelemetryWrite>(MaximumBatchSize) { first };
    DateTimeOffset secondStart = TruncateToSecond(first.Sample.CapturedAt);
    DateTimeOffset deadline = secondStart.AddSeconds(1);

    while (batch.Count < MaximumBatchSize)
    {
      while (batch.Count < MaximumBatchSize && writes.Reader.TryRead(out SessionTelemetryWrite? next))
      {
        if (next is null) continue;
        if (TruncateToSecond(next.Sample.CapturedAt) != secondStart)
        {
          lookahead = next;
          break;
        }
        batch.Add(next);
      }

      if (lookahead is not null) break;
      if (batch.Count >= MaximumBatchSize) break;
      TimeSpan remaining = deadline - timeProvider.GetUtcNow();
      if (remaining <= TimeSpan.Zero) break;

      using var window = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
      window.CancelAfter(remaining);
      try
      {
        if (!await writes.Reader.WaitToReadAsync(window.Token)) break;
      }
      catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
      {
        break;
      }
    }

    return batch;
  }

  private void PromoteOverflow()
  {
    lock (queueGate)
    {
      foreach (Guid sessionId in overflow.Keys.ToArray())
      {
        if (!writes.Writer.TryWrite(overflow[sessionId])) break;
        overflow.Remove(sessionId);
      }
    }
  }

  private void IncrementPendingLocked(Guid sessionId) =>
    pendingWrites[sessionId] = pendingWrites.GetValueOrDefault(sessionId) + 1;

  private void CompletePending(
    IReadOnlyList<SessionTelemetryWrite> completedWrites,
    Exception? failure)
  {
    lock (queueGate)
    {
      foreach (IGrouping<Guid, SessionTelemetryWrite> group in completedWrites.GroupBy(static write => write.SessionId))
      {
        if (failure is not null) pendingFailures.TryAdd(group.Key, failure);
        CompletePendingLocked(group.Key, group.Count());
      }
    }
  }

  private void CompletePendingLocked(Guid sessionId, int completedCount)
  {
    if (!pendingWrites.TryGetValue(sessionId, out int count)) return;
    int remaining = Math.Max(0, count - completedCount);
    if (remaining > 0)
    {
      pendingWrites[sessionId] = remaining;
      return;
    }

    pendingWrites.Remove(sessionId);
    pendingFailures.Remove(sessionId, out Exception? failure);
    if (failure is not null) terminalFailures.TryAdd(sessionId, failure);
    if (!flushWaiters.Remove(sessionId, out List<TaskCompletionSource>? waiters))
      return;
    foreach (TaskCompletionSource waiter in waiters)
    {
      if (failure is null) waiter.TrySetResult();
      else waiter.TrySetException(failure);
    }
  }

  private void CancelAllPending(CancellationToken cancellationToken)
  {
    lock (queueGate)
    {
      canceled = true;
      canceledToken = cancellationToken;
      completed = true;
      writes.Writer.TryComplete(new OperationCanceledException(cancellationToken));
      pendingWrites.Clear();
      pendingFailures.Clear();
      foreach (List<TaskCompletionSource> waiters in flushWaiters.Values)
        foreach (TaskCompletionSource waiter in waiters)
          waiter.TrySetCanceled(cancellationToken);
      flushWaiters.Clear();
    }
  }

  private void FailAllPending(Exception exception)
  {
    lock (queueGate)
    {
      completed = true;
      fatalFailure = exception;
      writes.Writer.TryComplete(exception);
      pendingWrites.Clear();
      pendingFailures.Clear();
      foreach (List<TaskCompletionSource> waiters in flushWaiters.Values)
        foreach (TaskCompletionSource waiter in waiters)
          waiter.TrySetException(exception);
      flushWaiters.Clear();
    }
  }

  private async Task<Exception?> PersistBatchAsync(
    TelemetryMetadata metadata,
    IReadOnlyList<SessionTelemetryWrite> writesToPersist,
    CancellationToken cancellationToken)
  {
    var attempt = 0;
    long startedAt = Stopwatch.GetTimestamp();
    var retryBudgetExceededLogged = false;
    while (true)
    {
      try
      {
        // Recheck immediately before opening and immediately before invoking
        // the store. A reconnect or authority change while queued invalidates
        // the entire metadata-homogeneous batch.
        if (!await isCurrent(writesToPersist[^1], cancellationToken))
        {
          logger.LogDebug(
            "Discarding stale live-session telemetry for {SessionId}, generation {Generation}, authority {AuthorityId}.",
            metadata.SessionId,
            metadata.ConnectionGeneration,
            metadata.AuthorityId);
          RecordAll(writesToPersist, "sample-stale-generation-discarded");
          return new InvalidOperationException(
            "The telemetry batch no longer belongs to the active session authority.");
        }

        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        if (!await isCurrent(writesToPersist[^1], cancellationToken))
        {
          RecordAll(writesToPersist, "sample-stale-generation-discarded");
          return new InvalidOperationException(
            "The telemetry batch no longer belongs to the active session authority.");
        }
        await store.AppendSamplesAndRecoveryCheckpointAsync(
          writesToPersist.Select(static write => write.Sample).ToArray(),
          writesToPersist[^1].Checkpoint,
          cancellationToken);
        RecordAll(writesToPersist, "sample-committed");
        return null;
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        throw;
      }
      catch (InvalidOperationException exception)
      {
        attempt++;
        if (Stopwatch.GetElapsedTime(startedAt) >= persistenceRetryBudget)
        {
          logger.LogError(
            exception,
            "The live-session telemetry batch for {SessionId} remained nonretryable through its persistence retry budget.",
            metadata.SessionId);
          RecordAll(writesToPersist, "sample-nonretryable-failed", exception.GetType().Name);
          return exception;
        }
        TimeSpan delay = TimeSpan.FromMilliseconds(Math.Min(2_000, 100 * attempt));
        logger.LogWarning(
          exception,
          "The live-session telemetry batch for {SessionId} was rejected; retrying in {DelayMs} ms.",
          metadata.SessionId,
          delay.TotalMilliseconds);
        RecordAll(writesToPersist, "sample-retry", exception.GetType().Name, delay.TotalSeconds);
        await Task.Delay(delay, cancellationToken);
      }
      catch (Exception exception)
      {
        attempt++;
        if (!retryBudgetExceededLogged && Stopwatch.GetElapsedTime(startedAt) >= persistenceRetryBudget)
        {
          logger.LogError(
            exception,
            "The live-session telemetry batch for {SessionId} exceeded its normal persistence retry budget; retaining accepted telemetry and continuing retries.",
            metadata.SessionId);
          RecordAll(writesToPersist, "sample-retry-budget-exceeded", exception.GetType().Name);
          retryBudgetExceededLogged = true;
        }
        TimeSpan delay = TimeSpan.FromMilliseconds(Math.Min(2_000, 100 * attempt));
        logger.LogWarning(
          exception,
          "The live-session telemetry batch for {SessionId} could not be persisted; retrying in {DelayMs} ms.",
          metadata.SessionId,
          delay.TotalMilliseconds);
        RecordAll(writesToPersist, "sample-retry", exception.GetType().Name, delay.TotalSeconds);
        await Task.Delay(delay, cancellationToken);
      }
    }
  }

  private static TimeSpan ValidateRetryBudget(TimeSpan value) =>
    value > TimeSpan.Zero
      ? value
      : throw new ArgumentOutOfRangeException("telemetryRetryBudget");

  private static DateTimeOffset TruncateToSecond(DateTimeOffset value)
  {
    DateTime utc = value.UtcDateTime;
    return new DateTimeOffset(utc.AddTicks(-(utc.Ticks % TimeSpan.TicksPerSecond)), TimeSpan.Zero);
  }

  private void RecordAll(
    IReadOnlyList<SessionTelemetryWrite> writesToRecord,
    string phase,
    string? failure = null,
    double? retrySeconds = null)
  {
    foreach (SessionTelemetryWrite write in writesToRecord)
      Record(write, phase, failure, retrySeconds);
  }

  private void Record(
    SessionTelemetryWrite write,
    string phase,
    string? failure = null,
    double? retrySeconds = null)
  {
    if (write.HeartRateDiagnostic is not { } diagnostic) return;
    diagnosticJournal?.Record(new BleDiagnosticEvent(
      timeProvider.GetUtcNow(),
      diagnostic.EnrollmentId ?? Guid.Empty,
      "HeartRate",
      diagnostic.SourceGeneration,
      phase,
      Failure: failure,
      LastValidAgeSeconds: diagnostic.AgeSeconds,
      RetrySeconds: retrySeconds,
      Quality: diagnostic.Quality,
      ProfileId: diagnostic.ProfileId,
      SessionId: write.SessionId,
      SampleSequence: write.Sample.Sequence,
      HasHeartRate: diagnostic.HasHeartRate,
      Reason: diagnostic.Reason,
      CapturedAtUtc: write.Sample.CapturedAt));
  }

  private sealed record TelemetryMetadata(
    Guid SessionId,
    long SessionVersion,
    long ConnectionGeneration,
    Guid AuthorityId);
}

internal sealed record SessionTelemetryWrite(
  Guid SessionId,
  SessionSample Sample,
  SessionRecoveryCheckpoint Checkpoint,
  long SessionVersion,
  long ConnectionGeneration,
  Guid AuthorityId,
  SessionHeartRateDiagnostic? HeartRateDiagnostic = null);

internal sealed record SessionHeartRateDiagnostic(
  Guid ProfileId,
  Guid? EnrollmentId,
  long SourceGeneration,
  double? AgeSeconds,
  string Quality,
  bool HasHeartRate,
  string Reason);
