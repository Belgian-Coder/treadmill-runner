using System.Threading.Channels;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminSyncWorker(
  IServiceScopeFactory scopeFactory,
  IGarminStore store,
  IGarminProvider provider,
  TimeProvider timeProvider,
  IApplicationMaintenanceState maintenanceState,
  ILogger<GarminSyncWorker> logger) : BackgroundService
{
  internal static readonly TimeSpan ReconcileInterval = TimeSpan.FromMinutes(1);
  internal static readonly TimeSpan LeaseDuration = TimeSpan.FromMinutes(2);
  internal const int MaximumAttempts = 5;
  private readonly Channel<bool> _wake = Channel.CreateBounded<bool>(new BoundedChannelOptions(1)
  {
    FullMode = BoundedChannelFullMode.DropWrite,
    SingleReader = true,
    SingleWriter = false,
  });

  public void Wake() => _wake.Writer.TryWrite(true);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      try
      {
        if (maintenanceState.TryBeginMutation())
        {
          try
          {
            await ProcessAvailableAsync(stoppingToken);
          }
          finally
          {
            maintenanceState.EndMutation();
          }
        }
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        logger.LogWarning(exception, "Garmin synchronization cycle failed; queued work remains durable.");
      }

      using var timeout = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
      timeout.CancelAfter(ReconcileInterval);
      try { await _wake.Reader.ReadAsync(timeout.Token); }
      catch (OperationCanceledException) when (!stoppingToken.IsCancellationRequested) { }
    }
  }

  internal async Task ProcessAvailableAsync(CancellationToken cancellationToken)
  {
    if (!provider.IsConfigured) return;
    for (var processed = 0; processed < 50; processed++)
    {
      DateTimeOffset now = timeProvider.GetUtcNow();
      GarminSyncItemRecord? item = await store.LeaseNextAsync(now, LeaseDuration, MaximumAttempts, cancellationToken);
      if (item is null) return;
      var publishStarted = false;
      try
      {
        GarminAccountLinkRecord link = await store.FindLinkAsync(item.UserProfileId, cancellationToken)
          ?? throw new InvalidOperationException("The Garmin account was disconnected while work was queued.");
        if (link.Id != item.AccountLinkId)
        {
          throw new GarminReconnectRequiredException("The queued Garmin account does not match this runner. Reconnect the account.");
        }
        using IServiceScope scope = scopeFactory.CreateScope();
        GarminConnectionService connection = scope.ServiceProvider.GetRequiredService<GarminConnectionService>();
        string accessToken = connection.UnprotectAccessToken(link.ProtectedAccessToken);
        if (link.AccessTokenExpiresAtUtc <= now.AddMinutes(2))
        {
          string refreshToken = connection.UnprotectRefreshToken(link.ProtectedRefreshToken)
            ?? throw new InvalidOperationException("Garmin authorization expired and no refresh token is available. Reconnect the account.");
          GarminAuthorizationResult refreshed = await provider.RefreshAsync(refreshToken, cancellationToken);
          if (!string.Equals(refreshed.Subject, link.ProviderSubject, StringComparison.Ordinal))
          {
            throw new GarminReconnectRequiredException("Garmin returned a different account during authorization refresh. Reconnect the account.");
          }
          if (!ContainsAllScopes(refreshed.Scopes, link.Scopes))
          {
            throw new GarminReconnectRequiredException("Garmin authorization no longer includes the required training access. Reconnect the account.");
          }
          accessToken = refreshed.AccessToken;
          await store.UpdateTokensAsync(
            link.Id,
            connection.ProtectToken(refreshed.AccessToken),
            refreshed.RefreshToken is null ? link.ProtectedRefreshToken : connection.ProtectToken(refreshed.RefreshToken),
            refreshed.ExpiresAtUtc,
            refreshed.Scopes,
            now,
            cancellationToken);
        }
        publishStarted = true;
        GarminPublishResult result = await provider.PublishAsync(item.Kind, item.PayloadJson, accessToken, item.IdempotencyKey, cancellationToken);
        await store.MarkSyncedAsync(item.Id, result.RemoteId, timeProvider.GetUtcNow(), cancellationToken);
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        throw;
      }
      catch (GarminReconnectRequiredException exception)
      {
        await store.MarkTerminalFailureAsync(item.Id, exception.Message, MaximumAttempts, timeProvider.GetUtcNow(), cancellationToken);
        logger.LogWarning("Garmin {Kind} synchronization requires the runner to reconnect their Garmin account.", item.Kind);
      }
      catch (Exception exception)
      {
        if (publishStarted && !provider.SupportsSafeRetry)
        {
          await store.MarkTerminalFailureAsync(
            item.Id,
            "Garmin may have received this item, so it was not retried automatically. Reconnect or reconcile it using the approved provider workflow.",
            MaximumAttempts,
            timeProvider.GetUtcNow(),
            cancellationToken);
          logger.LogWarning("Garmin {Kind} returned an uncertain publish outcome; automatic retry is blocked.", item.Kind);
        }
        else
        {
          TimeSpan delay = TimeSpan.FromMinutes(Math.Min(60, Math.Pow(2, item.AttemptCount - 1)));
          await store.MarkFailedAsync(item.Id, SafeMessage(exception), timeProvider.GetUtcNow() + delay, timeProvider.GetUtcNow(), cancellationToken);
          logger.LogWarning("Garmin {Kind} synchronization attempt {Attempt} failed; retry is scheduled.", item.Kind, item.AttemptCount);
        }
      }
    }
  }

  private static string SafeMessage(Exception exception) => exception switch
  {
    HttpRequestException => "Garmin Connect is temporarily unavailable.",
    TaskCanceledException => "The Garmin request timed out.",
    InvalidOperationException when exception.Message == "The Garmin account was disconnected while work was queued." => exception.Message,
    InvalidOperationException when exception.Message == "Garmin authorization expired and no refresh token is available. Reconnect the account." => exception.Message,
    _ => "Garmin synchronization could not be completed. Reconnect the account if the problem continues.",
  };

  private static bool ContainsAllScopes(string actual, string required)
  {
    char[] separators = [' ', ','];
    HashSet<string> actualScopes = actual.Split(separators, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
      .ToHashSet(StringComparer.Ordinal);
    return required.Split(separators, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
      .All(actualScopes.Contains);
  }

  private sealed class GarminReconnectRequiredException(string message) : Exception(message);
}
