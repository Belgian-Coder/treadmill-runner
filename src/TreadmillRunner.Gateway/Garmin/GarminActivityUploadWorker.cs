using System.Security.Cryptography;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Exports;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminActivityUploadWorker(
  IServiceScopeFactory scopeFactory,
  IGarminActivityUploadStore store,
  IGarminActivityAdapter adapter,
  GarminActivityBackupStore backups,
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
    foreach (GarminActivityUploadJob incomplete in await store.ListIncompleteReplacementJobsAsync(cancellationToken))
    {
      if (!backups.TryFindRecoveryOriginalRemoteId(incomplete.Id, out string? backedUpOriginal) ||
          string.IsNullOrWhiteSpace(backedUpOriginal) ||
          (!string.IsNullOrWhiteSpace(incomplete.MatchedRemoteId) && !string.Equals(incomplete.MatchedRemoteId, backedUpOriginal, StringComparison.Ordinal)))
        continue;
      await store.ResumeIncompleteReplacementAsync(incomplete.Id, backedUpOriginal, now, cancellationToken);
    }
    while (await store.LeaseNextAsync(timeProvider.GetUtcNow(), TimeSpan.FromMinutes(2), cancellationToken) is { } job)
      await ProcessOneAsync(job, cancellationToken);
  }

  internal async Task ProcessOneAsync(GarminActivityUploadJob job, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    GarminActivityUploadAccount? account = await store.FindAccountAsync(job.UserProfileId, cancellationToken);
    if (account is null || !account.Enabled)
    {
      await store.MarkFailedAsync(job.Id, "The runner's unsupported Garmin upload is disconnected or disabled.", true, now, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    string tokens;
    try { tokens = connections.Unprotect(account.ProtectedTokenStore); }
    catch (Exception exception)
    {
      logger.LogWarning(exception, "Protected Garmin activity tokens could not be opened for profile {ProfileId}.", job.UserProfileId);
      await store.MarkFailedAsync(job.Id, "Stored Garmin authentication can no longer be opened; reconnect this runner.", true, now, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    using IServiceScope scope = scopeFactory.CreateScope();
    ISessionStore sessions = scope.ServiceProvider.GetRequiredService<ISessionStore>();
    StoredWorkoutSession? session = await sessions.FindAsync(job.WorkoutSessionId, cancellationToken);
    if (session is null)
    {
      await store.MarkFailedAsync(job.Id, "The completed local session no longer exists.", true, now, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    string tempDirectory = Path.Combine(Path.GetTempPath(), "TreadmillRunner", "garmin-upload");
    Directory.CreateDirectory(tempDirectory);
    string localFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-local.fit");
    string watchFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-watch.fit");
    string mergedFitPath = Path.Combine(tempDirectory, $"{job.Id:N}-merged.fit");
    var mutation = new MutationState { Started = job.OperationPhase == "DeleteOriginal" };
    try
    {
      if (job.OperationPhase == "DeleteOriginal")
      {
        await DeleteMatchedOriginalAsync(job, tokens, watchFitPath, cancellationToken);
        return;
      }
      if (job.OperationPhase == "VerifyResync")
      {
        await VerifyResyncAsync(job, session, tokens, watchFitPath, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "ResolveReplacement")
      {
        await ResolveReplacementAsync(job, session, tokens, localFitPath, watchFitPath, uploadIfMissing: false, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "ReplacementUpload")
      {
        await ResolveReplacementAsync(job, session, tokens, localFitPath, watchFitPath, uploadIfMissing: false, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "EnsureReplacement")
      {
        await ResolveReplacementAsync(job, session, tokens, localFitPath, watchFitPath, uploadIfMissing: true, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "DeleteReplacementDuplicates")
      {
        await DeleteReplacementDuplicateAsync(job, session, tokens, localFitPath, watchFitPath, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "DeleteReplacementDuplicate")
      {
        await DeleteReplacementDuplicateAsync(job, session, tokens, localFitPath, watchFitPath, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase is "ResolveOriginal" or "ResolveRestoredOriginal")
      {
        await ResolveOriginalAsync(job, session, tokens, localFitPath, watchFitPath, restoreIfMissing: job.OperationPhase == "ResolveOriginal", mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "RestoreOriginal")
      {
        await ResolveOriginalAsync(job, session, tokens, localFitPath, watchFitPath, restoreIfMissing: false, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase is "ResolveLocalSource" or "ResolveRestoredLocal")
      {
        await ResolveLocalSourceAsync(job, session, tokens, localFitPath, watchFitPath, restoreIfMissing: job.OperationPhase == "ResolveLocalSource", mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "RestoreLocal")
      {
        await ResolveLocalSourceAsync(job, session, tokens, localFitPath, watchFitPath, restoreIfMissing: false, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "DeleteGeneratedCopies")
      {
        await DeleteGeneratedCopyAsync(job, session, tokens, localFitPath, watchFitPath, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "DeleteGeneratedCopy")
      {
        await DeleteGeneratedCopyAsync(job, session, tokens, localFitPath, watchFitPath, mutation, cancellationToken);
        return;
      }
      if (job.OperationPhase == "DeleteResyncedOriginal")
      {
        await VerifyResyncAsync(job, session, tokens, watchFitPath, mutation, cancellationToken);
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
        if (match.Disposition is GarminWatchActivityMatchDisposition.ReviewRequired or GarminWatchActivityMatchDisposition.Multiple)
        {
          await store.MarkReviewRequiredAsync(
            job.Id,
            match.Candidate?.RemoteId,
            match.Evidence,
            timeProvider.GetUtcNow(),
            cancellationToken,
            expectedLeaseExpiresAtUtc: RequiredLease(job));
          logger.LogInformation("Garmin watch candidates for upload job {JobId} require manual review before any upload because the treadmill activity shape is ambiguous: {Evidence}", job.Id, match.Evidence);
          return;
        }
        if (match.Disposition == GarminWatchActivityMatchDisposition.Single && match.Candidate is { } candidate)
        {
          if (account.WatchActivityHandling == GarminWatchActivityHandling.PreferWatch)
          {
            await store.MarkWatchFoundAsync(job.Id, candidate.RemoteId, match.Evidence, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
            return;
          }

          GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, watchFitPath, cancellationToken);
          if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
          {
            await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
            return;
          }
          tokens = download.TokenStore;
          await backups.BackupOriginalAsync(job.Id, candidate.RemoteId, watchFitPath, cancellationToken);
          await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
          await backups.BackupLocalAsync(job.Id, localFitPath, cancellationToken);
          byte[] merged = GarminFitActivityMerger.Merge(await File.ReadAllBytesAsync(watchFitPath, cancellationToken), session);
          await File.WriteAllBytesAsync(mergedFitPath, merged, cancellationToken);
          await backups.BackupReplacementAsync(job.Id, candidate.RemoteId, mergedFitPath, cancellationToken);
          await store.MarkReplacementUploadStartedAsync(
            job.Id, candidate.RemoteId, match.Evidence, RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
          mutation.Started = true;
          mutation.MatchedRemoteId = candidate.RemoteId;
          mutation.MatchEvidence = match.Evidence;
          GarminAdapterMessage replacement = await adapter.UploadAsync(tokens, mergedFitPath, cancellationToken);
          if (string.Equals(replacement.State, "confirmed", StringComparison.OrdinalIgnoreCase) &&
              !string.IsNullOrWhiteSpace(replacement.TokenStore) && !string.IsNullOrWhiteSpace(replacement.RemoteId))
          {
            if (string.Equals(candidate.RemoteId, replacement.RemoteId, StringComparison.Ordinal))
            {
              await store.MarkReplacementAwaitingResolutionAsync(job.Id, candidate.RemoteId, match.Evidence, "Garmin returned the original activity ID for the merged import. The app will identify the accepted replacement before deleting or uploading anything else.", connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
              return;
            }
            await store.MarkReplacementUploadedAsync(job.Id, candidate.RemoteId, replacement.RemoteId, match.Evidence, connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
            return;
          }
          if (string.Equals(replacement.State, "confirmed", StringComparison.OrdinalIgnoreCase))
          {
            await store.MarkReplacementAwaitingResolutionAsync(job.Id, candidate.RemoteId, match.Evidence, "Garmin accepted the merged activity without returning its ID. The app will find it read-only before removing the original.", string.IsNullOrWhiteSpace(replacement.TokenStore) ? null : connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
            return;
          }
          if (string.Equals(replacement.State, "unknown", StringComparison.OrdinalIgnoreCase))
          {
            await store.MarkReplacementAwaitingResolutionAsync(job.Id, candidate.RemoteId, match.Evidence, replacement.Message ?? "Garmin may have accepted the merged activity. The app will check read-only and will not upload it again.", string.IsNullOrWhiteSpace(replacement.TokenStore) ? null : connections.Protect(replacement.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
            return;
          }
          await HandleMutationResultAsync(job, replacement, "Garmin did not confirm the merged replacement with a distinct activity ID; the original watch activity was retained.", cancellationToken);
          return;
        }
      }

      if (job.OperationPhase is not ("WatchSearch" or "Upload"))
      {
        await store.MarkUnknownAsync(job.Id, $"Garmin recovery stopped at unsupported phase '{job.OperationPhase}'; no upload was sent.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }

      await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
      await store.MarkUploadStartedAsync(job.Id, "Upload", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
      mutation.Started = true;
      GarminAdapterMessage result = await adapter.UploadAsync(tokens, localFitPath, cancellationToken);
      await HandleMutationResultAsync(job, result, "Garmin did not confirm the activity upload.", cancellationToken);
    }
    catch (Exception exception) when (!mutation.Started && exception is InvalidDataException or Dynastream.Fit.FitException)
    {
      await store.MarkRejectedAsync(job.Id, "merge-source", "The matched watch FIT could not be validated or merged; the original watch activity was retained.", timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      logger.LogWarning(exception, "Garmin watch activity {JobId} could not be merged safely.", job.Id);
    }
    catch (TimeoutException exception)
    {
      if (!mutation.Started)
      {
        await store.MarkFailedAsync(job.Id, "The Garmin watch search timed out before any account mutation; it can be retried safely.", false, timeProvider.GetUtcNow().AddMinutes(2), timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      }
      else if (mutation.OriginalUploadStarted)
      {
        await store.MarkOriginalAwaitingResolutionAsync(job.Id, "The Garmin adapter timed out after the original restore may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      }
      else if (mutation.LocalUploadStarted)
      {
        await store.MarkLocalAwaitingResolutionAsync(job.Id, "The Garmin adapter timed out after the local activity restore may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      }
      else if (!string.IsNullOrWhiteSpace(mutation.MatchedRemoteId))
      {
        await store.MarkReplacementAwaitingResolutionAsync(job.Id, mutation.MatchedRemoteId, mutation.MatchEvidence ?? "A Garmin watch activity was selected for merge.", "The Garmin adapter timed out after the merged upload may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      }
      else
      {
        await store.MarkUnknownAsync(job.Id, "The Garmin adapter timed out after an account mutation may have begun; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      }
      logger.LogWarning(exception, "Garmin activity upload {JobId} timed out with an unknown outcome.", job.Id);
    }
    catch (GarminAdapterUnavailableException exception)
    {
      await store.MarkProviderUnavailableAsync(job.Id, "The unsupported Garmin adapter is unavailable. Install or repair it, then retry explicitly.", timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      logger.LogWarning(exception, "Garmin activity upload {JobId} could not start because the adapter is unavailable.", job.Id);
    }
    catch (GarminAdapterAmbiguousResultException exception)
    {
      if (!mutation.Started)
        await store.MarkFailedAsync(job.Id, "The Garmin watch search returned an invalid read response and can be retried safely.", false, timeProvider.GetUtcNow().AddMinutes(2), timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      else if (mutation.OriginalUploadStarted)
        await store.MarkOriginalAwaitingResolutionAsync(job.Id, "The Garmin adapter returned no valid confirmation after the original restore may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      else if (mutation.LocalUploadStarted)
        await store.MarkLocalAwaitingResolutionAsync(job.Id, "The Garmin adapter returned no valid confirmation after the local activity restore may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      else if (!string.IsNullOrWhiteSpace(mutation.MatchedRemoteId))
        await store.MarkReplacementAwaitingResolutionAsync(job.Id, mutation.MatchedRemoteId, mutation.MatchEvidence ?? "A Garmin watch activity was selected for merge.", "The Garmin adapter returned no valid confirmation after the merged upload may have started. The app will check read-only and will not upload it again.", null, timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      else
        await store.MarkUnknownAsync(job.Id, "The Garmin adapter returned no valid confirmation after an account mutation may have begun; no automatic retry will occur.", timeProvider.GetUtcNow(), CancellationToken.None, expectedLeaseExpiresAtUtc: RequiredLease(job));
      logger.LogWarning(exception, "Garmin activity upload {JobId} ended with an ambiguous adapter result.", job.Id);
    }
    finally
    {
      foreach (string path in new[] { localFitPath, watchFitPath, mergedFitPath })
        try { if (File.Exists(path)) File.Delete(path); } catch (IOException exception) { logger.LogWarning(exception, "Temporary Garmin FIT file cleanup failed for {JobId}.", job.Id); }
    }
  }

  private async Task ResolveReplacementAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string localFitPath,
    string candidateFitPath,
    bool uploadIfMissing,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) ||
        !backups.TryFindRecoveryOriginalRemoteId(job.Id, out _))
    {
      await store.MarkUnknownAsync(job.Id, "The accepted merged activity cannot be resolved because its original and replacement backups are incomplete.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    bool hasDurableReplacement = !string.IsNullOrWhiteSpace(job.ReplacementRemoteId);
    GarminWatchActivityCandidate[] canonical = (search.Candidates ?? [])
      .Where(candidate => !string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal) &&
        (string.Equals(candidate.RemoteId, job.ReplacementRemoteId, StringComparison.Ordinal) ||
          GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidate)))
      .OrderBy(candidate => candidate.RemoteId, StringComparer.Ordinal)
      .ToArray();
    await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
    var evidence = new List<ReplacementCandidateEvidence>(canonical.Length);
    foreach (GarminWatchActivityCandidate candidate in canonical)
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, candidateFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      evidence.Add(new(
        candidate,
        backups.MatchesReplacement(job.Id, job.MatchedRemoteId, candidateFitPath),
        backups.MatchesRecoveryOriginal(job.Id, candidateFitPath),
        backups.HasLocal(job.Id)
          ? backups.MatchesLocal(job.Id, candidateFitPath)
          : FilesMatch(localFitPath, candidateFitPath)));
    }

    ReplacementCandidateEvidence? kept = evidence.FirstOrDefault(item => item.MatchesMergedBackup);
    if (evidence.Any(item =>
      (kept is null || item.Candidate.RemoteId != kept.Candidate.RemoteId) && !item.IsProvenCopy))
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        null,
        $"{canonical.Length} canonical Garmin activities exist, but at least one does not match retained source FIT evidence. No activity was deleted and no upload was sent.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    if (kept is null)
    {
      if (!uploadIfMissing || hasDurableReplacement)
      {
        await store.CompleteReplacementResolutionCheckAsync(job.Id, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      if (!backups.TryGetRecoveryReplacementPath(job.Id, out string? replacementPath) || string.IsNullOrWhiteSpace(replacementPath))
      {
        await store.MarkUnknownAsync(job.Id, "The retained merged FIT is unavailable, so Merge into one did not upload or delete anything.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      await store.MarkReplacementUploadStartedAsync(
        job.Id,
        job.MatchedRemoteId,
        job.MatchEvidence ?? "Merge into one is resolving the retained merged FIT.",
        RequiredLease(job),
        timeProvider.GetUtcNow(),
        cancellationToken);
      mutation.Started = true;
      mutation.MatchedRemoteId = job.MatchedRemoteId;
      mutation.MatchEvidence = job.MatchEvidence;
      GarminAdapterMessage upload = await adapter.UploadAsync(tokens, replacementPath, cancellationToken);
      if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) &&
          !string.IsNullOrWhiteSpace(upload.TokenStore) && !string.IsNullOrWhiteSpace(upload.RemoteId) &&
          !string.Equals(upload.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal))
      {
        await store.MarkReplacementResolvedAsync(job.Id, job.MatchedRemoteId, upload.RemoteId,
          "Merge into one uploaded the retained merged FIT once and Garmin returned its distinct activity ID.",
          connections.Protect(upload.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
          string.Equals(upload.State, "unknown", StringComparison.OrdinalIgnoreCase))
      {
        await store.MarkReplacementAwaitingResolutionAsync(job.Id, job.MatchedRemoteId,
          job.MatchEvidence ?? "Merge into one is resolving the retained merged FIT.",
          upload.Message ?? "Garmin may have accepted the retained merged FIT. The app will check read-only and will not upload it again.",
          string.IsNullOrWhiteSpace(upload.TokenStore) ? null : connections.Protect(upload.TokenStore),
          timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      await HandleMutationResultAsync(job, upload, "Garmin did not confirm the retained merged FIT upload.", cancellationToken);
      return;
    }

    string resolutionEvidence = $"Resolved accepted merged activity {kept.Candidate.RemoteId} from {canonical.Length} canonical candidate(s) using the retained FIT evidence; no second upload was sent.";
    await store.MarkReplacementResolvedAsync(
      job.Id,
      job.MatchedRemoteId,
      kept.Candidate.RemoteId,
      resolutionEvidence,
      connections.Protect(tokens),
      timeProvider.GetUtcNow(),
      cancellationToken,
      expectedLeaseExpiresAtUtc: RequiredLease(job));
  }

  private async Task DeleteReplacementDuplicateAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string localFitPath,
    string candidateFitPath,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.ReplacementRemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "Replacement identity is incomplete; no Garmin activity was deleted.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    IReadOnlyList<GarminWatchActivityCandidate> candidates = search.Candidates ?? [];
    GarminWatchActivityCandidate? retainedReplacement = candidates.FirstOrDefault(candidate =>
      string.Equals(candidate.RemoteId, job.ReplacementRemoteId, StringComparison.Ordinal));
    if (retainedReplacement is null)
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        job.ReplacementRemoteId,
        $"The retained merged Garmin activity {job.ReplacementRemoteId} was not visible, so no duplicate or original activity was deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    GarminAdapterMessage retainedDownload = await adapter.DownloadOriginalAsync(
      tokens,
      retainedReplacement.RemoteId,
      candidateFitPath,
      cancellationToken);
    if (!string.Equals(retainedDownload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
        string.IsNullOrWhiteSpace(retainedDownload.TokenStore))
    {
      await HandleReadFailureAsync(job, retainedDownload.Kind, retainedDownload.Message, cancellationToken);
      return;
    }
    tokens = retainedDownload.TokenStore;
    if (!backups.MatchesRecoveryReplacement(job.Id, candidateFitPath))
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        retainedReplacement.RemoteId,
        $"The retained merged Garmin activity {retainedReplacement.RemoteId} no longer matches the saved merged FIT. Nothing was deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    bool originalStillExists = candidates.Any(candidate => string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal));
    GarminWatchActivityCandidate[] possibleDuplicates = candidates
      .Where(candidate => candidate.RemoteId != job.MatchedRemoteId && candidate.RemoteId != job.ReplacementRemoteId &&
        GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidate))
      .OrderBy(candidate => candidate.RemoteId, StringComparer.Ordinal)
      .ToArray();
    await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
    foreach (GarminWatchActivityCandidate candidate in possibleDuplicates)
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, candidateFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      bool provenGenerated = backups.MatchesRecoveryReplacement(job.Id, candidateFitPath) ||
        backups.MatchesRecoveryOriginal(job.Id, candidateFitPath) ||
        (backups.HasLocal(job.Id) ? backups.MatchesLocal(job.Id, candidateFitPath) : FilesMatch(localFitPath, candidateFitPath));
      if (!provenGenerated)
      {
        await store.MarkReviewRequiredAsync(
          job.Id,
          candidate.RemoteId,
          $"Garmin activity {candidate.RemoteId} has the local session shape but does not match either retained TreadmillRunner FIT. Nothing else was deleted.",
          timeProvider.GetUtcNow(),
          cancellationToken,
          expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }

      await store.MarkUploadStartedAsync(
        job.Id, "DeleteReplacementDuplicate", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
      mutation.Started = true;
      GarminAdapterMessage deletion = await adapter.DeleteAsync(tokens, candidate.RemoteId, cancellationToken);
      if (string.Equals(deletion.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(deletion.TokenStore))
      {
        await store.ContinueReplacementDuplicateCleanupAsync(job.Id, connections.Protect(deletion.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      await HandleMutationResultAsync(job, deletion, "Garmin did not confirm removal of a proven duplicate merged activity.", cancellationToken);
      return;
    }

    await store.CompleteReplacementDuplicateCleanupAsync(
      job.Id,
      originalStillExists,
      connections.Protect(tokens),
      timeProvider.GetUtcNow(),
      cancellationToken,
      expectedLeaseExpiresAtUtc: RequiredLease(job));
  }

  private async Task ResolveLocalSourceAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string localFitPath,
    string candidateFitPath,
    bool restoreIfMissing,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "The retained watch-original identity is incomplete; Undo merge cannot resolve the local activity.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    if (!backups.TryGetLocalPath(job.Id, out string? retainedLocalPath) || string.IsNullOrWhiteSpace(retainedLocalPath))
    {
      // Defensive fallback for a legacy job whose backup disappeared between
      // the original-resolution and local-resolution passes.
      await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
      await backups.BackupLocalAsync(job.Id, localFitPath, cancellationToken);
      retainedLocalPath = localFitPath;
    }

    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    bool hasDurableLocal = !string.IsNullOrWhiteSpace(job.RemoteId);
    var localMatches = new List<GarminWatchActivityCandidate>();
    foreach (GarminWatchActivityCandidate candidate in (search.Candidates ?? [])
      .Where(candidate => !string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal) &&
        (string.Equals(candidate.RemoteId, job.RemoteId, StringComparison.Ordinal) ||
          GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidate)))
      .OrderBy(candidate => candidate.RemoteId, StringComparer.Ordinal))
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, candidateFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      bool matchesLocal = backups.MatchesLocal(job.Id, candidateFitPath);
      bool matchesOriginal = backups.MatchesRecoveryOriginal(job.Id, candidateFitPath);
      bool matchesReplacement = backups.MatchesRecoveryReplacement(job.Id, candidateFitPath);
      if (matchesLocal)
        localMatches.Add(candidate);
      else if (!matchesOriginal && !matchesReplacement)
      {
        await store.MarkReviewRequiredAsync(
          job.Id,
          candidate.RemoteId,
          $"Garmin activity {candidate.RemoteId} overlaps this session but does not match the retained local, original, or merged FIT evidence. Undo stopped without deleting or uploading anything.",
          timeProvider.GetUtcNow(),
          cancellationToken,
          expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
    }

    GarminWatchActivityCandidate? kept = localMatches.FirstOrDefault();
    if (kept is not null)
    {
      await store.MarkLocalResolvedAsync(
        job.Id,
        kept.RemoteId,
        $"Undo merge identified retained plain local Garmin activity {kept.RemoteId} by exact deterministic FIT evidence.",
        connections.Protect(tokens),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    if (!restoreIfMissing || hasDurableLocal)
    {
      await store.CompleteLocalResolutionCheckAsync(job.Id, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    await store.MarkUploadStartedAsync(job.Id, "RestoreLocal", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
    mutation.Started = true;
    mutation.LocalUploadStarted = true;
    GarminAdapterMessage upload = await adapter.UploadAsync(tokens, retainedLocalPath, cancellationToken);
    if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) &&
        !string.IsNullOrWhiteSpace(upload.TokenStore) && !string.IsNullOrWhiteSpace(upload.RemoteId) &&
        !string.Equals(upload.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal))
    {
      await store.MarkLocalResolvedAsync(
        job.Id,
        upload.RemoteId,
        $"Undo merge restored the retained plain local Garmin FIT as activity {upload.RemoteId}.",
        connections.Protect(upload.TokenStore),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(upload.State, "unknown", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkLocalAwaitingResolutionAsync(
        job.Id,
        upload.Message ?? "Garmin may have restored the local activity. The app will check read-only and will not upload it again.",
        string.IsNullOrWhiteSpace(upload.TokenStore) ? null : connections.Protect(upload.TokenStore),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    await HandleMutationResultAsync(job, upload, "Garmin did not confirm restoration of the local activity.", cancellationToken);
  }

  private async Task ResolveOriginalAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string localFitPath,
    string candidateFitPath,
    bool restoreIfMissing,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) ||
        !backups.TryGetRecoveryOriginalPath(job.Id, out string? originalPath) ||
        string.IsNullOrWhiteSpace(originalPath))
    {
      await store.MarkUnknownAsync(job.Id, "The retained original Garmin FIT is unavailable, so Undo merge did not upload or delete anything.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    // Older merge jobs predate the local FIT backup.  Recreate that backup
    // from the deterministic session export before any Undo resolution so a
    // plain local source can be identified and retained exactly once.
    await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
    if (!backups.HasLocal(job.Id))
      await backups.BackupLocalAsync(job.Id, localFitPath, cancellationToken);

    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    var originals = new List<GarminWatchActivityCandidate>();
    foreach (GarminWatchActivityCandidate candidate in (search.Candidates ?? [])
      .Where(candidate => string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal) ||
        GarminWatchActivityMatcher.IsPlausibleShape(local, candidate))
      .OrderBy(candidate => candidate.RemoteId, StringComparer.Ordinal))
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, candidateFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      if (backups.MatchesRecoveryOriginal(job.Id, candidateFitPath)) originals.Add(candidate);
    }

    GarminWatchActivityCandidate? kept = originals.FirstOrDefault(candidate => candidate.RemoteId == job.MatchedRemoteId) ?? originals.FirstOrDefault();
    if (kept is not null)
    {
      await store.MarkOriginalResolvedAsync(
        job.Id,
        kept.RemoteId,
        $"Undo merge identified retained original Garmin activity {kept.RemoteId} by exact FIT evidence.",
        connections.Protect(tokens),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    if (!restoreIfMissing)
    {
      await store.CompleteOriginalResolutionCheckAsync(job.Id, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    await store.MarkUploadStartedAsync(job.Id, "RestoreOriginal", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
    mutation.Started = true;
    mutation.OriginalUploadStarted = true;
    GarminAdapterMessage upload = await adapter.UploadAsync(tokens, originalPath, cancellationToken);
    if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) &&
        !string.IsNullOrWhiteSpace(upload.TokenStore) && !string.IsNullOrWhiteSpace(upload.RemoteId))
    {
      await store.MarkOriginalResolvedAsync(
        job.Id,
        upload.RemoteId,
        $"Undo merge restored the retained original Garmin FIT as activity {upload.RemoteId}.",
        connections.Protect(upload.TokenStore),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (string.Equals(upload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(upload.State, "unknown", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkOriginalAwaitingResolutionAsync(
        job.Id,
        upload.Message ?? "Garmin may have restored the original activity. The app will check read-only and will not upload it again.",
        string.IsNullOrWhiteSpace(upload.TokenStore) ? null : connections.Protect(upload.TokenStore),
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    await HandleMutationResultAsync(job, upload, "Garmin did not confirm restoration of the original activity.", cancellationToken);
  }

  private async Task DeleteGeneratedCopyAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string localFitPath,
    string candidateFitPath,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.RemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "Both the retained watch-original and plain local Garmin activity identities are required; Undo merge deleted nothing.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (!backups.HasLocal(job.Id))
    {
      await File.WriteAllBytesAsync(localFitPath, SessionFitActivityExporter.Export(session), cancellationToken);
      await backups.BackupLocalAsync(job.Id, localFitPath, cancellationToken);
    }

    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    bool retainedWatchVerified = false;
    bool retainedLocalVerified = false;
    foreach (GarminWatchActivityCandidate candidate in (search.Candidates ?? [])
      .Where(candidate => string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal) ||
        string.Equals(candidate.RemoteId, job.RemoteId, StringComparison.Ordinal) ||
        GarminWatchActivityMatcher.IsPlausibleShape(local, candidate))
      .OrderBy(candidate => candidate.RemoteId, StringComparer.Ordinal))
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, candidateFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      bool isKeptWatch = string.Equals(candidate.RemoteId, job.MatchedRemoteId, StringComparison.Ordinal);
      bool isKeptLocal = string.Equals(candidate.RemoteId, job.RemoteId, StringComparison.Ordinal);
      bool matchesOriginal = backups.MatchesRecoveryOriginal(job.Id, candidateFitPath);
      bool matchesLocal = backups.MatchesLocal(job.Id, candidateFitPath);
      bool matchesReplacement = backups.MatchesRecoveryReplacement(job.Id, candidateFitPath);

      if (isKeptWatch)
      {
        if (!matchesOriginal)
        {
          await store.MarkReviewRequiredAsync(
            job.Id,
            candidate.RemoteId,
            $"Retained watch activity {candidate.RemoteId} no longer matches the backed-up original FIT. Undo stopped without deleting anything.",
            timeProvider.GetUtcNow(),
            cancellationToken,
            expectedLeaseExpiresAtUtc: RequiredLease(job));
          return;
        }
        retainedWatchVerified = true;
        continue;
      }
      if (isKeptLocal)
      {
        if (!matchesLocal)
        {
          await store.MarkReviewRequiredAsync(
            job.Id,
            candidate.RemoteId,
            $"Retained plain local activity {candidate.RemoteId} no longer matches the deterministic local FIT. Undo stopped without deleting anything.",
            timeProvider.GetUtcNow(),
            cancellationToken,
            expectedLeaseExpiresAtUtc: RequiredLease(job));
          return;
        }
        retainedLocalVerified = true;
        continue;
      }

      bool provenGenerated = matchesOriginal || matchesReplacement || matchesLocal;
      if (!provenGenerated)
      {
        if (GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidate))
        {
          await store.MarkReviewRequiredAsync(
            job.Id,
            candidate.RemoteId,
            $"Garmin activity {candidate.RemoteId} overlaps this session but does not match retained local, original, or merged FIT evidence. Undo stopped without deleting it.",
            timeProvider.GetUtcNow(),
            cancellationToken,
            expectedLeaseExpiresAtUtc: RequiredLease(job));
          return;
        }
        continue;
      }

      await store.MarkUploadStartedAsync(
        job.Id, "DeleteGeneratedCopy", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
      mutation.Started = true;
      GarminAdapterMessage deletion = await adapter.DeleteAsync(tokens, candidate.RemoteId, cancellationToken);
      if (string.Equals(deletion.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(deletion.TokenStore))
      {
        await store.ContinueGeneratedCopyCleanupAsync(job.Id, connections.Protect(deletion.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
        return;
      }
      await HandleMutationResultAsync(job, deletion, "Garmin did not confirm removal of a FIT-proven generated copy during Undo merge.", cancellationToken);
      return;
    }

    if (!retainedWatchVerified || !retainedLocalVerified)
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        !retainedWatchVerified ? job.MatchedRemoteId : job.RemoteId,
        $"Undo could not freshly FIT-verify both retained source activities (watch={retainedWatchVerified}, local={retainedLocalVerified}). No additional activity was deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    await store.CompleteUndoAsync(
      job.Id,
      $"Undo complete: watch-original {job.MatchedRemoteId} and plain local {job.RemoteId} are retained; only exact original, merged-replacement, and local duplicates were removed. The local TreadmillRunner session was unchanged.",
      connections.Protect(tokens),
      timeProvider.GetUtcNow(),
      cancellationToken,
      expectedLeaseExpiresAtUtc: RequiredLease(job));
  }

  private static bool FilesMatch(string expectedPath, string candidatePath)
  {
    if (!File.Exists(expectedPath) || !File.Exists(candidatePath)) return false;
    byte[] expected = SHA256.HashData(File.ReadAllBytes(expectedPath));
    byte[] candidate = SHA256.HashData(File.ReadAllBytes(candidatePath));
    return CryptographicOperations.FixedTimeEquals(expected, candidate);
  }

  private async Task DeleteMatchedOriginalAsync(
    GarminActivityUploadJob job,
    string tokens,
    string watchFitPath,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.ReplacementRemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "Replacement state is incomplete; no Garmin activity was deleted.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (!backups.TryGetRecoveryOriginalPath(job.Id, out _))
    {
      await store.MarkUnknownAsync(job.Id, "The merged replacement exists, but the original Garmin FIT is not backed up locally; no Garmin activity was deleted.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    GarminAdapterMessage replacementDownload = await adapter.DownloadOriginalAsync(
      tokens,
      job.ReplacementRemoteId,
      watchFitPath,
      cancellationToken);
    if (!string.Equals(replacementDownload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
        string.IsNullOrWhiteSpace(replacementDownload.TokenStore))
    {
      await HandleReadFailureAsync(job, replacementDownload.Kind, replacementDownload.Message, cancellationToken);
      return;
    }
    tokens = replacementDownload.TokenStore;
    if (!backups.MatchesRecoveryReplacement(job.Id, watchFitPath))
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        job.ReplacementRemoteId,
        "The retained merged Garmin activity no longer matches the saved merged FIT. The original activity was not deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, job.MatchedRemoteId, watchFitPath, cancellationToken);
    if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
    {
      await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
      return;
    }
    tokens = download.TokenStore;
    if (!backups.MatchesRecoveryOriginal(job.Id, watchFitPath))
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        job.MatchedRemoteId,
        "The Garmin activity at the matched watch ID no longer matches the backed-up original FIT. No deletion was attempted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }

    await store.MarkUploadStartedAsync(job.Id, "DeleteOriginal", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
    GarminAdapterMessage result = await adapter.DeleteAsync(tokens, job.MatchedRemoteId, cancellationToken);
    if (string.Equals(result.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.TokenStore))
      await store.MarkOriginalDeletedAwaitingResyncAsync(job.Id, connections.Protect(result.TokenStore), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
    else
      await HandleMutationResultAsync(job, result, "The merged activity exists, but Garmin did not confirm removal of the original; review both activities manually.", cancellationToken);
  }

  private async Task VerifyResyncAsync(
    GarminActivityUploadJob job,
    StoredWorkoutSession session,
    string tokens,
    string watchFitPath,
    MutationState mutation,
    CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.ReplacementRemoteId))
    {
      await store.MarkUnknownAsync(job.Id, "Replacement state is incomplete; resync cleanup could not be verified.", timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    GarminAdapterSearchMessage search = await adapter.SearchWatchActivitiesAsync(tokens, session.StartedAt!.Value, cancellationToken);
    if (!string.Equals(search.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(search.TokenStore))
    {
      await HandleReadFailureAsync(job, search.Kind, search.Message, cancellationToken);
      return;
    }
    tokens = search.TokenStore;
    GarminActivityMatchReference local = ToMatchReference(session);
    GarminWatchActivityCandidate? retainedReplacement = (search.Candidates ?? []).FirstOrDefault(candidate =>
      string.Equals(candidate.RemoteId, job.ReplacementRemoteId, StringComparison.Ordinal));
    if (retainedReplacement is null)
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        job.ReplacementRemoteId,
        $"The retained merged Garmin activity {job.ReplacementRemoteId} was not visible during verification. No other activity was deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    GarminAdapterMessage retainedDownload = await adapter.DownloadOriginalAsync(
      tokens,
      retainedReplacement.RemoteId,
      watchFitPath,
      cancellationToken);
    if (!string.Equals(retainedDownload.State, "confirmed", StringComparison.OrdinalIgnoreCase) ||
        string.IsNullOrWhiteSpace(retainedDownload.TokenStore))
    {
      await HandleReadFailureAsync(job, retainedDownload.Kind, retainedDownload.Message, cancellationToken);
      return;
    }
    tokens = retainedDownload.TokenStore;
    if (!backups.MatchesRecoveryReplacement(job.Id, watchFitPath))
    {
      await store.MarkReviewRequiredAsync(
        job.Id,
        retainedReplacement.RemoteId,
        "The retained merged Garmin activity failed exact FIT verification. No other activity was deleted.",
        timeProvider.GetUtcNow(),
        cancellationToken,
        expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    foreach (GarminWatchActivityCandidate candidate in (search.Candidates ?? [])
      .Where(item => !string.Equals(item.RemoteId, job.ReplacementRemoteId, StringComparison.Ordinal) && GarminWatchActivityMatcher.IsPlausibleShape(local, item)))
    {
      GarminAdapterMessage download = await adapter.DownloadOriginalAsync(tokens, candidate.RemoteId, watchFitPath, cancellationToken);
      if (!string.Equals(download.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(download.TokenStore))
      {
        await HandleReadFailureAsync(job, download.Kind, download.Message, cancellationToken);
        return;
      }
      tokens = download.TokenStore;
      if (!backups.MatchesRecoveryOriginal(job.Id, watchFitPath)) continue;
      await store.MarkUploadStartedAsync(
        job.Id, "DeleteResyncedOriginal", RequiredLease(job), timeProvider.GetUtcNow(), cancellationToken);
      mutation.Started = true;
      GarminAdapterMessage deletion = await adapter.DeleteAsync(tokens, candidate.RemoteId, cancellationToken);
      if (!string.Equals(deletion.State, "confirmed", StringComparison.OrdinalIgnoreCase) || string.IsNullOrWhiteSpace(deletion.TokenStore))
      {
        await HandleMutationResultAsync(job, deletion, "Garmin did not confirm removal of a watch activity re-created after replacement.", cancellationToken);
        return;
      }
      tokens = deletion.TokenStore;
      mutation.Started = false;
    }
    await store.CompleteResyncCheckAsync(job.Id, connections.Protect(tokens), timeProvider.GetUtcNow(), cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
  }

  private async Task HandleReadFailureAsync(GarminActivityUploadJob job, string? kind, string? message, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    if (string.Equals(kind, "provider-unavailable", StringComparison.OrdinalIgnoreCase))
      await store.MarkProviderUnavailableAsync(job.Id, message ?? "The unsupported Garmin adapter dependency is unavailable.", now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
    else
      await store.MarkFailedAsync(job.Id, message ?? "Garmin watch activity lookup failed before any account mutation.", string.Equals(kind, "authentication", StringComparison.OrdinalIgnoreCase), now.Add(string.Equals(kind, "rate-limit", StringComparison.OrdinalIgnoreCase) ? TimeSpan.FromMinutes(15) : TimeSpan.FromMinutes(2)), now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
  }

  private async Task HandleMutationResultAsync(GarminActivityUploadJob job, GarminAdapterMessage result, string fallbackMessage, CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    if (string.Equals(result.State, "confirmed", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(result.TokenStore))
    {
      await store.MarkConfirmedAsync(job.Id, result.RemoteId, connections.Protect(result.TokenStore), now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (string.Equals(result.State, "unknown", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkUnknownAsync(job.Id, result.Message ?? fallbackMessage, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (string.Equals(result.Kind, "provider-unavailable", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkProviderUnavailableAsync(job.Id, result.Message ?? fallbackMessage, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    if (string.Equals(result.Kind, "duplicate", StringComparison.OrdinalIgnoreCase) || string.Equals(result.Kind, "rejected", StringComparison.OrdinalIgnoreCase))
    {
      await store.MarkRejectedAsync(job.Id, result.Kind!.ToLowerInvariant(), result.Message ?? fallbackMessage, now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
      return;
    }
    bool authentication = string.Equals(result.Kind, "authentication", StringComparison.OrdinalIgnoreCase);
    TimeSpan delay = string.Equals(result.Kind, "rate-limit", StringComparison.OrdinalIgnoreCase) ? TimeSpan.FromMinutes(15) : TimeSpan.FromMinutes(Math.Pow(2, Math.Max(0, job.AttemptCount - 1)));
    await store.MarkFailedAsync(job.Id, result.Message ?? fallbackMessage, authentication, now.Add(delay), now, cancellationToken, expectedLeaseExpiresAtUtc: RequiredLease(job));
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

  private static DateTimeOffset RequiredLease(GarminActivityUploadJob job) =>
    job.LeaseExpiresAtUtc ?? throw new InvalidOperationException("A Garmin mutation requires the lease that authorized it.");

  private sealed class MutationState
  {
    public bool Started { get; set; }
    public string? MatchedRemoteId { get; set; }
    public string? MatchEvidence { get; set; }
    public bool OriginalUploadStarted { get; set; }
    public bool LocalUploadStarted { get; set; }
  }

  private sealed record ReplacementCandidateEvidence(
    GarminWatchActivityCandidate Candidate,
    bool MatchesMergedBackup,
    bool MatchesOriginalBackup,
    bool MatchesLocalExport)
  {
    public bool IsProvenCopy => MatchesMergedBackup || MatchesOriginalBackup || MatchesLocalExport;
  }
}
