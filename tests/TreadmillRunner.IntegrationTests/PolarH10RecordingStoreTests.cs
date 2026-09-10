using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Polar;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PolarH10RecordingStoreTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string DatabasePath => Path.Combine(directory, "polar-h10.db");

  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Fact]
  public async Task Enqueue_is_idempotent_and_lease_has_one_winner()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var first = new PolarH10RecordingStore(factory);
    var second = new PolarH10RecordingStore(factory);
    DateTimeOffset now = seed.Start;
    PolarH10RecordingJob job = await first.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, now);
    Assert.Equal(job.Id, (await second.EnqueueAsync(seed.SessionId, seed.ProfileId, job.ExerciseId, seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, now)).Id);

    PolarH10RecordingJob?[] leases = await Task.WhenAll(
      first.LeaseNextAsync(now, TimeSpan.FromMinutes(2)),
      second.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
    Assert.Single(leases, lease => lease is not null);
  }

  [Fact]
  public async Task Verified_payload_fills_only_null_hr_and_recalculates_aggregates()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await store.MarkRecordingAsync(job.Id, seed.Start);
    var recorded = new PolarH10MemoryRecord(job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", seed.Start, seed.Start.AddSeconds(4),
      PolarH10SampleType.HeartRate, 1, new byte[] { 1, 2, 3, 4 },
      new ushort[] { 100, 101, 102, 103 }.Select((value, index) => new PolarH10HeartRateSample(seed.Start.AddSeconds(index), value)).ToArray(), []);
    await store.StoreDownloadedAsync(job.Id, recorded, seed.Start.AddMinutes(21));

    Assert.Equal(PolarH10RecordingOutcome.Merged, await store.MergeDownloadedAsync(job.Id, seed.Start.AddMinutes(21).AddSeconds(1)));
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    SessionSampleEntity[] samples = await context.SessionSamples.OrderBy(row => row.Sequence).ToArrayAsync();
    Assert.Equal(new ushort?[] { 100, 101, 102, 103 }, samples.Select(row => row.HeartRateBpm));
    WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
    Assert.Equal(101.5, session.AverageHeartRateBpm);
    Assert.Equal((ushort)103, session.MaximumHeartRateBpm);
    Assert.Single(context.SessionEvents, row => row.Kind == "session-warning" && row.DetailsJson.Contains("polar-h10-memory-merged"));
    PolarH10RecordingEntity stored = await context.PolarH10Recordings.SingleAsync();
    Assert.Equal(1, stored.MergeCount);
    Assert.Equal(64, stored.PayloadSha256!.Length);
    Assert.Equal(recorded.Payload.Length, stored.PayloadBytes);
    Assert.Empty(await store.ListLocalAsync());
    var garmin = new GarminActivityUploadStore(factory);
    await garmin.ConnectAsync(seed.ProfileId, "runner", "protected", true, seed.Start.AddHours(-1));
    Assert.True(await garmin.ReconcileCompletedSessionsAsync(seed.Start.AddMinutes(22)) > 0);
  }

  [Fact]
  public async Task Pending_h10_recovery_blocks_Garmin_reconcile_until_explicit_skip()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var polar = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await polar.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    var garmin = new GarminActivityUploadStore(factory);
    await garmin.ConnectAsync(seed.ProfileId, "runner", "protected", true, seed.Start.AddHours(-1));

    Assert.Equal(0, await garmin.ReconcileCompletedSessionsAsync(seed.Start.AddMinutes(30)));
    await polar.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.Skipped, "operator skip", seed.Start.AddMinutes(31));
    Assert.True(await garmin.ReconcileCompletedSessionsAsync(seed.Start.AddMinutes(31)) > 0);
  }

  [Fact]
  public async Task Terminal_lifecycle_requests_win_over_inflight_start_and_download_results()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);

    await store.QueueStopAsync(seed.SessionId, seed.Start.AddMinutes(20));
    await store.MarkRecordingAsync(job.Id, seed.Start.AddSeconds(1));
    Assert.Equal(PolarH10RecordingOutcome.StopPending, (await store.FindByIdAsync(job.Id))!.Outcome);

    await store.QueueDiscardCleanupAsync(seed.SessionId, seed.Start.AddMinutes(21));
    var downloaded = new PolarH10MemoryRecord(job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", seed.Start, seed.Start.AddSeconds(1),
      PolarH10SampleType.HeartRate, 1, new byte[] { 100 }, [new(seed.Start, 100)], []);
    await store.StoreDownloadedAsync(job.Id, downloaded, seed.Start.AddMinutes(21));

    Assert.Equal(PolarH10RecordingOutcome.DiscardCleanupPending, (await store.FindByIdAsync(job.Id))!.Outcome);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    Assert.Null((await context.PolarH10Recordings.SingleAsync()).Payload);
  }

  [Fact]
  public async Task Completed_discard_cleanup_removes_any_detached_payload_and_samples()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    var downloaded = new PolarH10MemoryRecord(job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", seed.Start, seed.Start.AddSeconds(1),
      PolarH10SampleType.HeartRate, 1, new byte[] { 100 }, [new(seed.Start, 100)], []);
    await store.StoreDownloadedAsync(job.Id, downloaded, seed.Start.AddMinutes(20));
    await store.QueueDiscardCleanupAsync(seed.SessionId, seed.Start.AddMinutes(21));

    await store.CompleteDiscardCleanupAsync(job.Id);

    Assert.Null(await store.FindByIdAsync(job.Id));
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    Assert.Empty(context.PolarH10RecordingSamples);
  }

  [Fact]
  public async Task Worker_restarts_from_durable_states_and_removes_only_the_exact_merged_recording()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start);
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var wake = new FakeWakeSignal();
    var clock = new FixedTimeProvider(seed.Start);
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      clock, wake, new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      await context.SaveChangesAsync();
    }
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.Recording, (await store.FindByIdAsync(job.Id))!.Outcome);
    clock.UtcNow = seed.Start.AddMinutes(20);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Completed";
      await context.SaveChangesAsync();
    }
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.Merged, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.True(client.RemoteExists);
    client.DeleteFailuresRemaining = 1;
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.RemovalPending, (await store.FindByIdAsync(job.Id))!.Outcome);
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.Completed, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.False(client.RemoteExists);
    Assert.Equal(2, client.DeleteCalls);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
      Assert.Single(context.SessionEvents, row => row.Kind == "session-warning" && row.DetailsJson.Contains("polar-h10-memory-merged"));
    Assert.True(wake.Count > 0);
  }

  [Fact]
  public async Task Disabled_feature_does_not_start_a_queued_recording()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start);
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = false, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    Assert.Equal(PolarH10RecordingOutcome.NotStarted, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.False(client.RemoteExists);
  }

  [Fact]
  public async Task Manual_existing_recording_download_is_retained_without_start_or_delete()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(null, seed.ProfileId, "manual-existing", seed.EnrollmentId,
      "Manual", PolarH10SampleType.HeartRate, 1, seed.Start);
    await store.QueueStopByIdAsync(job.Id, seed.Start);
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start, remoteExists: true);
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    Assert.Equal(PolarH10RecordingOutcome.Retained, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.True(client.RemoteExists);
    Assert.Equal(0, client.StartCalls);
    Assert.Equal(0, client.DeleteCalls);
    Assert.Single(await store.ListLocalAsync());
  }

  private async Task<(IDbContextFactory<TreadmillRunnerDbContext>, Seed)> CreateDatabaseAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    DateTimeOffset start = DateTimeOffset.Parse("2026-09-10T07:00:00Z");
    Guid profileId = Guid.NewGuid(), workoutId = Guid.NewGuid(), revisionId = Guid.NewGuid(), sessionId = Guid.NewGuid(), enrollmentId = Guid.NewGuid();
    context.UserProfiles.Add(new UserProfileEntity { Id = profileId, DisplayName = "Runner", NormalizedDisplayName = "RUNNER", UnitSystem = "Metric", WeightKilograms = 70, Version = 1, CreatedAtUtc = start, UpdatedAtUtc = start });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = "Easy run", Kind = "Structured", CreatedAtUtc = start });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity { Id = revisionId, WorkoutId = workoutId, RevisionNumber = 1, DefinitionJson = "{}", ContentSha256 = new string('a', 64), CreatedAtUtc = start });
    context.DeviceEnrollments.Add(new DeviceEnrollmentEntity
    {
      Id = enrollmentId,
      Role = "HeartRate",
      DeviceId = "001122334455",
      ProtocolId = "heart-rate",
      IdentityFingerprint = new string('b', 64),
      DisplayName = "Polar H10",
      Evidence = "UserConfirmed",
      HeartRateDeviceKind = "ChestStrap",
      HeartRateDeviceFamily = "Polar",
      Version = 1,
      CreatedAtUtc = start,
      UpdatedAtUtc = start,
    });
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = sessionId,
      UserProfileId = profileId,
      UserProfileName = "Runner",
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Easy run",
      State = "Completed",
      ArmedAtUtc = start.AddSeconds(-2),
      StartedAtUtc = start,
      EndedAtUtc = start.AddMinutes(20),
      DurationSeconds = 1200,
      DistanceKilometers = 2,
      MetricAlgorithmVersion = "v1",
      ControllerConfigurationJson = "{}",
      RecordPolarH10Memory = true,
    });
    ushort?[] values = [100, 101, null, 103];
    for (int second = 0; second < values.Length; second++)
      context.SessionSamples.Add(new SessionSampleEntity
      {
        WorkoutSessionId = sessionId,
        Sequence = second,
        CapturedAtUtc = start.AddSeconds(second),
        ElapsedMilliseconds = second * 1000,
        RequestedSpeedKph = 7,
        MeasuredSpeedKph = 7,
        RequestedInclinePercent = 1,
        MeasuredInclinePercent = 1,
        HeartRateBpm = values[second],
        MetricAlgorithmVersion = "v1",
      });
    await context.SaveChangesAsync();
    return (factory, new(profileId, sessionId, enrollmentId, start));
  }

  private sealed record Seed(Guid ProfileId, Guid SessionId, Guid EnrollmentId, DateTimeOffset Start);

  private sealed class FakeMemoryClient(Guid enrollmentId, string exerciseId, DateTimeOffset startedAt, bool remoteExists = false) : IPolarH10MemoryClient
  {
    private bool recording;
    public bool RemoteExists { get; private set; } = remoteExists;
    public int StartCalls { get; private set; }
    public int DeleteCalls { get; private set; }
    public int DeleteFailuresRemaining { get; set; }

    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(Guid? requestedEnrollmentId, CancellationToken cancellationToken = default) =>
      Task.FromResult(new PolarH10DeviceRecordingStatus(enrollmentId, "001122334455", "Polar H10", recording, recording ? exerciseId : null));
    public Task StartAsync(Guid requestedEnrollmentId, string requestedExerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default)
    { Assert.Equal(enrollmentId, requestedEnrollmentId); Assert.Equal(exerciseId, requestedExerciseId); StartCalls++; recording = true; RemoteExists = true; return Task.CompletedTask; }
    public Task StopAsync(Guid requestedEnrollmentId, CancellationToken cancellationToken = default) { recording = false; return Task.CompletedTask; }
    public Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(Guid requestedEnrollmentId, CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<PolarH10RemoteRecording>>(RemoteExists ? [new($"/{exerciseId}/SAMPLES.BPB", 4)] : []);
    public Task<PolarH10MemoryRecord> FetchAsync(Guid requestedEnrollmentId, string remotePath, DateTimeOffset requestedStart, CancellationToken cancellationToken = default) =>
      Task.FromResult(new PolarH10MemoryRecord(exerciseId, remotePath, startedAt, startedAt.AddSeconds(4), PolarH10SampleType.HeartRate, 1,
        new byte[] { 1, 2, 3, 4 }, new ushort[] { 100, 101, 102, 103 }.Select((value, index) => new PolarH10HeartRateSample(startedAt.AddSeconds(index), value)).ToArray(), []));
    public Task DeleteAsync(Guid requestedEnrollmentId, string remotePath, CancellationToken cancellationToken = default)
    {
      Assert.Equal($"/{exerciseId}/SAMPLES.BPB", remotePath);
      DeleteCalls++;
      if (DeleteFailuresRemaining-- > 0) throw new IOException("synthetic removal failure");
      RemoteExists = false;
      return Task.CompletedTask;
    }
  }

  private sealed class FakeWakeSignal : IGarminActivityUploadWakeSignal
  {
    public int Count { get; private set; }
    public void Wake() => Count++;
  }

  private sealed class FixedOptionsMonitor<T>(T value) : IOptionsMonitor<T>
  {
    public T CurrentValue => value;
    public T Get(string? name) => value;
    public IDisposable? OnChange(Action<T, string?> listener) => null;
  }

  private sealed class FixedTimeProvider(DateTimeOffset utcNow) : TimeProvider
  {
    public DateTimeOffset UtcNow { get; set; } = utcNow;
    public override DateTimeOffset GetUtcNow() => UtcNow;
  }
}
