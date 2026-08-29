using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Dynastream.Fit;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminActivityUploadWorkerTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Theory]
  [InlineData(AdapterMode.Confirmed, "Confirmed", true, false, "Connected")]
  [InlineData(AdapterMode.Duplicate, "Failed", false, true, "Connected")]
  [InlineData(AdapterMode.Ambiguous, "Unknown", false, false, "Connected")]
  [InlineData(AdapterMode.Timeout, "Unknown", false, false, "Connected")]
  [InlineData(AdapterMode.Unavailable, "Failed", false, true, "ProviderUnavailable")]
  public async Task Worker_maps_adapter_outcomes_without_unsafe_retry(
    AdapterMode mode,
    string expectedJobStatus,
    bool expectedConfirmed,
    bool expectedRetry,
    string expectedAccountState)
  {
    string databasePath = Path.Combine(directory, $"worker-{mode}-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z");
    await SeedCompletedSessionAsync(factory, profileId, now.AddHours(-1));
    var store = new GarminActivityUploadStore(factory);
    var adapter = new OutcomeAdapter(mode);
    await using var connections = new GarminActivityConnectionService(
      adapter, store, DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))), TimeProvider.System);
    await store.ConnectAsync(profileId, "marc", connections.Protect("original-token-store"), true, now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));

    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(factory)
      .AddScoped<ISessionStore, SessionStore>()
      .BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(
      services.GetRequiredService<IServiceScopeFactory>(), store, adapter, BackupStore(TimeProvider.System), connections,
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<GarminActivityUploadWorker>.Instance);
    await worker.ProcessOneAsync(leased, default);

    GarminActivityUploadStatus status = await store.GetStatusAsync(profileId);
    GarminActivityUploadJob job = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal(expectedJobStatus, job.Status);
    Assert.Equal(expectedConfirmed ? 1 : 0, status.Confirmed);
    Assert.Equal(expectedRetry, job.CanRetry);
    Assert.Equal(expectedAccountState, (await store.FindAccountAsync(profileId))!.State);
    Assert.True(adapter.SawReadableFit);
    Assert.Null(await store.LeaseNextAsync(now.AddHours(1), TimeSpan.FromMinutes(2)));
  }

  [Theory]
  [InlineData(GarminWatchActivityHandling.PreferWatch, "FoundInGarmin", true)]
  [InlineData(GarminWatchActivityHandling.MergeAndReplace, "Confirmed", true)]
  [InlineData(GarminWatchActivityHandling.MergeAndReplace, "Pending", false)]
  public async Task Exact_watch_match_uses_the_selected_per_profile_flow(string handling, string expectedStatus, bool replacementHasId)
  {
    string databasePath = Path.Combine(directory, $"matching-{handling}-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new OutcomeAdapter(AdapterMode.Confirmed, returnMatch: true, replacementHasId);
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter, store, DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))), clock);
    await store.ConnectAsync(profileId, "marc", connections.Protect("original-token-store"), true, handling, now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await using ServiceProvider services = new ServiceCollection().AddSingleton(factory).AddScoped<ISessionStore, SessionStore>().BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(services.GetRequiredService<IServiceScopeFactory>(), store, adapter, BackupStore(clock), connections, clock, new ApplicationMaintenanceState(), NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);
    if (handling == GarminWatchActivityHandling.MergeAndReplace && replacementHasId)
    {
      GarminActivityUploadJob deleteLease = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now.AddSeconds(1), TimeSpan.FromMinutes(2)));
      Assert.Equal("DeleteOriginal", deleteLease.OperationPhase);
      await worker.ProcessOneAsync(deleteLease, default);
      for (var check = 0; check < 3; check++)
      {
        GarminActivityUploadJob verifyLease = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now.AddDays(1), TimeSpan.FromMinutes(2)));
        Assert.Equal("VerifyResync", verifyLease.OperationPhase);
        await worker.ProcessOneAsync(verifyLease, default);
      }
      Assert.Equal(new[]
      {
        "search", "download", "upload",
        "download", "download", "delete",
        "search", "download", "download", "delete",
        "search", "download",
        "search", "download",
      }, adapter.Calls);
      Assert.Equal(3, Directory.GetFiles(Path.Combine(directory, "garmin-backups"), "*.fit").Length);
    }

    GarminActivityUploadJob completed = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal(expectedStatus, completed.Status);
    Assert.Equal("watch-123", completed.MatchedRemoteId);
    if (handling == GarminWatchActivityHandling.PreferWatch)
      Assert.Equal(new[] { "search" }, adapter.Calls);
    else if (replacementHasId)
      Assert.Equal("replacement-456", completed.RemoteId);
    else
    {
      Assert.Equal("ResolveReplacement", completed.OperationPhase);
      Assert.Equal(new[] { "search", "download", "upload" }, adapter.Calls);
    }
  }

  [Fact]
  public async Task Unique_watch_match_without_local_heart_rate_uses_treadmill_shape()
  {
    string databasePath = Path.Combine(directory, $"review-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started, includeHeartRate: false);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new OutcomeAdapter(AdapterMode.Confirmed, returnMatch: true);
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter, store, DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))), clock);
    await store.ConnectAsync(profileId, "marc", connections.Protect("original-token-store"), true, now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await using ServiceProvider services = new ServiceCollection().AddSingleton(factory).AddScoped<ISessionStore, SessionStore>().BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(services.GetRequiredService<IServiceScopeFactory>(), store, adapter, BackupStore(clock), connections, clock, new ApplicationMaintenanceState(), NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);

    GarminActivityUploadJob completed = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("FoundInGarmin", completed.Status);
    Assert.Equal("WatchSearch", completed.OperationPhase);
    Assert.Equal("watch-123", completed.MatchedRemoteId);
    Assert.Equal("watch-match", completed.FailureKind);
    Assert.DoesNotContain("heart-rate", completed.MatchEvidence, StringComparison.OrdinalIgnoreCase);
    Assert.Equal(new[] { "search" }, adapter.Calls);
    Assert.False(adapter.SawReadableFit);
    Assert.Null(await store.LeaseNextAsync(now.AddHours(1), TimeSpan.FromMinutes(2)));
  }

  [Fact]
  public async Task Multiple_plausible_watch_activities_require_review_without_uploading_a_third_copy()
  {
    string databasePath = Path.Combine(directory, $"multiple-review-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new OutcomeAdapter(AdapterMode.Confirmed, returnMatch: true, initialMatchCount: 2);
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter, store, DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))), clock);
    await store.ConnectAsync(
      profileId,
      "marc",
      connections.Protect("original-token-store"),
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(factory)
      .AddScoped<ISessionStore, SessionStore>()
      .BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(
      services.GetRequiredService<IServiceScopeFactory>(), store, adapter, BackupStore(clock), connections,
      clock, new ApplicationMaintenanceState(), NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);

    GarminActivityUploadJob review = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("ReviewRequired", review.Status);
    Assert.Equal("Review", review.OperationPhase);
    Assert.Contains("2 Garmin treadmill activities are plausible", Assert.IsType<string>(review.MatchEvidence));
    Assert.Equal(new[] { "search" }, adapter.Calls);
    Assert.False(adapter.SawReadableFit);
    Assert.Null(await store.LeaseNextAsync(now.AddHours(1), TimeSpan.FromMinutes(2)));
  }

  private static async Task SeedCompletedSessionAsync(
    IDbContextFactory<TreadmillRunnerDbContext> factory,
    Guid profileId,
    DateTimeOffset started,
    bool includeHeartRate = true)
  {
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    Guid workoutId = Guid.NewGuid(), revisionId = Guid.NewGuid(), sessionId = Guid.NewGuid();
    context.UserProfiles.Add(new UserProfileEntity { Id = profileId, DisplayName = "Marc", NormalizedDisplayName = "MARC", UnitSystem = "Metric", WeightKilograms = 70, Version = 1, CreatedAtUtc = started, UpdatedAtUtc = started });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = "Worker test", Kind = "Structured", CreatedAtUtc = started });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity { Id = revisionId, WorkoutId = workoutId, RevisionNumber = 1, DefinitionJson = "{}", ContentSha256 = new string('d', 64), CreatedAtUtc = started });
    var session = new WorkoutSessionEntity
    {
      Id = sessionId,
      UserProfileId = profileId,
      UserProfileName = "Marc",
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Worker test",
      State = "Completed",
      ArmedAtUtc = started.AddSeconds(-5),
      StartedAtUtc = started,
      EndedAtUtc = started.AddMinutes(1),
      DurationSeconds = 60,
      DistanceKilometers = 0.1,
      EstimatedCalories = 8,
      AverageHeartRateBpm = includeHeartRate ? 130 : null,
      MaximumHeartRateBpm = includeHeartRate ? 140 : null,
      AverageSpeedKph = 6,
      AverageInclinePercent = 1,
      MetricAlgorithmVersion = "v1",
      ControllerConfigurationJson = "{}",
    };
    session.Samples.Add(new SessionSampleEntity
    {
      WorkoutSessionId = sessionId,
      Sequence = 0,
      CapturedAtUtc = started.AddSeconds(1),
      ElapsedMilliseconds = 1000,
      PlannedSpeedKph = 6,
      RequestedSpeedKph = 6,
      MeasuredSpeedKph = 6,
      PlannedInclinePercent = 1,
      RequestedInclinePercent = 1,
      MeasuredInclinePercent = 1,
      HeartRateBpm = includeHeartRate ? (ushort)130 : null,
      DistanceKilometers = .0017,
      EstimatedCalories = .13,
      TelemetryAgeMilliseconds = 10,
      MetricAlgorithmVersion = "v1",
    });
    context.WorkoutSessions.Add(session);
    await context.SaveChangesAsync();
  }

  private GarminActivityBackupStore BackupStore(TimeProvider clock) => new(
    new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
    {
      ["GarminActivityUpload:BackupRoot"] = Path.Combine(directory, "garmin-backups"),
    }).Build(),
    clock);

  public enum AdapterMode { Confirmed, Duplicate, Ambiguous, Timeout, Unavailable }

  private sealed class OutcomeAdapter(
    AdapterMode mode,
    bool returnMatch = false,
    bool replacementHasId = true,
    int initialMatchCount = 1) : IGarminActivityAdapter
  {
    public bool SawReadableFit { get; private set; }
    public List<string> Calls { get; } = [];
    private DateTimeOffset searchedStart;
    private readonly HashSet<string> deletedRemoteIds = [];
    private byte[]? uploadedFit;
    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<GarminAdapterSearchMessage> SearchWatchActivitiesAsync(string tokenStore, DateTimeOffset startedAtUtc, CancellationToken cancellationToken)
    {
      Calls.Add("search");
      searchedStart = startedAtUtc;
      IReadOnlyList<GarminWatchActivityCandidate> candidates = !returnMatch
        ? []
        : initialMatchCount > 1
          ? [
            new("watch-123", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, []),
            new("watch-456", "treadmill_running", startedAtUtc.AddSeconds(20), 60, .1, 131, 141, []),
          ]
        : !deletedRemoteIds.Contains("watch-123")
          ? [new("watch-123", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, [])]
          : !deletedRemoteIds.Contains("watch-resynced")
            ? [new("replacement-456", "treadmill_running", startedAtUtc, 60, .1, 130, 140, []), new("watch-resynced", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, [])]
            : [new("replacement-456", "treadmill_running", startedAtUtc, 60, .1, 130, 140, [])];
      return Task.FromResult(new GarminAdapterSearchMessage("confirmed", null, null, "searched-token-store", candidates));
    }
    public Task<GarminAdapterMessage> DownloadOriginalAsync(string tokenStore, string remoteId, string outputPath, CancellationToken cancellationToken)
    {
      Calls.Add("download");
      if (string.Equals(remoteId, "replacement-456", StringComparison.Ordinal) && uploadedFit is not null)
      {
        System.IO.File.WriteAllBytes(outputPath, uploadedFit);
        return Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "download-token-store", remoteId));
      }
      using var stream = new MemoryStream();
      var encoder = new Encode(ProtocolVersion.V20);
      encoder.Open(stream);
      var file = new FileIdMesg(); file.SetType(Dynastream.Fit.File.Activity); file.SetTimeCreated(new Dynastream.Fit.DateTime(searchedStart.UtcDateTime)); encoder.Write(file);
      var record = new RecordMesg(); record.SetTimestamp(new Dynastream.Fit.DateTime(searchedStart.UtcDateTime)); record.SetHeartRate(130); encoder.Write(record);
      var session = new SessionMesg(); session.SetTimestamp(new Dynastream.Fit.DateTime(searchedStart.AddMinutes(1).UtcDateTime)); session.SetStartTime(new Dynastream.Fit.DateTime(searchedStart.UtcDateTime)); encoder.Write(session);
      encoder.Close();
      System.IO.File.WriteAllBytes(outputPath, stream.ToArray());
      return Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "download-token-store", remoteId));
    }
    public Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken)
    {
      SawReadableFit = System.IO.File.Exists(activityPath) && new FileInfo(activityPath).Length > 16;
      uploadedFit = System.IO.File.ReadAllBytes(activityPath);
      Calls.Add("upload");
      return mode switch
      {
        AdapterMode.Confirmed => Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "updated-token-store", returnMatch ? replacementHasId ? "replacement-456" : null : "12345")),
        AdapterMode.Duplicate => Task.FromResult(new GarminAdapterMessage("failed", "duplicate", "Garmin reports that this activity already exists.", null, null, null)),
        AdapterMode.Ambiguous => Task.FromException<GarminAdapterMessage>(new GarminAdapterAmbiguousResultException("missing response")),
        AdapterMode.Timeout => Task.FromException<GarminAdapterMessage>(new TimeoutException("adapter timed out")),
        AdapterMode.Unavailable => Task.FromException<GarminAdapterMessage>(new GarminAdapterUnavailableException("python missing")),
        _ => throw new ArgumentOutOfRangeException(),
      };
    }
    public Task<GarminAdapterMessage> DeleteAsync(string tokenStore, string remoteId, CancellationToken cancellationToken)
    {
      Calls.Add("delete");
      deletedRemoteIds.Add(remoteId);
      return Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "deleted-token-store", remoteId));
    }
  }

  [Fact]
  public async Task No_id_replacement_uses_read_only_resolution_and_never_uploads_again()
  {
    string databasePath = Path.Combine(directory, $"replacement-resolution-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new OutcomeAdapter(AdapterMode.Confirmed, returnMatch: true, replacementHasId: false);
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter,
      store,
      DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))),
      clock);
    await store.ConnectAsync(
      profileId,
      "marc",
      connections.Protect("original-token-store"),
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(factory)
      .AddScoped<ISessionStore, SessionStore>()
      .BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(
      services.GetRequiredService<IServiceScopeFactory>(),
      store,
      adapter,
      BackupStore(clock),
      connections,
      clock,
      new ApplicationMaintenanceState(),
      NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);

    GarminActivityUploadJob awaitingResolution = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("Pending", awaitingResolution.Status);
    Assert.Equal("ResolveReplacement", awaitingResolution.OperationPhase);
    Assert.Equal("watch-123", awaitingResolution.MatchedRemoteId);
    Assert.Null(awaitingResolution.ReplacementRemoteId);
    Assert.Equal(1, adapter.Calls.Count(call => call == "upload"));

    // The resolver is read-only. Exhaust its durable checks so the user-facing retry path is exercised.
    for (var check = 0; check < 3; check++)
    {
      GarminActivityUploadJob resolutionLease = Assert.IsType<GarminActivityUploadJob>(
        await store.LeaseNextAsync(now.AddMinutes(20 + check), TimeSpan.FromMinutes(2)));
      Assert.Equal("ResolveReplacement", resolutionLease.OperationPhase);
      await worker.ProcessOneAsync(resolutionLease, default);
    }

    GarminActivityUploadJob unresolved = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("Unknown", unresolved.Status);
    Assert.Equal("ResolveReplacement", unresolved.OperationPhase);
    Assert.Equal("watch-123", unresolved.MatchedRemoteId);
    Assert.Null(unresolved.ReplacementRemoteId);
    Assert.Equal(1, adapter.Calls.Count(call => call == "upload"));

    GarminActivityUploadJob retry = await store.StartHistoricalRecoveryAsync(
      unresolved.Id,
      profileId,
      "MergeIntoOne",
      "watch-123",
      Guid.NewGuid(),
      new string('r', 64),
      now.AddMinutes(30));
    Assert.Equal("Pending", retry.Status);
    Assert.Equal("ResolveReplacement", retry.OperationPhase);
    Assert.Equal(0, retry.AttemptCount);

    GarminActivityUploadJob retryLease = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now.AddMinutes(30), TimeSpan.FromMinutes(2)));
    await worker.ProcessOneAsync(retryLease, default);
    Assert.Equal(1, adapter.Calls.Count(call => call == "upload"));
  }

  [Fact]
  public async Task Replacement_resolution_keeps_one_corrected_copy_then_deletes_duplicate_and_original()
  {
    string databasePath = Path.Combine(directory, $"replacement-cleanup-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new ReplacementResolutionAdapter();
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter,
      store,
      DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))),
      clock);
    await store.ConnectAsync(
      profileId,
      "marc",
      connections.Protect("original-token-store"),
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
      await store.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(factory)
      .AddScoped<ISessionStore, SessionStore>()
      .BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(
      services.GetRequiredService<IServiceScopeFactory>(),
      store,
      adapter,
      BackupStore(clock),
      connections,
      clock,
      new ApplicationMaintenanceState(),
      NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);
    Assert.Equal("ResolveReplacement", (Assert.Single(await store.ListJobsAsync(profileId))).OperationPhase);

    DateTimeOffset tick = now.AddMinutes(20);
    for (var pass = 0; pass < 10; pass++)
    {
      GarminActivityUploadJob? next = await store.LeaseNextAsync(tick, TimeSpan.FromMinutes(2));
      if (next is null) break;
      await worker.ProcessOneAsync(next, default);
      tick = tick.AddMinutes(30);
    }

    GarminActivityUploadJob completed = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("Confirmed", completed.Status);
    Assert.Equal("watch-original", completed.MatchedRemoteId);
    Assert.Equal("corrected-1", completed.ReplacementRemoteId);
    Assert.Equal("corrected-1", completed.RemoteId);
    Assert.Equal(new[] { "corrected-2", "watch-original" }, adapter.DeletedRemoteIds);
    Assert.Equal(new[] { "corrected-1" }, adapter.ActiveRemoteIds);
    Assert.Equal(1, adapter.Calls.Count(call => call == "upload"));
  }

  [Theory]
  [InlineData(true)]
  [InlineData(false)]
  public async Task Undo_merge_restores_exactly_one_watch_original_and_one_plain_local_source(bool localRestoreHasId)
  {
    string databasePath = Path.Combine(directory, $"replacement-undo-{Guid.NewGuid():N}.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(databasePath);
    Guid profileId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-05T08:00:00Z"), started = now.AddHours(-1);
    await SeedCompletedSessionAsync(factory, profileId, started);
    var store = new GarminActivityUploadStore(factory);
    var adapter = new ReplacementResolutionAdapter(localRestoreHasId);
    var clock = new FixedTimeProvider(now);
    await using var connections = new GarminActivityConnectionService(
      adapter,
      store,
      DataProtectionProvider.Create(new DirectoryInfo(Path.Combine(directory, $"keys-{Guid.NewGuid():N}"))),
      clock);
    await store.ConnectAsync(
      profileId,
      "marc",
      connections.Protect("original-token-store"),
      enabled: true,
      watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
      nowUtc: now.AddHours(-2));
    Assert.True(await store.ReconcileCompletedSessionsAsync(now) > 0);
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(factory)
      .AddScoped<ISessionStore, SessionStore>()
      .BuildServiceProvider();
    var worker = new GarminActivityUploadWorker(
      services.GetRequiredService<IServiceScopeFactory>(),
      store,
      adapter,
      BackupStore(clock),
      connections,
      clock,
      new ApplicationMaintenanceState(),
      NullLogger<GarminActivityUploadWorker>.Instance);

    DateTimeOffset tick = now;
    for (var pass = 0; pass < 10; pass++)
    {
      GarminActivityUploadJob? next = await store.LeaseNextAsync(tick, TimeSpan.FromMinutes(2));
      if (next is null) break;
      await worker.ProcessOneAsync(next, default);
      tick = tick.AddMinutes(30);
    }
    GarminActivityUploadJob merged = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("Confirmed", merged.Status);
    Assert.Equal("corrected-1", merged.RemoteId);

    GarminActivityUploadJob undo = await store.StartHistoricalRecoveryAsync(
      merged.Id,
      profileId,
      "UndoMerge",
      "watch-original",
      Guid.NewGuid(),
      new string('u', 64),
      tick);
    Assert.Equal("Pending", undo.Status);
    Assert.Equal("ResolveOriginal", undo.OperationPhase);

    for (var pass = 0; pass < 10; pass++)
    {
      GarminActivityUploadJob? next = await store.LeaseNextAsync(tick, TimeSpan.FromMinutes(2));
      if (next is null) break;
      await worker.ProcessOneAsync(next, default);
      tick = tick.AddMinutes(30);
    }

    GarminActivityUploadJob restored = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.True(restored.Status == "Confirmed", $"status={restored.Status} phase={restored.OperationPhase} err={restored.LastError} evidence={restored.MatchEvidence} remote={restored.RemoteId} matched={restored.MatchedRemoteId} calls={string.Join(',', adapter.Calls)} deleted={string.Join(',', adapter.DeletedRemoteIds)}");
    Assert.Equal("UndoComplete", restored.OperationPhase);
    Assert.Equal("local-1", restored.RemoteId);
    Assert.Equal("restored-original", restored.MatchedRemoteId);
    Assert.Null(restored.ReplacementRemoteId);
    Assert.Equal(new[] { "corrected-2", "watch-original", "corrected-1" }, adapter.DeletedRemoteIds);
    Assert.Equal(new[] { "local-1", "restored-original" }, adapter.ActiveRemoteIds);
    Assert.Equal(3, adapter.Calls.Count(call => call == "upload"));

    GarminActivityUploadJob redo = await store.StartHistoricalRecoveryAsync(
      restored.Id,
      profileId,
      "MergeIntoOne",
      "watch-original",
      Guid.NewGuid(),
      new string('m', 64),
      tick);
    Assert.Equal("Pending", redo.Status);
    Assert.Equal("EnsureReplacement", redo.OperationPhase);
    Assert.Equal("restored-original", redo.MatchedRemoteId);

    for (var pass = 0; pass < 12; pass++)
    {
      GarminActivityUploadJob? next = await store.LeaseNextAsync(tick, TimeSpan.FromMinutes(2));
      if (next is null) break;
      await worker.ProcessOneAsync(next, default);
      tick = tick.AddMinutes(30);
    }

    GarminActivityUploadJob remerged = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal("Confirmed", remerged.Status);
    Assert.Equal("corrected-3", remerged.RemoteId);
    Assert.Equal("corrected-3", remerged.ReplacementRemoteId);
    Assert.Equal("restored-original", remerged.MatchedRemoteId);
    Assert.Equal(new[] { "corrected-2", "watch-original", "corrected-1", "local-1", "restored-original" }, adapter.DeletedRemoteIds);
    Assert.Equal(new[] { "corrected-3" }, adapter.ActiveRemoteIds);
    Assert.Equal(4, adapter.Calls.Count(call => call == "upload"));
  }

  private sealed class ReplacementResolutionAdapter(bool localRestoreHasId = true) : IGarminActivityAdapter
  {
    private readonly HashSet<string> activeRemoteIds = ["watch-original"];
    private DateTimeOffset searchedStart;
    private byte[]? mergedFit;
    private byte[]? localFit;
    private bool replacementVisible;

    public List<string> Calls { get; } = [];
    public List<string> DeletedRemoteIds { get; } = [];
    public IReadOnlyList<string> ActiveRemoteIds => activeRemoteIds.OrderBy(id => id).ToArray();

    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken) => throw new NotSupportedException();

    public Task<GarminAdapterSearchMessage> SearchWatchActivitiesAsync(
      string tokenStore,
      DateTimeOffset startedAtUtc,
      CancellationToken cancellationToken)
    {
      Calls.Add("search");
      searchedStart = startedAtUtc;
      var candidates = new List<GarminWatchActivityCandidate>();
      if (activeRemoteIds.Contains("watch-original"))
        candidates.Add(new("watch-original", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, []));
      if (activeRemoteIds.Contains("restored-original"))
        candidates.Add(new("restored-original", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, []));
      if (replacementVisible)
      {
        if (activeRemoteIds.Contains("corrected-1"))
          candidates.Add(new("corrected-1", "treadmill_running", startedAtUtc, 60, .1, 130, 140, []));
        if (activeRemoteIds.Contains("corrected-2"))
          candidates.Add(new("corrected-2", "treadmill_running", startedAtUtc, 60, .1, 130, 140, []));
        if (activeRemoteIds.Contains("corrected-3"))
          candidates.Add(new("corrected-3", "treadmill_running", startedAtUtc, 60, .1, 130, 140, []));
      }
      if (activeRemoteIds.Contains("local-1"))
        candidates.Add(new("local-1", "treadmill_running", startedAtUtc, 60, .1, 130, 140, []));
      return Task.FromResult(new GarminAdapterSearchMessage(
        "confirmed", null, null, "searched-token-store", candidates));
    }

    public Task<GarminAdapterMessage> DownloadOriginalAsync(
      string tokenStore,
      string remoteId,
      string outputPath,
      CancellationToken cancellationToken)
    {
      Calls.Add("download");
      byte[] bytes = remoteId switch
      {
        "watch-original" or "restored-original" => CreateOriginalFit(searchedStart),
        "corrected-1" or "corrected-2" or "corrected-3" when mergedFit is not null => mergedFit!,
        "local-1" when localFit is not null => localFit!,
        _ => throw new InvalidOperationException($"No FIT fixture exists for {remoteId}.")
      };
      System.IO.File.WriteAllBytes(outputPath, bytes);
      return Task.FromResult(new GarminAdapterMessage(
        "confirmed", null, null, null, "download-token-store", remoteId));
    }

    public Task<GarminAdapterMessage> UploadAsync(
      string tokenStore,
      string activityPath,
      CancellationToken cancellationToken)
    {
      Calls.Add("upload");
      byte[] uploaded = System.IO.File.ReadAllBytes(activityPath);
      if (uploaded.AsSpan().SequenceEqual(CreateOriginalFit(searchedStart)))
      {
        activeRemoteIds.Add("restored-original");
        return Task.FromResult(new GarminAdapterMessage(
          "confirmed", null, null, null, "updated-token-store", "restored-original"));
      }
      if (mergedFit is null)
      {
        mergedFit = uploaded;
        replacementVisible = true;
        activeRemoteIds.Add("corrected-1");
        activeRemoteIds.Add("corrected-2");
        return Task.FromResult(new GarminAdapterMessage(
          "confirmed", null, null, null, "updated-token-store", null));
      }
      if (uploaded.AsSpan().SequenceEqual(mergedFit))
      {
        replacementVisible = true;
        activeRemoteIds.Add("corrected-3");
        return Task.FromResult(new GarminAdapterMessage(
          "confirmed", null, null, null, "updated-token-store", "corrected-3"));
      }
      localFit = uploaded;
      activeRemoteIds.Add("local-1");
      return Task.FromResult(new GarminAdapterMessage(
        "confirmed", null, null, null, "updated-token-store", localRestoreHasId ? "local-1" : null));
    }

    public Task<GarminAdapterMessage> DeleteAsync(
      string tokenStore,
      string remoteId,
      CancellationToken cancellationToken)
    {
      Calls.Add($"delete:{remoteId}");
      DeletedRemoteIds.Add(remoteId);
      activeRemoteIds.Remove(remoteId);
      return Task.FromResult(new GarminAdapterMessage(
        "confirmed", null, null, null, "deleted-token-store", remoteId));
    }

    private static byte[] CreateOriginalFit(DateTimeOffset startedAtUtc)
    {
      using var stream = new MemoryStream();
      var encoder = new Encode(ProtocolVersion.V20);
      encoder.Open(stream);
      var file = new FileIdMesg();
      file.SetType(Dynastream.Fit.File.Activity);
      file.SetTimeCreated(new Dynastream.Fit.DateTime(startedAtUtc.UtcDateTime));
      encoder.Write(file);
      var record = new RecordMesg();
      record.SetTimestamp(new Dynastream.Fit.DateTime(startedAtUtc.UtcDateTime));
      record.SetHeartRate(130);
      encoder.Write(record);
      var session = new SessionMesg();
      session.SetTimestamp(new Dynastream.Fit.DateTime(startedAtUtc.AddMinutes(1).UtcDateTime));
      session.SetStartTime(new Dynastream.Fit.DateTime(startedAtUtc.UtcDateTime));
      encoder.Write(session);
      encoder.Close();
      return stream.ToArray();
    }
  }

  private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
  {
    public override DateTimeOffset GetUtcNow() => now;
  }
}
