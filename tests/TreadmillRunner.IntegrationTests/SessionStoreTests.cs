using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionStoreTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(_directory, "sessions.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory))
    {
      Directory.Delete(_directory, recursive: true);
    }

    return Task.CompletedTask;
  }

  [Fact]
  public async Task Session_store_round_trips_samples_events_summary_and_debrief()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var armedAt = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var sessionId = Guid.NewGuid();

    await store.CreateAsync(new NewWorkoutSession(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      armedAt,
      "{\"heartRateController\":\"disabled\"}",
      SessionMetricAlgorithms.EstimatedCaloriesV1));
    await store.MarkRunningAsync(sessionId, armedAt.AddSeconds(3));
    await store.AppendSampleAsync(Sample(sessionId, 0, armedAt.AddSeconds(3), 0, 6.5, 6.4, 118));
    await store.AppendSampleAsync(Sample(sessionId, 1, armedAt.AddSeconds(4), 1, 6.5, 6.6, 120));
    await store.AppendEventAsync(sessionId, new SessionWarningEvent(
      "simulator-note",
      "Runner changed pace from the console.",
      armedAt.AddSeconds(4)));
    await store.FinalizeAsync(new SessionSummary(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      armedAt.AddSeconds(3),
      armedAt.AddMinutes(20),
      TimeSpan.FromMinutes(19).Add(TimeSpan.FromSeconds(57)),
      2.2,
      143,
      129,
      151,
      6.6,
      1.0));
    await store.SaveDebriefAsync(new SessionDebrief(
      sessionId,
      5,
      " Comfortable finish. ",
      armedAt.AddMinutes(21)));

    StoredWorkoutSession stored = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(sessionId));
    Assert.Equal(SessionState.Completed, stored.State);
    Assert.Equal(2, stored.Samples.Count);
    Assert.Equal(1, stored.Samples[1].Sequence);
    Assert.Equal(6.6, stored.Samples[1].MeasuredSpeedKph);
    Assert.IsType<SessionWarningEvent>(Assert.Single(stored.Events));
    Assert.Equal(5, stored.Debrief?.PerceivedExertion);
    Assert.Equal("Comfortable finish.", stored.Debrief?.Note);

    SessionSummary summary = Assert.Single(await store.ListSummariesAsync(ids.ProfileId));
    Assert.Equal(sessionId, summary.SessionId);
    Assert.Equal("Runner", summary.UserProfileName);
    Assert.Equal("Easy Run", summary.WorkoutTitle);
    Assert.Equal(2.2, summary.DistanceKilometers);
  }

  [Fact]
  public async Task Startup_interruption_marks_only_unfinished_sessions_and_records_event()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var activeId = Guid.NewGuid();
    var completedId = Guid.NewGuid();

    await store.CreateAsync(New(activeId, ids, now));
    await store.MarkRunningAsync(activeId, now.AddSeconds(3));
    await store.CreateAsync(New(completedId, ids, now.AddMinutes(-30)));
    await store.MarkRunningAsync(completedId, now.AddMinutes(-30).AddSeconds(3));
    await store.FinalizeAsync(new SessionSummary(
      completedId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      now.AddMinutes(-30).AddSeconds(3),
      now.AddMinutes(-10),
      TimeSpan.FromMinutes(19),
      2.1,
      135,
      126,
      146,
      6.5,
      1));

    int interrupted = await store.InterruptUnfinishedAsync(now, "Gateway restarted.");

    Assert.Equal(1, interrupted);
    StoredWorkoutSession active = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(activeId));
    Assert.Equal(SessionState.Interrupted, active.State);
    Assert.Equal(now, active.EndedAt);
    SessionInterruptedEvent interruption = Assert.IsType<SessionInterruptedEvent>(Assert.Single(active.Events));
    Assert.Equal("Gateway restarted.", interruption.Reason);
    Assert.Equal(SessionState.Completed, (await store.FindAsync(completedId))?.State);
  }

  [Fact]
  [Trait("Category", "Soak")]
  public async Task Four_hour_one_hertz_session_persists_and_reads_all_samples()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var armedAt = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var startedAt = armedAt.AddSeconds(3);
    var sessionId = Guid.NewGuid();

    await store.CreateAsync(New(sessionId, ids, armedAt));
    await store.MarkRunningAsync(sessionId, startedAt);
    for (var sequence = 0; sequence < 14_400; sequence++)
    {
      await store.AppendSampleAsync(Sample(
        sessionId,
        sequence,
        startedAt.AddSeconds(sequence),
        sequence,
        requestedSpeed: 8,
        measuredSpeed: 8,
        heartRate: 135));
    }

    await store.FinalizeAsync(new SessionSummary(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      startedAt,
      startedAt.AddHours(4),
      TimeSpan.FromHours(4),
      32,
      1_728,
      135,
      135,
      8,
      1));

    StoredWorkoutSession stored = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(sessionId));
    Assert.Equal(14_400, stored.Samples.Count);
    Assert.Equal(0, stored.Samples[0].Sequence);
    Assert.Equal(14_399, stored.Samples[^1].Sequence);
    Assert.Equal(TimeSpan.FromSeconds(14_399), stored.Samples[^1].Elapsed);
  }

  private static NewWorkoutSession New(Guid sessionId, SeedIds ids, DateTimeOffset armedAt) => new(
    sessionId,
    ids.ProfileId,
    "Runner",
    ids.RevisionId,
    "Easy Run",
    armedAt,
    "{}",
    SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static SessionSample Sample(
    Guid sessionId,
    long sequence,
    DateTimeOffset capturedAt,
    double elapsedSeconds,
    double requestedSpeed,
    double measuredSpeed,
    ushort heartRate) => new(
      sessionId,
      sequence,
      capturedAt,
      TimeSpan.FromSeconds(elapsedSeconds),
      plannedSpeedKph: 6.5,
      requestedSpeed,
      measuredSpeed,
      plannedInclinePercent: 1,
      requestedInclinePercent: 1,
      measuredInclinePercent: 1,
      heartRate,
      distanceKilometers: measuredSpeed * elapsedSeconds / 3600,
      estimatedKilocalories: elapsedSeconds * 0.12,
      telemetryAge: TimeSpan.FromMilliseconds(40),
      SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static async Task MigrateAndSeedAsync(IDbContextFactory<TreadmillRunnerDbContext> factory)
  {
    await using var context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
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
    workout.Revisions.Add(new WorkoutRevisionEntity
    {
      Id = Guid.NewGuid(),
      RevisionNumber = 1,
      DefinitionJson = "{\"blocks\":[],\"schemaVersion\":1,\"title\":\"Easy Run\"}",
      ContentSha256 = new string('e', 64),
      CreatedAtUtc = now,
    });
    context.AddRange(profile, workout);
    await context.SaveChangesAsync();
  }

  private static async Task<SeedIds> ReadSeedIdsAsync(IDbContextFactory<TreadmillRunnerDbContext> factory)
  {
    await using var context = await factory.CreateDbContextAsync();
    return new SeedIds(
      await context.UserProfiles.Select(profile => profile.Id).SingleAsync(),
      await context.WorkoutRevisions.Select(revision => revision.Id).SingleAsync());
  }

  private sealed record SeedIds(Guid ProfileId, Guid RevisionId);
}
