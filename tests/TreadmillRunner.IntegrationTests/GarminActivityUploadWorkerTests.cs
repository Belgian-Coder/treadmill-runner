using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
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
      TimeProvider.System, NullLogger<GarminActivityUploadWorker>.Instance);
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

  private sealed class OutcomeAdapter(AdapterMode mode) : IGarminActivityAdapter
  {
    public bool SawReadableFit { get; private set; }
    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken)
    {
      SawReadableFit = File.Exists(activityPath) && new FileInfo(activityPath).Length > 16;
      return mode switch
      {
        AdapterMode.Confirmed => Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "updated-token-store", "12345")),
        AdapterMode.Duplicate => Task.FromResult(new GarminAdapterMessage("failed", "duplicate", "Garmin reports that this activity already exists.", null, null, null)),
        AdapterMode.Ambiguous => Task.FromException<GarminAdapterMessage>(new GarminAdapterAmbiguousResultException("missing response")),
        AdapterMode.Timeout => Task.FromException<GarminAdapterMessage>(new TimeoutException("adapter timed out")),
        AdapterMode.Unavailable => Task.FromException<GarminAdapterMessage>(new GarminAdapterUnavailableException("python missing")),
        _ => throw new ArgumentOutOfRangeException(),
      };
    }
  }
}
