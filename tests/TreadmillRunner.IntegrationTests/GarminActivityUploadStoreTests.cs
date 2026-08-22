using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminActivityUploadStoreTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string DatabasePath => Path.Combine(directory, "garmin-activity.db");
  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Fact]
  public async Task Completed_session_is_queued_once_and_unknown_outcome_is_never_retried()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, Guid sessionId) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await store.ConnectAsync(profileId, "marc", "protected-token-json", enabled: true, now.AddHours(-2));

    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    await store.ReconcileCompletedSessionsAsync(now);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    Assert.Equal(sessionId, leased.WorkoutSessionId);
    Assert.Equal(1, leased.AttemptCount);
    await store.MarkUploadStartedAsync(leased.Id, "Upload", now.AddSeconds(1));
    Assert.Null(await store.LeaseNextAsync(now.AddMinutes(3), TimeSpan.FromMinutes(2)));

    Assert.Null(await store.LeaseNextAsync(now.AddHours(1), TimeSpan.FromMinutes(2)));
    Assert.False(await store.RetryFailedAsync(leased.Id, profileId, now.AddHours(1)));
    GarminActivityUploadStatus status = await store.GetStatusAsync(profileId);
    Assert.Equal(1, status.Unknown);
    Assert.True(await store.DismissAsync(leased.Id, profileId, now.AddHours(1)));
    Assert.Equal(0, (await store.GetStatusAsync(profileId)).Unknown);
  }

  [Fact]
  public async Task Enable_watermark_skips_old_history_and_atomic_lease_has_one_winner()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, _) = await SeedCompletedSessionAsync(factory, "Marc");
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    var first = new GarminActivityUploadStore(factory);
    GarminActivityUploadAccount account = await first.ConnectAsync(profileId, "marc", "protected-token-json", enabled: true, now);
    Assert.Equal(0, await first.ReconcileCompletedSessionsAsync(now));
    Assert.True(await first.DisconnectAsync(profileId, account.Version));
    await first.ConnectAsync(profileId, "marc", "protected-token-json", enabled: true, now.AddMinutes(1));
    Assert.Equal(0, await first.ReconcileCompletedSessionsAsync(now.AddMinutes(1)));

    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.EndedAtUtc = now.AddMinutes(2);
      session.StartedAtUtc = now.AddMinutes(1);
      await context.SaveChangesAsync();
    }
    Assert.True(await first.ReconcileCompletedSessionsAsync(now.AddMinutes(3)) > 0);
    var second = new GarminActivityUploadStore(factory);
    GarminActivityUploadJob?[] leases = await Task.WhenAll(
      first.LeaseNextAsync(now.AddMinutes(8), TimeSpan.FromMinutes(2)),
      second.LeaseNextAsync(now.AddMinutes(8), TimeSpan.FromMinutes(2)));
    Assert.Single(leases, lease => lease is not null);
  }

  [Fact]
  public async Task Account_enablement_and_watch_binding_are_isolated_per_profile()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid marc, _) = await SeedCompletedSessionAsync(factory, "Marc");
    (Guid partner, _) = await SeedCompletedSessionAsync(factory, "Partner");
    var uploads = new GarminActivityUploadStore(factory);
    var watches = new GarminWatchBindingStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    GarminActivityUploadAccount account = await uploads.ConnectAsync(marc, "marc", "protected-token-json", false, now);
    await watches.ReplaceAsync(marc, "Fenix 8", new string('a', 64), now);
    await watches.ReplaceAsync(partner, "Vivoactive", new string('b', 64), now);

    Assert.False((await uploads.GetStatusAsync(marc)).Enabled);
    Assert.False((await uploads.GetStatusAsync(partner)).Connected);
    GarminActivityUploadAccount enabled = await uploads.SetSettingsAsync(marc, true, GarminWatchActivityHandling.MergeAndReplace, account.Version, now.AddMinutes(1));
    Assert.True(enabled.Enabled);
    Assert.Equal(GarminWatchActivityHandling.MergeAndReplace, enabled.WatchActivityHandling);
    Assert.Equal("Fenix 8", (await watches.FindByTokenHashAsync(new string('a', 64), now.AddMinutes(2)))!.DeviceLabel);
    Assert.Equal("Vivoactive", (await watches.FindForProfileAsync(partner))!.DeviceLabel);
  }

  [Fact]
  public async Task Disconnect_is_rejected_while_upload_is_in_flight_and_succeeds_after_terminal_outcome()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, _) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    GarminActivityUploadAccount account = await store.ConnectAsync(profileId, "marc", "protected-token-json", true, now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));

    InvalidOperationException error = await Assert.ThrowsAsync<InvalidOperationException>(() => store.DisconnectAsync(profileId, account.Version));
    Assert.Contains("cannot be cancelled", error.Message, StringComparison.OrdinalIgnoreCase);
    Assert.NotNull(await store.FindAccountAsync(profileId));

    await store.MarkUnknownAsync(leased.Id, "Confirmation was lost.", now.AddSeconds(1));
    GarminActivityUploadAccount current = Assert.IsType<GarminActivityUploadAccount>(await store.FindAccountAsync(profileId));
    Assert.True(await store.DisconnectAsync(profileId, current.Version));
    Assert.Null(await store.FindAccountAsync(profileId));
  }

  [Fact]
  public async Task Unknown_job_can_be_acknowledged_as_found_without_provider_confirmation_or_retry()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, _) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await store.ConnectAsync(profileId, "marc", "protected-token-json", true, now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await store.MarkUnknownAsync(leased.Id, "Confirmation was lost.", now.AddSeconds(1));

    GarminActivityUploadJob found = await store.AcknowledgeFoundInGarminAsync(
      leased.Id, profileId, Guid.NewGuid(), new string('a', 64), now.AddMinutes(1));

    Assert.Equal("FoundInGarmin", found.Status);
    Assert.Equal(now.AddMinutes(1), found.AcknowledgedAtUtc);
    Assert.Null(found.RemoteId);
    Assert.False(found.CanRetry);
    Assert.Null(await store.LeaseNextAsync(now.AddHours(1), TimeSpan.FromMinutes(2)));
    Assert.False(await store.RetryFailedAsync(found.Id, profileId, now.AddHours(1)));
  }

  [Fact]
  public async Task Unknown_upload_verified_absent_can_be_idempotently_requeued_from_watch_search()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, _) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await store.ConnectAsync(
      profileId,
      "marc",
      "protected-token-json",
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await store.MarkUploadStartedAsync(leased.Id, "ReplacementUpload", now.AddSeconds(1));
    await store.MarkUnknownAsync(leased.Id, "The response was interrupted.", now.AddSeconds(2));

    Guid operationId = Guid.NewGuid();
    string fingerprint = new('d', 64);
    GarminActivityUploadJob requeued = await store.RetryUnknownVerifiedAbsentAsync(
      leased.Id, profileId, operationId, fingerprint, now.AddMinutes(6));

    Assert.Equal("Pending", requeued.Status);
    Assert.Equal("WatchSearch", requeued.OperationPhase);
    Assert.Equal(0, requeued.AttemptCount);
    GarminActivityUploadJob reLeased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now.AddMinutes(6), TimeSpan.FromMinutes(2)));
    Assert.Equal(leased.Id, reLeased.Id);
    await Assert.ThrowsAsync<OperationReplayException>(() => store.RetryUnknownVerifiedAbsentAsync(
      leased.Id, profileId, operationId, fingerprint, now.AddMinutes(7)));
  }

  [Fact]
  public async Task Duplicate_can_be_retried_from_watch_search_after_the_user_removes_the_existing_import()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, _) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await store.ConnectAsync(
      profileId,
      "marc",
      "protected-token-json",
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await store.MarkUploadStartedAsync(leased.Id, "Upload", now.AddSeconds(1));
    await store.MarkRejectedAsync(leased.Id, "duplicate", "Garmin reports that this activity already exists.", now.AddSeconds(2));

    GarminActivityUploadJob duplicate = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.True(duplicate.CanRetry);
    Assert.True(await store.RetryFailedAsync(leased.Id, profileId, now.AddMinutes(1)));
    GarminActivityUploadJob retried = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now.AddMinutes(1), TimeSpan.FromMinutes(2)));
    Assert.Equal("WatchSearch", retried.OperationPhase);
    Assert.Equal(1, retried.AttemptCount);
  }

  [Fact]
  public async Task Legacy_confirmed_job_can_be_idempotently_requeued_only_for_merge_and_replace()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateDatabaseAsync();
    (Guid profileId, Guid sessionId) = await SeedCompletedSessionAsync(factory, "Marc");
    var store = new GarminActivityUploadStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await store.ConnectAsync(
      profileId,
      "marc",
      "protected-token-json",
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await store.MarkConfirmedAsync(leased.Id, remoteId: null, protectedTokenStore: "protected-token-json", nowUtc: now.AddSeconds(1));

    Guid operationId = Guid.NewGuid();
    string fingerprint = new('b', 64);
    GarminActivityUploadJob requeued = await store.ReprocessLegacyConfirmedForMergeAsync(
      leased.Id,
      profileId,
      operationId,
      fingerprint,
      now.AddMinutes(1));

    Assert.Equal(sessionId, requeued.WorkoutSessionId);
    Assert.Equal("Pending", requeued.Status);
    Assert.Equal("WatchSearch", requeued.OperationPhase);
    Assert.Equal(0, requeued.AttemptCount);
    GarminActivityUploadJob reLeased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now.AddMinutes(1), TimeSpan.FromMinutes(2)));
    Assert.Equal(leased.Id, reLeased.Id);
    await Assert.ThrowsAsync<OperationReplayException>(() => store.ReprocessLegacyConfirmedForMergeAsync(
      leased.Id,
      profileId,
      operationId,
      fingerprint,
      now.AddMinutes(2)));
  }

  private async Task<IDbContextFactory<TreadmillRunnerDbContext>> CreateDatabaseAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    return factory;
  }

  private static async Task<(Guid ProfileId, Guid SessionId)> SeedCompletedSessionAsync(IDbContextFactory<TreadmillRunnerDbContext> factory, string name)
  {
    Guid profileId = Guid.NewGuid(), workoutId = Guid.NewGuid(), revisionId = Guid.NewGuid(), sessionId = Guid.NewGuid();
    DateTimeOffset start = DateTimeOffset.Parse("2026-08-05T07:00:00Z");
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    context.UserProfiles.Add(new UserProfileEntity { Id = profileId, DisplayName = name, NormalizedDisplayName = name.ToUpperInvariant(), UnitSystem = "Metric", WeightKilograms = 70, Version = 1, CreatedAtUtc = start, UpdatedAtUtc = start });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = "Easy run", Kind = "Structured", CreatedAtUtc = start });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity { Id = revisionId, WorkoutId = workoutId, RevisionNumber = 1, DefinitionJson = "{}", ContentSha256 = new string('c', 64), CreatedAtUtc = start });
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = sessionId,
      UserProfileId = profileId,
      UserProfileName = name,
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Easy run",
      State = "Completed",
      ArmedAtUtc = start,
      StartedAtUtc = start,
      EndedAtUtc = start.AddMinutes(20),
      DurationSeconds = 1200,
      DistanceKilometers = 2.5,
      MetricAlgorithmVersion = "v1",
      ControllerConfigurationJson = "{}",
    });
    await context.SaveChangesAsync();
    return (profileId, sessionId);
  }
}
