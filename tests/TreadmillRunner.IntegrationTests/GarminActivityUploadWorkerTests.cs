using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
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
  [InlineData(AdapterMode.Duplicate, "Failed", false, false, "Connected")]
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
      services.GetRequiredService<IServiceScopeFactory>(), store, adapter, connections,
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
  [InlineData(GarminWatchActivityHandling.MergeAndReplace, "Unknown", false)]
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
    var worker = new GarminActivityUploadWorker(services.GetRequiredService<IServiceScopeFactory>(), store, adapter, connections, clock, new ApplicationMaintenanceState(), NullLogger<GarminActivityUploadWorker>.Instance);

    await worker.ProcessOneAsync(leased, default);
    if (handling == GarminWatchActivityHandling.MergeAndReplace && replacementHasId)
    {
      GarminActivityUploadJob deleteLease = Assert.IsType<GarminActivityUploadJob>(await store.LeaseNextAsync(now.AddSeconds(1), TimeSpan.FromMinutes(2)));
      Assert.Equal("DeleteOriginal", deleteLease.OperationPhase);
      await worker.ProcessOneAsync(deleteLease, default);
      Assert.Equal(new[] { "search", "download", "upload", "delete" }, adapter.Calls);
    }

    GarminActivityUploadJob completed = Assert.Single(await store.ListJobsAsync(profileId));
    Assert.Equal(expectedStatus, completed.Status);
    Assert.Equal("watch-123", completed.MatchedRemoteId);
    if (handling == GarminWatchActivityHandling.PreferWatch)
      Assert.Equal(new[] { "search" }, adapter.Calls);
    else if (replacementHasId)
      Assert.Equal("replacement-456", completed.RemoteId);
    else
      Assert.Equal(new[] { "search", "download", "upload" }, adapter.Calls);
  }

  private static async Task SeedCompletedSessionAsync(IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId, DateTimeOffset started)
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
      AverageHeartRateBpm = 130,
      MaximumHeartRateBpm = 140,
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
      HeartRateBpm = 130,
      DistanceKilometers = .0017,
      EstimatedCalories = .13,
      TelemetryAgeMilliseconds = 10,
      MetricAlgorithmVersion = "v1",
    });
    context.WorkoutSessions.Add(session);
    await context.SaveChangesAsync();
  }

  public enum AdapterMode { Confirmed, Duplicate, Ambiguous, Timeout, Unavailable }

  private sealed class OutcomeAdapter(AdapterMode mode, bool returnMatch = false, bool replacementHasId = true) : IGarminActivityAdapter
  {
    public bool SawReadableFit { get; private set; }
    public List<string> Calls { get; } = [];
    private DateTimeOffset searchedStart;
    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<GarminAdapterSearchMessage> SearchWatchActivitiesAsync(string tokenStore, DateTimeOffset startedAtUtc, CancellationToken cancellationToken)
    {
      Calls.Add("search");
      searchedStart = startedAtUtc;
      IReadOnlyList<GarminWatchActivityCandidate> candidates = returnMatch
        ? [new("watch-123", "treadmill_running", startedAtUtc.AddSeconds(15), 60, .1, 130, 140, [])]
        : [];
      return Task.FromResult(new GarminAdapterSearchMessage("confirmed", null, null, "searched-token-store", candidates));
    }
    public Task<GarminAdapterMessage> DownloadOriginalAsync(string tokenStore, string remoteId, string outputPath, CancellationToken cancellationToken)
    {
      Calls.Add("download");
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
      return Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "deleted-token-store", remoteId));
    }
  }

  private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
  {
    public override DateTimeOffset GetUtcNow() => now;
  }
}
