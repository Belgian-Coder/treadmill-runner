using Microsoft.AspNetCore.SignalR;
using Microsoft.Extensions.Logging;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Gateway.Hubs;

namespace TreadmillRunner.Gateway.Live;

/// <summary>
/// Publishes only the newest live/session snapshot. SignalR client backpressure
/// cannot delay the authoritative session loop. A terminal session snapshot is
/// retained independently of the latest nonterminal snapshot until delivered.
/// </summary>
internal sealed class LatestLiveSnapshotBroadcaster(
  IHubContext<LiveHub> hubContext,
  ILogger logger,
  TimeProvider timeProvider)
{
  private static readonly TimeSpan InitialRetryDelay = TimeSpan.FromMilliseconds(100);
  private static readonly TimeSpan MaximumRetryDelay = TimeSpan.FromSeconds(2);
  private readonly object sync = new();
  private readonly SemaphoreSlim signal = new(0, 1);
  private LiveSnapshot? latestLive;
  private ActiveSessionSnapshot? latestSession;
  private ActiveSessionSnapshot? pendingTerminalSession;
  private bool completed;
  private int retryAttempt;
  private DateTimeOffset retryNotBeforeUtc;

  public void Publish(LiveSnapshot? live, ActiveSessionSnapshot? session)
  {
    lock (sync)
    {
      if (completed) return;
      if (live is not null) latestLive = live;
      if (session is not null)
      {
        if (IsTerminal(session.Live.SessionState))
          pendingTerminalSession = session;
        else
          latestSession = session;
      }
    }
    TrySignal();
  }

  public void Complete()
  {
    lock (sync) completed = true;
    TrySignal();
  }

  public async Task RunAsync(CancellationToken cancellationToken)
  {
    try
    {
      while (true)
      {
        await signal.WaitAsync(cancellationToken);
        TimeSpan retryDelay;
        lock (sync)
          retryDelay = retryNotBeforeUtc - timeProvider.GetUtcNow();
        if (retryDelay > TimeSpan.Zero)
          await Task.Delay(retryDelay, cancellationToken);

        LiveSnapshot? live;
        ActiveSessionSnapshot? terminal;
        ActiveSessionSnapshot? session;
        lock (sync)
        {
          live = latestLive;
          terminal = pendingTerminalSession;
          session = latestSession;
          latestLive = null;
          pendingTerminalSession = null;
          latestSession = null;
          if (completed && live is null && terminal is null && session is null) return;
        }

        bool liveSent = live is null || await TrySendAsync("snapshot", live, cancellationToken);
        bool terminalSent = terminal is null || await TrySendAsync("sessionSnapshot", terminal, cancellationToken);
        bool sessionSent = session is null || await TrySendAsync("sessionSnapshot", session, cancellationToken);
        bool allSent = liveSent && terminalSent && sessionSent;

        lock (sync)
        {
          if (allSent)
          {
            retryAttempt = 0;
            retryNotBeforeUtc = DateTimeOffset.MinValue;
          }
          else
          {
            // Requeue only the captured values that were not replaced by a
            // newer publication while SignalR was running. Terminal and latest
            // nonterminal state are intentionally independent slots.
            if (!terminalSent && terminal is not null && pendingTerminalSession is null)
              pendingTerminalSession = terminal;
            if (!sessionSent && session is not null && latestSession is null)
              latestSession = session;
            if (!liveSent && live is not null && latestLive is null)
              latestLive = live;
            retryAttempt = Math.Min(retryAttempt + 1, 8);
            double multiplier = Math.Pow(2, retryAttempt - 1);
            retryNotBeforeUtc = timeProvider.GetUtcNow() + TimeSpan.FromMilliseconds(
              Math.Min(MaximumRetryDelay.TotalMilliseconds, InitialRetryDelay.TotalMilliseconds * multiplier));
          }
        }

        if (!allSent) TrySignal();
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Host shutdown owns cancellation. The authoritative loop remains
      // independent of SignalR publication latency.
    }
  }

  private async Task<bool> TrySendAsync(
    string method,
    object payload,
    CancellationToken cancellationToken)
  {
    try
    {
      await hubContext.Clients.All.SendAsync(method, payload, cancellationToken);
      return true;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogWarning(exception, "Latest live snapshot publication failed; newer state remains authoritative and will retry with backoff.");
      return false;
    }
  }

  private void TrySignal()
  {
    try { signal.Release(); }
    catch (SemaphoreFullException) { }
  }

  private static bool IsTerminal(TreadmillRunner.Core.Sessions.SessionState state) =>
    state is
      TreadmillRunner.Core.Sessions.SessionState.Completed or
      TreadmillRunner.Core.Sessions.SessionState.Stopped or
      TreadmillRunner.Core.Sessions.SessionState.Interrupted or
      TreadmillRunner.Core.Sessions.SessionState.Faulted;
}
