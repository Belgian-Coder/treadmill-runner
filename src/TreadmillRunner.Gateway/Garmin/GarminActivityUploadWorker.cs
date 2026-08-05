using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Exports;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminActivityUploadWorker(
  IServiceScopeFactory scopeFactory,
  IGarminActivityUploadStore store,
  IGarminActivityAdapter adapter,
  GarminActivityConnectionService connections,
  TimeProvider timeProvider,
  IApplicationMaintenanceState maintenanceState,
  ILogger<GarminActivityUploadWorker> logger) : BackgroundService
{
  private readonly SemaphoreSlim _wake = new(0, 1);

  public void Wake()
  {
    try { _wake.Release(); }
    catch (SemaphoreFullException)
    {
      // One pending wake signal is sufficient; the durable queue is reconciled every pass.
    }
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      try
      {
        if (maintenanceState.TryBeginMutation())
        {
          try { await DrainAsync(stoppingToken); }
          finally { maintenanceState.EndMutation(); }
        }
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
      catch (Exception exception) { logger.LogError(exception, "The unsupported Garmin activity upload worker pass failed."); }
      try { await _wake.WaitAsync(TimeSpan.FromMinutes(1), stoppingToken); }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
    }
  }

  private async Task DrainAsync(CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    await store.ReconcileCompletedSessionsAsync(now, cancellationToken);
    while (await store.LeaseNextAsync(timeProvider.GetUtcNow(), TimeSpan.FromMinutes(2), cancellationToken) is { } job)
      await ProcessOneAsync(job, cancellationToken);
  }

  internal async Task ProcessOneAsync(GarminActivityUploadJob job, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    GarminActivityUploadAccount? account = await store.FindAccountAsync(job.UserProfileId, cancellationToken);
    if (account is null || !account.Enabled)
    {
      await store.MarkFailedAsync(job.Id, "The runner's unsupported Garmin upload is disconnected or disabled.", true, now, now, cancellationToken);
      return;
    }
    string tokens;
    try { tokens = connections.Unprotect(account.ProtectedTokenStore); }
    catch (Exception exception)
    {
      logger.LogWarning(exception, "Protected Garmin activity tokens could not be opened for profile {ProfileId}.", job.UserProfileId);
      await store.MarkFailedAsync(job.Id, "Stored Garmin authentication can no longer be opened; reconnect this runner.", true, now, now, cancellationToken);
      return;
    }

    using IServiceScope scope = scopeFactory.CreateScope();
    ISessionStore sessions = scope.ServiceProvider.GetRequiredService<ISessionStore>();
    StoredWorkoutSession? session = await sessions.FindAsync(job.WorkoutSessionId, cancellationToken);
    if (session is null)
    {
      await store.MarkFailedAsync(job.Id, "The completed local session no longer exists.", true, now, now, cancellationToken);
      return;
    }

    string tempDirectory = Path.Combine(Path.GetTempPath(), "TreadmillRunner", "garmin-upload");
    Directory.CreateDirectory(tempDirectory);
    string fitPath = Path.Combine(tempDirectory, $"{job.Id:N}.fit");
    try
    {
      await File.WriteAllBytesAsync(fitPath, SessionFitActivityExporter.Export(session), cancellationToken);
      GarminAdapterMessage result = await adapter.UploadAsync(tokens, fitPath, cancellationToken);
      if (string.Equals(result.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.TokenStore))
      {
        await store.MarkConfirmedAsync(job.Id, result.RemoteId, connections.Protect(result.TokenStore), timeProvider.GetUtcNow(), cancellationToken);
      }
      else if (string.Equals(result.State, "unknown", StringComparison.OrdinalIgnoreCase))
      {
        await store.MarkUnknownAsync(job.Id, result.Message ?? "Garmin received an upload request but its outcome is unknown.", timeProvider.GetUtcNow(), cancellationToken);
      }
      else
      {
        bool authentication = string.Equals(result.Kind, "authentication", StringComparison.OrdinalIgnoreCase);
        if (string.Equals(result.Kind, "provider-unavailable", StringComparison.OrdinalIgnoreCase))
        {
          await store.MarkProviderUnavailableAsync(job.Id, result.Message ?? "The unsupported Garmin adapter dependency is unavailable.", timeProvider.GetUtcNow(), cancellationToken);
          return;
        }
        if (string.Equals(result.Kind, "duplicate", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(result.Kind, "rejected", StringComparison.OrdinalIgnoreCase))
        {
          await store.MarkRejectedAsync(job.Id, result.Kind!.ToLowerInvariant(), result.Message ?? "Garmin rejected the activity import.", timeProvider.GetUtcNow(), cancellationToken);
          return;
        }
        TimeSpan delay = string.Equals(result.Kind, "rate-limit", StringComparison.OrdinalIgnoreCase)
          ? TimeSpan.FromMinutes(15)
          : TimeSpan.FromMinutes(Math.Pow(2, Math.Max(0, job.AttemptCount - 1)));
        await store.MarkFailedAsync(job.Id, result.Message ?? "Garmin rejected the activity import.", authentication, timeProvider.GetUtcNow().Add(delay), timeProvider.GetUtcNow(), cancellationToken);
      }
    }
    catch (TimeoutException exception)
    {
      await store.MarkUnknownAsync(job.Id, "The Garmin adapter timed out after upload began; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin activity upload {JobId} timed out with an unknown outcome.", job.Id);
    }
    catch (GarminAdapterUnavailableException exception)
    {
      await store.MarkProviderUnavailableAsync(job.Id, "The unsupported Garmin adapter is unavailable. Install or repair it, then retry explicitly.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin activity upload {JobId} could not start because the adapter is unavailable.", job.Id);
    }
    catch (GarminAdapterAmbiguousResultException exception)
    {
      await store.MarkUnknownAsync(job.Id, "The Garmin adapter returned no valid confirmation after upload may have begun; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin activity upload {JobId} ended with an ambiguous adapter result.", job.Id);
    }
    finally
    {
      try { if (File.Exists(fitPath)) File.Delete(fitPath); } catch (IOException exception) { logger.LogWarning(exception, "Temporary Garmin FIT file cleanup failed for {JobId}.", job.Id); }
    }
  }
}
