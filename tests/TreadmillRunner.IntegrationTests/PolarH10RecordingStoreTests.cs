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
  public async Task Active_manual_lookup_uses_SQLite_compatible_timestamp_ordering()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob older = await store.EnqueueAsync(null, null, "manual-older", seed.EnrollmentId,
      "Manual", PolarH10SampleType.HeartRate, 1, seed.Start);
    PolarH10RecordingJob newer = await store.EnqueueAsync(null, null, "manual-newer", seed.EnrollmentId,
      "Manual", PolarH10SampleType.HeartRate, 1, seed.Start.AddSeconds(1));

    Assert.Equal(newer.Id, (await store.FindActiveManualAsync())?.Id);

    await store.MarkOutcomeAsync(newer.Id, PolarH10RecordingOutcome.NotStarted, null, seed.Start.AddSeconds(2));
    Assert.Equal(older.Id, (await store.FindActiveManualAsync())?.Id);
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
  public async Task Verified_payload_fills_a_disconnect_gap_despite_live_sampling_phase_drift()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      SessionSampleEntity[] samples = await context.SessionSamples.OrderBy(row => row.Sequence).ToArrayAsync();
      double[] offsets = [1.5, 2.5, 3.968, 4.5];
      for (var index = 0; index < samples.Length; index++)
        samples[index].CapturedAtUtc = seed.Start.AddSeconds(offsets[index]);
      await context.SaveChangesAsync();
    }
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(
      seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await store.MarkRecordingAsync(job.Id, seed.Start);
    var recorded = new PolarH10MemoryRecord(
      job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", seed.Start, seed.Start.AddSeconds(4),
      PolarH10SampleType.HeartRate, 1, new byte[] { 1, 2, 3, 4 },
      new ushort[] { 100, 101, 102, 103 }
        .Select((value, index) => new PolarH10HeartRateSample(seed.Start.AddSeconds(index), value)).ToArray(), []);
    await store.StoreDownloadedAsync(job.Id, recorded, seed.Start.AddMinutes(21));

    Assert.Equal(PolarH10RecordingOutcome.Merged, await store.MergeDownloadedAsync(job.Id, seed.Start.AddMinutes(21).AddSeconds(1)));

    await using TreadmillRunnerDbContext verification = await factory.CreateDbContextAsync();
    SessionSampleEntity[] merged = await verification.SessionSamples.OrderBy(row => row.Sequence).ToArrayAsync();
    Assert.Equal(new ushort?[] { 100, 101, 102, 103 }, merged.Select(row => row.HeartRateBpm));
    Assert.Equal(1, (await verification.PolarH10Recordings.SingleAsync()).MergeCount);
  }

  [Fact]
  public async Task Prepare_time_recording_ignores_pre_run_samples_and_merges_the_exact_workout_window()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    DateTimeOffset preparedAt = seed.Start.AddMinutes(-2);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      (await context.WorkoutSessions.SingleAsync()).ArmedAtUtc = preparedAt;
      await context.SaveChangesAsync();
    }
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, preparedAt);
    await store.MarkRecordingAsync(job.Id, preparedAt.AddSeconds(1));
    PolarH10HeartRateSample[] samples = Enumerable.Range(0, 124)
      .Select(index => new PolarH10HeartRateSample(
        preparedAt.AddSeconds(index),
        index >= 120 ? (ushort)(100 + index - 120) : (ushort)80))
      .ToArray();
    var recording = new PolarH10MemoryRecord(job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", preparedAt, preparedAt.AddSeconds(124),
      PolarH10SampleType.HeartRate, 1, Enumerable.Repeat((byte)1, 124).ToArray(), samples, []);
    await store.StoreDownloadedAsync(job.Id, recording, seed.Start.AddMinutes(21));

    Assert.Equal(PolarH10RecordingOutcome.Merged, await store.MergeDownloadedAsync(job.Id, seed.Start.AddMinutes(21).AddSeconds(1)));
    await using TreadmillRunnerDbContext verification = await factory.CreateDbContextAsync();
    SessionSampleEntity[] merged = await verification.SessionSamples.OrderBy(row => row.Sequence).ToArrayAsync();
    Assert.Equal(new ushort?[] { 100, 101, 102, 103 }, merged.Select(row => row.HeartRateBpm));
    Assert.DoesNotContain(merged, row => row.HeartRateBpm == 80);
  }

  [Fact]
  public async Task Legacy_running_time_start_still_merges_when_the_session_was_armed_earlier()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      (await context.WorkoutSessions.SingleAsync()).ArmedAtUtc = seed.Start.AddMinutes(-3);
      await context.SaveChangesAsync();
    }
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(
      seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await store.MarkRecordingAsync(job.Id, seed.Start);
    var recording = new PolarH10MemoryRecord(
      job.ExerciseId, $"/{job.ExerciseId}/SAMPLES.BPB", seed.Start, seed.Start.AddSeconds(4),
      PolarH10SampleType.HeartRate, 1, new byte[] { 1, 2, 3, 4 },
      new ushort[] { 100, 101, 102, 103 }
        .Select((value, index) => new PolarH10HeartRateSample(seed.Start.AddSeconds(index), value)).ToArray(),
      []);
    await store.StoreDownloadedAsync(job.Id, recording, seed.Start.AddMinutes(21));

    Assert.Equal(
      PolarH10RecordingOutcome.Merged,
      await store.MergeDownloadedAsync(job.Id, seed.Start.AddMinutes(21).AddSeconds(1)));
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
    var access = new TrackingMemoryAccessCoordinator();
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(access);
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
    client.DeleteFailuresRemaining = 1;
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.Merged, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.True(client.RemoteExists);
    await worker.DrainOneAsync(default);
    Assert.Equal(PolarH10RecordingOutcome.Completed, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.False(client.RemoteExists);
    Assert.Equal(2, client.DeleteCalls);
    Assert.Equal(3, access.AcquireCalls);
    Assert.Equal(3, access.ReleaseCalls);
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
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new TrackingMemoryAccessCoordinator());
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = false, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    Assert.Equal(PolarH10RecordingOutcome.NotStarted, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.False(client.RemoteExists);
  }

  [Fact]
  public async Task Manual_existing_recording_download_is_retained_after_verified_remote_removal()
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
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new TrackingMemoryAccessCoordinator());
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    Assert.Equal(PolarH10RecordingOutcome.Retained, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.False(client.RemoteExists);
    Assert.Equal(0, client.StartCalls);
    Assert.Equal(1, client.DeleteCalls);
    Assert.Single(await store.ListLocalAsync());
  }

  [Fact]
  public async Task Explicit_stop_rearms_an_exhausted_recording_job()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(null, seed.ProfileId, "manual-exhausted", seed.EnrollmentId,
      "Manual", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      PolarH10RecordingEntity row = await context.PolarH10Recordings.SingleAsync(candidate => candidate.Id == job.Id);
      row.Status = PolarH10RecordingOutcome.ReviewRequired.ToString();
      row.AttemptCount = 5;
      row.LastError = "Previous failure";
      await context.SaveChangesAsync();
    }

    await store.QueueStopByIdAsync(job.Id, seed.Start.AddMinutes(1));

    PolarH10RecordingJob updated = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.StopPending, updated.Outcome);
    Assert.Equal(0, updated.AttemptCount);
    Assert.Null(updated.LastError);
  }

  [Fact]
  public async Task Stale_fifth_attempt_cannot_overwrite_a_newly_rearmed_stop()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(null, seed.ProfileId, "manual-race", seed.EnrollmentId,
           "Manual", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      PolarH10RecordingEntity row = await context.PolarH10Recordings.SingleAsync(candidate => candidate.Id == job.Id);
      row.Status = PolarH10RecordingOutcome.Retryable.ToString();
      row.AttemptCount = 4;
      row.AvailableAtUtc = seed.Start;
      await context.SaveChangesAsync();
    }

    PolarH10RecordingJob leased = await store.LeaseNextAsync(seed.Start, TimeSpan.FromSeconds(30))
      ?? throw new InvalidOperationException("Expected the fifth attempt lease.");
    Assert.Equal(5, leased.AttemptCount);
    await store.QueueStopByIdAsync(job.Id, seed.Start.AddSeconds(1));

    bool staleWrite = await store.MarkOutcomeIfVersionAsync(
      leased.Id, leased.Version, leased.AttemptCount, PolarH10RecordingOutcome.ReviewRequired,
      "stale worker failure", seed.Start.AddSeconds(2));
    Assert.False(staleWrite);
    PolarH10RecordingJob rearmed = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.StopPending, rearmed.Outcome);
    Assert.Equal(0, rearmed.AttemptCount);
    Assert.Null(rearmed.LastError);

    PolarH10RecordingJob next = await store.LeaseNextAsync(seed.Start.AddSeconds(2), TimeSpan.FromSeconds(30))
      ?? throw new InvalidOperationException("The rearmed stop must remain leaseable.");
    Assert.Equal(PolarH10RecordingOutcome.StopPending, next.Outcome);
    Assert.Equal(1, next.AttemptCount);
  }

  [Fact]
  [Trait("Category", "ReleaseSmoke")]
  public async Task Stale_success_transitions_cannot_overwrite_a_concurrent_discard()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, "automatic-discard-race", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    PolarH10RecordingJob leased = await store.LeaseNextAsync(seed.Start, TimeSpan.FromSeconds(30))
      ?? throw new InvalidOperationException("Expected an automatic H10 worker lease.");

    Assert.True(await store.QueueDiscardCleanupAsync(seed.SessionId, seed.Start.AddSeconds(1)));

    Assert.False(await store.QueueStopByIdIfVersionAsync(
      leased.Id, leased.Version, seed.Start.AddSeconds(2)));
    Assert.False(await store.MarkRemoteRemovedIfVersionAsync(
      leased.Id, leased.Version, seed.Start.AddSeconds(3)));

    PolarH10RecordingJob discarded = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.DiscardCleanupPending, discarded.Outcome);
    Assert.Equal(0, discarded.RemovalCount);
  }

  [Fact]
  public async Task Unconfirmed_automatic_start_is_bounded_once_and_reconciled_after_the_workout()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      await context.SaveChangesAsync();
    }
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start) { BlockStartUntilCanceled = true };
    var access = new TrackingMemoryAccessCoordinator();
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(access);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var clock = new FixedTimeProvider(seed.Start);
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      clock, new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance)
    {
      AutomaticStartTimeout = TimeSpan.FromMilliseconds(50),
    };

    await worker.DrainOneAsync(default).WaitAsync(TimeSpan.FromSeconds(2));

    PolarH10RecordingJob deferred = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.ReviewRequired, deferred.Outcome);
    Assert.Contains("deferred until the workout ends", deferred.LastError, StringComparison.OrdinalIgnoreCase);
    Assert.Equal(1, client.StartCalls);
    Assert.Equal(1, access.AcquireCalls);
    Assert.Equal(1, access.ReleaseCalls);

    clock.UtcNow = seed.Start.AddMinutes(10);
    await worker.DrainOneAsync(default);
    Assert.Equal(1, client.StartCalls);
    Assert.Equal(1, access.AcquireCalls);
    Assert.Equal(1, access.ReleaseCalls);

    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Completed";
      await context.SaveChangesAsync();
    }
    Assert.True(await store.QueueStopAsync(seed.SessionId, clock.UtcNow));
    client.BlockStartUntilCanceled = false;

    await worker.DrainOneAsync(default);

    Assert.Equal(PolarH10RecordingOutcome.NotStarted, (await store.FindByIdAsync(job.Id))!.Outcome);
    Assert.Equal(1, client.StartCalls);
    Assert.Equal(2, access.AcquireCalls);
    Assert.Equal(2, access.ReleaseCalls);
  }

  [Fact]
  public async Task Existing_retryable_automatic_start_does_not_interrupt_an_active_workout()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      PolarH10RecordingEntity row = await context.PolarH10Recordings.SingleAsync();
      row.Status = PolarH10RecordingOutcome.Retryable.ToString();
      row.AttemptCount = 1;
      row.AvailableAtUtc = seed.Start;
      row.LastError = "Prior installed worker failure";
      await context.SaveChangesAsync();
    }
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start);
    var access = new TrackingMemoryAccessCoordinator();
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(access);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    PolarH10RecordingJob deferred = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.ReviewRequired, deferred.Outcome);
    Assert.Equal(0, client.StatusCalls);
    Assert.Equal(0, client.StartCalls);
    Assert.Equal(0, access.AcquireCalls);
    Assert.Equal(0, access.ReleaseCalls);
  }

  [Fact]
  public async Task Expired_StartPending_lease_is_not_replayed_after_worker_restart()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      PolarH10RecordingEntity row = await context.PolarH10Recordings.SingleAsync();
      row.AttemptCount = 1;
      row.LeaseExpiresAtUtc = seed.Start.AddSeconds(-1);
      row.AvailableAtUtc = seed.Start.AddMinutes(-1);
      await context.SaveChangesAsync();
    }
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start);
    var access = new TrackingMemoryAccessCoordinator();
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(access);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    PolarH10RecordingJob deferred = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.ReviewRequired, deferred.Outcome);
    Assert.Equal(2, deferred.AttemptCount);
    Assert.Equal(0, client.StartCalls);
    Assert.Equal(0, access.AcquireCalls);
  }

  [Fact]
  public async Task Confirmed_start_is_durably_recorded_after_the_PFTP_deadline_fires()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      await context.SaveChangesAsync();
    }
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start)
    {
      BlockStartUntilCanceled = true,
      ConfirmAfterStartCancellation = true,
    };
    var access = new TrackingMemoryAccessCoordinator();
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(access);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance)
    {
      AutomaticStartTimeout = TimeSpan.FromMilliseconds(50),
    };

    await worker.DrainOneAsync(default).WaitAsync(TimeSpan.FromSeconds(2));

    PolarH10RecordingJob recording = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.Recording, recording.Outcome);
    Assert.Equal(seed.Start, recording.StartConfirmedAtUtc);
    Assert.Equal(1, access.AcquireCalls);
    Assert.Equal(1, access.ReleaseCalls);
  }

  [Fact]
  public async Task Already_active_exact_recording_preserves_the_requested_alignment_time()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(seed.SessionId, seed.ProfileId, $"tr-{seed.SessionId:N}", seed.EnrollmentId,
      "Automatic", PolarH10SampleType.HeartRate, 1, seed.Start);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      WorkoutSessionEntity session = await context.WorkoutSessions.SingleAsync();
      session.State = "Running";
      await context.SaveChangesAsync();
    }
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start) { StartWasIssued = false };
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new TrackingMemoryAccessCoordinator());
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start.AddSeconds(12)), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    PolarH10RecordingJob recording = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.Recording, recording.Outcome);
    Assert.Equal(seed.Start, recording.StartConfirmedAtUtc);
  }

  [Fact]
  public async Task Worker_never_touches_PFTP_when_live_connection_suspension_is_refused()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Seed seed) = await CreateDatabaseAsync();
    var store = new PolarH10RecordingStore(factory);
    PolarH10RecordingJob job = await store.EnqueueAsync(null, seed.ProfileId, "manual-no-lease", seed.EnrollmentId,
      "Manual", PolarH10SampleType.HeartRate, 1, seed.Start);
    var client = new FakeMemoryClient(seed.EnrollmentId, job.ExerciseId, seed.Start);
    var services = new ServiceCollection();
    services.AddSingleton<IPolarH10RecordingStore>(store);
    services.AddSingleton<IPolarH10MemoryClient>(client);
    services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new TrackingMemoryAccessCoordinator(acquireResult: false));
    await using ServiceProvider provider = services.BuildServiceProvider();
    var worker = new PolarH10MemoryWorker(provider.GetRequiredService<IServiceScopeFactory>(),
      new FixedOptionsMonitor<PolarH10MemoryOptions>(new() { Enabled = true, LeaseSeconds = 30, PollSeconds = 1 }),
      new FixedTimeProvider(seed.Start), new FakeWakeSignal(), new PolarH10OperationGate(), NullLogger<PolarH10MemoryWorker>.Instance);

    await worker.DrainOneAsync(default);

    Assert.Equal(0, client.StatusCalls);
    Assert.Equal(0, client.StartCalls);
    PolarH10RecordingJob unchanged = (await store.FindByIdAsync(job.Id))!;
    Assert.Equal(PolarH10RecordingOutcome.Retryable, unchanged.Outcome);
    Assert.Null(unchanged.LeaseExpiresAtUtc);
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

  private sealed class FakeMemoryClient(Guid enrollmentId, string exerciseId, DateTimeOffset startedAt, bool remoteExists = false) : IPolarH10MemoryClient, IPolarH10MemorySession
  {
    private bool recording;
    public bool RemoteExists { get; private set; } = remoteExists;
    public int StartCalls { get; private set; }
    public int StatusCalls { get; private set; }
    public int DeleteCalls { get; private set; }
    public int DeleteFailuresRemaining { get; set; }
    public bool BlockStartUntilCanceled { get; set; }
    public bool ConfirmAfterStartCancellation { get; set; }
    public bool StartWasIssued { get; set; } = true;

    public Guid EnrollmentId => enrollmentId;
    public string DeviceId => "001122334455";
    public string DisplayName => "Polar H10";
    public Task<IPolarH10MemorySession> OpenAsync(Guid? requestedEnrollmentId, CancellationToken cancellationToken = default)
    {
      Assert.Equal(enrollmentId, requestedEnrollmentId);
      return Task.FromResult<IPolarH10MemorySession>(this);
    }
    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default)
    {
      StatusCalls++;
      return Task.FromResult(new PolarH10DeviceRecordingStatus(enrollmentId, "001122334455", "Polar H10", recording, recording ? exerciseId : null));
    }
    public async Task<PolarH10StartResult> StartAsync(string requestedExerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default)
    {
      Assert.Equal(exerciseId, requestedExerciseId);
      StartCalls++;
      if (BlockStartUntilCanceled)
      {
        try { await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken); }
        catch (OperationCanceledException) when (ConfirmAfterStartCancellation) { }
      }
      recording = true;
      RemoteExists = true;
      return new(
        new PolarH10DeviceRecordingStatus(enrollmentId, "001122334455", "Polar H10", true, exerciseId),
        StartWasIssued,
        StartWasIssued ? startedAt : null);
    }
    public Task StopAsync(CancellationToken cancellationToken = default) { recording = false; return Task.CompletedTask; }
    public Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<PolarH10RemoteRecording>>(RemoteExists ? [new($"/{exerciseId}/SAMPLES.BPB", 4)] : []);
    public Task<PolarH10MemoryRecord> FetchAsync(string remotePath, DateTimeOffset requestedStart, CancellationToken cancellationToken = default) =>
      Task.FromResult(new PolarH10MemoryRecord(exerciseId, remotePath, startedAt, startedAt.AddSeconds(4), PolarH10SampleType.HeartRate, 1,
        new byte[] { 1, 2, 3, 4 }, new ushort[] { 100, 101, 102, 103 }.Select((value, index) => new PolarH10HeartRateSample(startedAt.AddSeconds(index), value)).ToArray(), []));
    public Task DeleteAsync(string remotePath, CancellationToken cancellationToken = default)
    {
      Assert.Equal($"/{exerciseId}/SAMPLES.BPB", remotePath);
      DeleteCalls++;
      if (DeleteFailuresRemaining-- > 0) throw new IOException("synthetic removal failure");
      RemoteExists = false;
      return Task.CompletedTask;
    }
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }

  private sealed class FakeWakeSignal : IGarminActivityUploadWakeSignal
  {
    public int Count { get; private set; }
    public void Wake() => Count++;
  }

  private sealed class TrackingMemoryAccessCoordinator(bool acquireResult = true) : IPolarH10MemoryAccessCoordinator
  {
    public int AcquireCalls { get; private set; }
    public int ReleaseCalls { get; private set; }

    public Task<Guid> ResolveEnrollmentIdAsync(Guid? enrollmentId, CancellationToken cancellationToken = default) =>
      Task.FromResult(enrollmentId ?? throw new InvalidOperationException());

    public Task<IPolarH10MemoryAccessLease> AcquireAsync(Guid? enrollmentId, CancellationToken cancellationToken = default)
    {
      AcquireCalls++;
      if (!acquireResult)
        throw new InvalidOperationException("The exact H10 live connection could not be released for a memory operation.");
      return Task.FromResult<IPolarH10MemoryAccessLease>(new Lease(enrollmentId ?? throw new InvalidOperationException(), this));
    }

    private sealed class Lease(Guid enrollmentId, TrackingMemoryAccessCoordinator owner) : IPolarH10MemoryAccessLease
    {
      private int disposed;
      public Guid EnrollmentId { get; } = enrollmentId;

      public ValueTask DisposeAsync()
      {
        if (Interlocked.Exchange(ref disposed, 1) == 0)
          owner.ReleaseCalls++;
        return ValueTask.CompletedTask;
      }
    }
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
