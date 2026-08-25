using System.Threading.Channels;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using TreadmillRunner.Core.Sessions;

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
  Func<SessionTelemetryWrite, CancellationToken, Task<bool>> isCurrent)
{
  private const int QueueCapacity = 256;
  private const int MaximumOverflowSessions = 256;
  private const int MaximumBatchSize = 32;
  private readonly object queueGate = new();
  private readonly Dictionary<Guid, SessionTelemetryWrite> overflow = [];
  private readonly Channel<SessionTelemetryWrite> writes = Channel.CreateBounded<SessionTelemetryWrite>(
    new BoundedChannelOptions(QueueCapacity)
    {
      SingleReader = true,
      SingleWriter = false,
      FullMode = BoundedChannelFullMode.Wait,
    });
  private SessionTelemetryWrite? lookahead;
  private bool completed;

  /// <summary>
  /// Enqueues without waiting for the writer or database. When the channel is
  /// full, only the newest write for each session is retained in the bounded
  /// overflow slots.
  /// </summary>
  public bool TryEnqueue(SessionTelemetryWrite write)
  {
    lock (queueGate)
    {
      if (completed) return false;
      if (writes.Writer.TryWrite(write)) return true;

      if (overflow.ContainsKey(write.SessionId))
      {
        overflow[write.SessionId] = write;
        return true;
      }

      if (overflow.Count >= MaximumOverflowSessions)
      {
        Guid oldest = overflow
          .OrderBy(static pair => pair.Value.Sample.CapturedAt)
          .Select(static pair => pair.Key)
          .First();
        overflow.Remove(oldest);
        logger.LogWarning("The live-session telemetry queue is saturated; an older coalesced session write was replaced.");
      }

      overflow[write.SessionId] = write;
      return true;
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

          await PersistBatchAsync(group.Key, ordered, cancellationToken);
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Shutdown cancellation is bounded by the coordinator. A later session
      // can persist a fresh checkpoint after restart.
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

  private async Task PersistBatchAsync(
    TelemetryMetadata metadata,
    IReadOnlyList<SessionTelemetryWrite> writesToPersist,
    CancellationToken cancellationToken)
  {
    var attempt = 0;
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
          return;
        }

        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        if (!await isCurrent(writesToPersist[^1], cancellationToken)) return;
        await store.AppendSamplesAndRecoveryCheckpointAsync(
          writesToPersist.Select(static write => write.Sample).ToArray(),
          writesToPersist[^1].Checkpoint,
          cancellationToken);
        return;
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        throw;
      }
      catch (InvalidOperationException exception)
      {
        // A session can become terminal while a queued sample is draining. The
        // terminal summary/checkpoint is authoritative; retrying this batch
        // forever would block every later session in the single writer.
        logger.LogWarning(
          exception,
          "The live-session telemetry batch for {SessionId} was no longer applicable; it was discarded after the session state changed.",
          metadata.SessionId);
        return;
      }
      catch (Exception exception)
      {
        attempt++;
        TimeSpan delay = TimeSpan.FromMilliseconds(Math.Min(2_000, 100 * attempt));
        logger.LogWarning(
          exception,
          "The live-session telemetry batch for {SessionId} could not be persisted; retrying in {DelayMs} ms.",
          metadata.SessionId,
          delay.TotalMilliseconds);
        await Task.Delay(delay, cancellationToken);
      }
    }
  }

  private static DateTimeOffset TruncateToSecond(DateTimeOffset value)
  {
    DateTime utc = value.UtcDateTime;
    return new DateTimeOffset(utc.AddTicks(-(utc.Ticks % TimeSpan.TicksPerSecond)), TimeSpan.Zero);
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
  Guid AuthorityId);
