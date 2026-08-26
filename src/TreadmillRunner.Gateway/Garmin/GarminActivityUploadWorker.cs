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
    string localFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-local.fit");
    string watchFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-watch.fit");
    string mergedFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-merged.fit");
    bool mutationStarted = job.OperationPhase == "DeleteOriginal";
    try
    {
      if (job.OperationPhase == "DeleteOriginal")
      {
        await DeleteMatchedOriginalAsync(job, tokens, cancellationToken);
        return;
      }

      GarminWatchActivityMatch? match = null;
      if (job.OperationPhase == "WatchSearch" && session.Definition.Origin != SessionOrigin.SystemTest)
      {
        GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
        if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
        {
          await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
          return;
        }
        tokens = search.TokenStore;
        match = GarminWatchActivityMatcher.Match(ToMatchReference(session), search.Candidates ?? []);
        if (match.Disposition == GarminWatchActivityMatchDisposition.ReviewRequired)
        {
          await store.MarkReviewRequiredAsync(
            job.Id,
            match.Candidate?.RemoteId,
            match.Evidence,
            timeProvider.GetUtcNow(),
            cancellationToken);
          logger.LogInformation("Garmin watch candidate for upload job {JobId} requires manual review because local heart-rate evidence is absent: {Evidence}", job.Id, match.Evidence);
          return;
        }
        if (match.Disposition == GarminWatchActivityMatchDisposition.Single && match.Candidate is { } candidate)
        {
          if (account.WatchActivityHandling == GarminWatchActivityHandling.PreferWatch)
          {
            await store.MarkWatchFoundAsync(job.Id, candidate.RemoteId, match.Evidence, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken);
            return;
          }

          GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, watchFitPath, cancellationToken);
          if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
          {
            await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
            return;
          }
          tokens = download.TokenStore;
          byte[] merged = GarminFitActivityMerger.Merge(await File.ReadAllBytesAsync(watchFitPath, cancellationToken), session);
          await File.WriteAllBytesAsync(mergedFitPath, merged, cancellationToken);
          await store.MarkUploadStartedAsync(job.Id, "ReplacementUpload", timeProvider.GetUtcNow(), cancellationToken);
          mutationStarted = true;
          GarminAdapterMessage replacement = await adapter.UploadAsync(tokens, mergedFitPath, cancellationToken);
          if (string.Equals(replacement.State, "confirmed", StringComparison.OrdinalIgnoreCase) &&
              !string.IsNullOrWhiteSpace(replacement.TokenStore) && !string.IsNullOrWhiteSpace(replacement.RemoteId))
          {
            if (string.Equals(candidate.RemoteId, replacement.RemoteId, StringComparison.Ordinal))
            {
              await store.MarkReplacementUncertainAsync(job.Id, candidate.RemoteId, match.Evidence, "Garmin returned the original activity ID for the merged import; no delete was attempted.", connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken);
              return;
            }
            await store.MarkReplacementUploadedAsync(job.Id, candidate.RemoteId, replacement.RemoteId, match.Evidence, connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken);
            return;
          }
          if (string.Equals(replacement.State, "confirmed", StringComparison.OrdinalIgnoreCase))
          {
            await store.MarkReplacementUncertainAsync(job.Id, candidate.RemoteId, match.Evidence, "Garmin confirmed the merged import without a distinct activity ID; the original watch activity was retained.", string.IsNullOrWhiteSpace(replacement.TokenStore) ? null : connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken);
            return;
          }
          await HandleMutationResultAsync(job, replacement, "Garmin did not confirm the merged replacement with a distinct activity ID; the original watch activity was retained.", cancellationToken);
          return;
        }
      }

      await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
      await store.MarkUploadStartedAsync(job.Id, "Upload", timeProvider.GetUtcNow(), cancellationToken);
      mutationStarted = true;
      GarminAdapterMessage result = await adapter.UploadAsync(tokens, localFitPath, cancellationToken);
      await HandleMutationResultAsync(job, result,
        match?.Disposition == GarminWatchActivityMatchDisposition.Multiple
          ? "Multiple plausible watch activities were found, so the local activity was uploaded for manual cleanup."
          : "Garmin did not confirm the activity upload.", cancellationToken);
    }
    catch (Exception exception) when (!mutationStarted && exception is InvalidDataException or Dynastream.Fit.FitException)
    {
      await store.MarkRejectedAsync(job.Id, "merge-source", "The matched watch FIT could not be validated or merged; the original watch activity was retained.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin watch activity {JobId} could not be merged safely.", job.Id);
    }
    catch (TimeoutException exception)
    {
      if (!mutationStarted)
      {
        await store.MarkFailedAsync(job.Id, "The Garmin watch search timed out before any account mutation; it can be retried safely.", false, timeProvider.GetUtcNow().AddMinutes(2), timeProvider.GetUtcNow(), CancellationToken.None);
      }
      else
      {
        await store.MarkUnknownAsync(job.Id, "The Garmin adapter timed out after an account mutation may have begun; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None);
      }
      logger.LogWarning(exception, "Garmin activity upload {JobId} timed out with an unknown outcome.", job.Id);
    }
    catch (GarminAdapterUnavailableException exception)
    {
      await store.MarkProviderUnavailableAsync(job.Id, "The unsupported Garmin adapter is unavailable. Install or repair it, then retry explicitly.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin activity upload {JobId} could not start because the adapter is unavailable.", job.Id);
    }
    catch (GarminAdapterAmbiguousResultException exception)
    {
      if (!mutationStarted)
        await store.MarkFailedAsync(job.Id, "The Garmin watch search returned an invalid read response and can be retried safely.", false, timeProvider.GetUtcNow().AddMinutes(2), timeProvider.GetUtcNow(), CancellationToken.None);
      else
        await store.MarkUnknownAsync(job.Id, "The Garmin adapter returned no valid confirmation after an account mutation may have begun; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None);
      logger.LogWarning(exception, "Garmin activity upload {JobId} ended with an ambiguous adapter result.", job.Id);
    }
    finally
    {
      foreach (string path in new[] { localFitPath, watchFitPath, mergedFitPath })
        try { if (File.Exists(path)) File.Delete(path); } catch (IOException exception) { logger.LogWarning(exception, "Temporary Garmin FIT file cleanup failed for {JobId}.", job.Id); }
    }
  }

  private async Task DeleteMatchedOriginalAsync(GarminActivityUploadJob job, string tokens, CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.ReplacementRemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "Replacement state is incomplete; no Garmin activity was deleted.", timeProvider.GetUtcNow(), cancellationToken);
      return;
    }
    await store.MarkUploadStartedAsync(job.Id, "DeleteOriginal", timeProvider.GetUtcNow(), cancellationToken);
    GarminAdapterMessage result = await adapter.DeleteAsync(tokens, job.MatchedRemoteId, cancellationToken);
    if (string.Equals(result.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.TokenStore))
      await store.MarkConfirmedAsync(job.Id, job.ReplacementRemoteId, connections.Protect(result.TokenStore), timeProvider.GetUtcNow(), cancellationToken);
    else
      await HandleMutationResultAsync(job, result, "The merged activity exists, but Garmin did not confirm removal of the original; review both activities manually.", cancellationToken);
  }

  private async Task HandleReadFailureAsync(GarminActivityUploadJob job, string? kind, string? message, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    if (string.Equals(kind, "provider-unavailable", StringComparison.OrdinalIgnoreCase))
      await store.MarkProviderUnavailableAsync(job.Id, message ?? "The unsupported Garmin adapter dependency is unavailable.", now, cancellationToken);
    else
      await store.MarkFailedAsync(job.Id, message ?? "Garmin watch activity lookup failed before any account mutation.", string.Equals(kind, "authentication", StringComparison.OrdinalIgnoreCase), now.Add(string.Equals(kind, "rate-limit", StringComparison.OrdinalIgnoreCase) ? TimeSpan.FromMinutes(15) : TimeSpan.FromMinutes(2)), now, cancellationToken);
  }

  private async Task HandleMutationResultAsync(GarminActivityUploadJob job, GarminAdapterMessage result, string fallbackMessage, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    if (string.Equals(result.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.TokenStore))
    {
      await store.MarkConfirmedAsync(job.Id, result.RemoteId, connections.Protect(result.TokenStore), now, cancellationToken);
      return;
    }
    if (string.Equals(result.State, "unknown", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkUnknownAsync(job.Id, result.Message ?? fallbackMessage, now, cancellationToken);
      return;
    }
    if (string.Equals(result.Kind, "provider-unavailable", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkProviderUnavailableAsync(job.Id, result.Message ?? fallbackMessage, now, cancellationToken);
      return;
    }
    if (string.Equals(result.Kind, "duplicate", StringComparison.OrdinalIgnoreCase) || string.Equals(result.Kind, "rejected", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkRejectedAsync(job.Id, result.Kind!.ToLowerInvariant(), result.Message ?? fallbackMessage, now, cancellationToken);
      return;
    }
    bool authentication = string.Equals(result.Kind, "authentication", StringComparison.OrdinalIgnoreCase);
    TimeSpan delay = string.Equals(result.Kind, "rate-limit", StringComparison.OrdinalIgnoreCase) ? TimeSpan.FromMinutes(15) : TimeSpan.FromMinutes(Math.Pow(2, Math.Max(0, job.AttemptCount - 1)));
    await store.MarkFailedAsync(job.Id, result.Message ?? fallbackMessage, authentication, now.Add(delay), now, cancellationToken);
  }

  internal static GarminActivityMatchReference ToMatchReference(StoredWorkoutSession session)
  {
    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(session.Samples);
    return new(
      session.StartedAt!.Value,
      session.Duration.TotalSeconds,
      session.DistanceKilometers,
      statistics.AverageHeartRateBpm ?? session.AverageHeartRateBpm,
      statistics.MaximumHeartRateBpm ?? session.MaximumHeartRateBpm,
      session.Samples.Where(sample => sample.HeartRateBpm is not null)
        .Select(sample => new GarminWatchHeartRateSample(sample.Elapsed.TotalSeconds, sample.HeartRateBpm!.Value)).ToArray());
  }
}
