using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionStoreRecoveryRegressionTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(directory, "session-store-regressions.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Display_query_retains_a_session_with_exactly_one_sample()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId, Guid revisionId) =
      await CreateDatabaseAsync();
    var store = new SessionStore(factory);
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    Guid sessionId = await CreateRunningSessionAsync(store, profileId, revisionId, started);
    SessionSample sample = Sample(sessionId, started.AddSeconds(1));
    await store.AppendSampleAsync(sample);

    StoredWorkoutSessionDisplay display = Assert.IsType<StoredWorkoutSessionDisplay>(
      await store.FindDisplayAsync(sessionId));

    Assert.Equal(1, display.TotalSampleCount);
    Assert.Equal(sample, Assert.Single(display.Session.Samples));
  }

  [Fact]
  public async Task Older_recovery_checkpoint_cannot_replace_newer_resume_state()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId, Guid revisionId) =
      await CreateDatabaseAsync();
    var store = new SessionStore(factory);
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    Guid sessionId = await CreateRunningSessionAsync(store, profileId, revisionId, started);
    SessionRecoveryCheckpoint older = Checkpoint(sessionId, started, savedSeconds: 10, sessionVersion: 2);
    SessionRecoveryCheckpoint newer = Checkpoint(sessionId, started, savedSeconds: 20, sessionVersion: 3);
    await store.SaveRecoveryCheckpointAsync(newer);

    await store.SaveRecoveryCheckpointAsync(older);

    RecoverableWorkoutSession recovered = Assert.IsType<RecoverableWorkoutSession>(
      await store.FindRecoverableAsync());
    Assert.Equal(newer, recovered.Checkpoint);
  }

  [Fact]
  public async Task Concurrent_checkpoint_writers_converge_on_the_newest_resume_state()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId, Guid revisionId) =
      await CreateDatabaseAsync();
    var store = new SessionStore(factory);
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    Guid sessionId = await CreateRunningSessionAsync(store, profileId, revisionId, started);
    SessionRecoveryCheckpoint older = Checkpoint(sessionId, started, savedSeconds: 10, sessionVersion: 2);
    SessionRecoveryCheckpoint newer = Checkpoint(sessionId, started, savedSeconds: 20, sessionVersion: 3);

    await Task.WhenAll(
      store.SaveRecoveryCheckpointAsync(newer),
      store.SaveRecoveryCheckpointAsync(older));

    RecoverableWorkoutSession recovered = Assert.IsType<RecoverableWorkoutSession>(
      await store.FindRecoverableAsync());
    Assert.Equal(newer, recovered.Checkpoint);
  }

  [Fact]
  public async Task Equal_timestamp_checkpoint_keeps_the_highest_session_version()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId, Guid revisionId) =
      await CreateDatabaseAsync();
    var store = new SessionStore(factory);
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    Guid sessionId = await CreateRunningSessionAsync(store, profileId, revisionId, started);
    SessionRecoveryCheckpoint lower = Checkpoint(sessionId, started, savedSeconds: 20, sessionVersion: 2);
    SessionRecoveryCheckpoint higher = Checkpoint(sessionId, started, savedSeconds: 20, sessionVersion: 3);
    await store.SaveRecoveryCheckpointAsync(lower);
    await store.SaveRecoveryCheckpointAsync(higher);

    await store.SaveRecoveryCheckpointAsync(lower);

    RecoverableWorkoutSession recovered = Assert.IsType<RecoverableWorkoutSession>(
      await store.FindRecoverableAsync());
    Assert.Equal(higher, recovered.Checkpoint);
  }

  private static async Task<Guid> CreateRunningSessionAsync(
    SessionStore store,
    Guid profileId,
    Guid revisionId,
    DateTimeOffset started)
  {
    Guid sessionId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(
      sessionId,
      profileId,
      "Runner",
      revisionId,
      "Recovery run",
      started.AddSeconds(-2),
      "{}",
      SessionMetricAlgorithms.EstimatedCaloriesV1,
      origin: SessionOrigin.Hardware));
    await store.MarkRunningAsync(sessionId, started);
    return sessionId;
  }

  private async Task<(IDbContextFactory<TreadmillRunnerDbContext> Factory, Guid ProfileId, Guid RevisionId)>
    CreateDatabaseAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory =
      TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T07:00:00Z");
    var profile = new UserProfileEntity
    {
      Id = Guid.NewGuid(),
      DisplayName = "Runner",
      NormalizedDisplayName = "RUNNER",
      UnitSystem = "Metric",
      WeightKilograms = 72,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    };
    var workout = new WorkoutEntity
    {
      Id = Guid.NewGuid(),
      Name = "Easy Run",
      CreatedAtUtc = now,
    };
    var revision = new WorkoutRevisionEntity
    {
      Id = Guid.NewGuid(),
      RevisionNumber = 1,
      DefinitionJson = "{\"blocks\":[],\"schemaVersion\":1,\"title\":\"Easy Run\"}",
      ContentSha256 = new string('e', 64),
      CreatedAtUtc = now,
    };
    workout.Revisions.Add(revision);
    context.AddRange(profile, workout);
    await context.SaveChangesAsync();
    return (factory, profile.Id, revision.Id);
  }

  private static SessionSample Sample(Guid sessionId, DateTimeOffset capturedAt) => new(
    sessionId,
    0,
    capturedAt,
    TimeSpan.FromSeconds(1),
    plannedSpeedKph: 6.5,
    requestedSpeedKph: 6.5,
    measuredSpeedKph: 6.4,
    plannedInclinePercent: 1,
    requestedInclinePercent: 1,
    measuredInclinePercent: 1,
    heartRateBpm: 120,
    distanceKilometers: 6.4 / 3600,
    estimatedKilocalories: 0.12,
    telemetryAge: TimeSpan.FromMilliseconds(40),
    SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static SessionRecoveryCheckpoint Checkpoint(
    Guid sessionId,
    DateTimeOffset started,
    int savedSeconds,
    long sessionVersion) => new(
      sessionId,
      started.AddSeconds(savedSeconds),
      SessionState.Running,
      sessionVersion,
      started,
      new WorkoutProgressionCheckpoint(
        1,
        TimeSpan.FromSeconds(savedSeconds),
        0.1,
        TimeSpan.FromSeconds(savedSeconds),
        0.1),
      0.1,
      6.5,
      1,
      6.5,
      1,
      HeartRateAutomationMode.Disabled,
      savedSeconds);
}
