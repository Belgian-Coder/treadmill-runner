using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Polar;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PolarH10AutomaticPreparationServiceTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string DatabasePath => Path.Combine(directory, "polar-h10-prepare.db");

  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Fact]
  public async Task Prepare_replaces_exact_active_recording_and_restores_live_hr_before_success()
  {
    (PolarH10RecordingStore store, Seed seed) = await CreateStoreAsync();
    var calls = new List<string>();
    var client = new TrackingMemoryClient(seed.EnrollmentId, "stale-exercise", calls);
    var access = new TrackingAccessCoordinator(seed.EnrollmentId, calls);
    var devices = new ReadyHeartRateCoordinator(seed.EnrollmentId);
    var wake = new TrackingWakeSignal();
    var service = new PolarH10AutomaticPreparationService(
      Options.Create(new PolarH10MemoryOptions { Enabled = true }),
      store,
      client,
      access,
      devices,
      new PolarH10OperationGate(),
      wake,
      TimeProvider.System,
      NullLogger<PolarH10AutomaticPreparationService>.Instance);

    await service.PrepareAsync(seed.SessionId, seed.ProfileId, seed.EnrollmentId);

    string requested = $"tr-{seed.SessionId:N}";
    Assert.Equal(requested, client.ActiveExerciseId);
    Assert.Equal(1, client.StopCalls);
    Assert.Equal(["/stale-exercise/SAMPLES.BPB"], client.DeletedPaths);
    Assert.Equal(1, client.StartMutationCalls);
    Assert.Equal(1, access.AcquireCalls);
    Assert.Equal(1, access.ReleaseCalls);
    Assert.True(calls.IndexOf("memory-dispose") < calls.IndexOf("access-release"));
    Assert.Equal(0, wake.Count);
    PolarH10RecordingJob job = Assert.IsType<PolarH10RecordingJob>(await store.FindAsync(seed.SessionId));
    Assert.Equal(PolarH10RecordingOutcome.Recording, job.Outcome);
    Assert.Equal(requested, job.ExerciseId);
  }

  [Fact]
  public async Task Prepare_fails_closed_when_active_recording_has_no_exact_identifier()
  {
    (PolarH10RecordingStore store, Seed seed) = await CreateStoreAsync();
    var client = new TrackingMemoryClient(seed.EnrollmentId, activeExerciseId: null, []) { ReportsAnonymousRecording = true };
    var wake = new TrackingWakeSignal();
    var service = new PolarH10AutomaticPreparationService(
      Options.Create(new PolarH10MemoryOptions { Enabled = true }),
      store,
      client,
      new TrackingAccessCoordinator(seed.EnrollmentId, []),
      new ReadyHeartRateCoordinator(seed.EnrollmentId),
      new PolarH10OperationGate(),
      wake,
      TimeProvider.System,
      NullLogger<PolarH10AutomaticPreparationService>.Instance);

    await Assert.ThrowsAsync<InvalidOperationException>(() =>
      service.PrepareAsync(seed.SessionId, seed.ProfileId, seed.EnrollmentId));

    Assert.Equal(0, client.StopCalls);
    Assert.Equal(0, client.StartMutationCalls);
    Assert.Empty(client.DeletedPaths);
    PolarH10RecordingJob job = Assert.IsType<PolarH10RecordingJob>(await store.FindAsync(seed.SessionId));
    Assert.Equal(PolarH10RecordingOutcome.DiscardCleanupPending, job.Outcome);
    Assert.Equal(1, wake.Count);
  }

  [Fact]
  public async Task Prepare_preserves_an_owned_undownloaded_recording_before_superseding_it()
  {
    (PolarH10RecordingStore store, Seed seed) = await CreateStoreAsync();
    const string staleExercise = "owned-stale";
    PolarH10RecordingJob stale = await store.EnqueueAsync(
      null, seed.ProfileId, staleExercise, seed.EnrollmentId, "Manual",
      PolarH10SampleType.HeartRate, 1, DateTimeOffset.UtcNow.AddMinutes(-2));
    await store.MarkRecordingAsync(stale.Id, DateTimeOffset.UtcNow.AddMinutes(-2));
    var client = new TrackingMemoryClient(seed.EnrollmentId, staleExercise, []);
    var wake = new TrackingWakeSignal();
    var service = new PolarH10AutomaticPreparationService(
      Options.Create(new PolarH10MemoryOptions { Enabled = true }), store, client,
      new TrackingAccessCoordinator(seed.EnrollmentId, []), new ReadyHeartRateCoordinator(seed.EnrollmentId),
      new PolarH10OperationGate(), wake, TimeProvider.System,
      NullLogger<PolarH10AutomaticPreparationService>.Instance);

    await service.PrepareAsync(seed.SessionId, seed.ProfileId, seed.EnrollmentId);

    PolarH10RecordingJob preserved = Assert.IsType<PolarH10RecordingJob>(await store.FindByIdAsync(stale.Id));
    Assert.Equal(PolarH10RecordingOutcome.Downloaded, preserved.Outcome);
    Assert.NotNull(preserved.PayloadSha256);
    Assert.Equal(1, client.FetchCalls);
    Assert.Equal(1, wake.Count);
  }

  [Fact]
  public async Task Prepare_queues_exact_cleanup_when_start_confirmation_is_uncertain()
  {
    (PolarH10RecordingStore store, Seed seed) = await CreateStoreAsync();
    var client = new TrackingMemoryClient(seed.EnrollmentId, null, []) { ThrowAfterStart = true };
    var wake = new TrackingWakeSignal();
    var service = new PolarH10AutomaticPreparationService(
      Options.Create(new PolarH10MemoryOptions { Enabled = true }), store, client,
      new TrackingAccessCoordinator(seed.EnrollmentId, []), new ReadyHeartRateCoordinator(seed.EnrollmentId),
      new PolarH10OperationGate(), wake, TimeProvider.System,
      NullLogger<PolarH10AutomaticPreparationService>.Instance);

    await Assert.ThrowsAsync<InvalidOperationException>(() =>
      service.PrepareAsync(seed.SessionId, seed.ProfileId, seed.EnrollmentId));

    PolarH10RecordingJob job = Assert.IsType<PolarH10RecordingJob>(await store.FindAsync(seed.SessionId));
    Assert.Equal(PolarH10RecordingOutcome.DiscardCleanupPending, job.Outcome);
    Assert.Equal($"tr-{seed.SessionId:N}", client.ActiveExerciseId);
    Assert.Equal(1, wake.Count);
  }

  [Fact]
  public async Task Prepare_queues_exact_cleanup_when_the_request_is_cancelled_after_start_dispatch()
  {
    (PolarH10RecordingStore store, Seed seed) = await CreateStoreAsync();
    using var cancellation = new CancellationTokenSource();
    var client = new TrackingMemoryClient(seed.EnrollmentId, null, [])
    {
      AfterStart = () => cancellation.Cancel(),
    };
    var wake = new TrackingWakeSignal();
    var service = new PolarH10AutomaticPreparationService(
      Options.Create(new PolarH10MemoryOptions { Enabled = true }), store, client,
      new TrackingAccessCoordinator(seed.EnrollmentId, []), new ReadyHeartRateCoordinator(seed.EnrollmentId),
      new PolarH10OperationGate(), wake, TimeProvider.System,
      NullLogger<PolarH10AutomaticPreparationService>.Instance);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
      service.PrepareAsync(seed.SessionId, seed.ProfileId, seed.EnrollmentId, cancellation.Token));

    PolarH10RecordingJob job = Assert.IsType<PolarH10RecordingJob>(await store.FindAsync(seed.SessionId));
    Assert.Equal(PolarH10RecordingOutcome.DiscardCleanupPending, job.Outcome);
    Assert.Equal(1, wake.Count);
  }

  private async Task<(PolarH10RecordingStore Store, Seed Seed)> CreateStoreAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    DateTimeOffset now = DateTimeOffset.UtcNow;
    Guid profileId = Guid.NewGuid(), workoutId = Guid.NewGuid(), revisionId = Guid.NewGuid(), sessionId = Guid.NewGuid(), enrollmentId = Guid.NewGuid();
    context.UserProfiles.Add(new UserProfileEntity { Id = profileId, DisplayName = "Runner", NormalizedDisplayName = "RUNNER", UnitSystem = "Metric", WeightKilograms = 70, Version = 1, CreatedAtUtc = now, UpdatedAtUtc = now });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = "Prepared run", Kind = "Structured", CreatedAtUtc = now });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity { Id = revisionId, WorkoutId = workoutId, RevisionNumber = 1, DefinitionJson = "{}", ContentSha256 = new string('a', 64), CreatedAtUtc = now });
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
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    });
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = sessionId,
      UserProfileId = profileId,
      UserProfileName = "Runner",
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Prepared run",
      State = "ArmedWaitingForPhysicalStart",
      ArmedAtUtc = now,
      MetricAlgorithmVersion = "v1",
      ControllerConfigurationJson = "{}",
      RecordPolarH10Memory = true,
    });
    await context.SaveChangesAsync();
    return (new PolarH10RecordingStore(factory), new(profileId, sessionId, enrollmentId));
  }

  private sealed record Seed(Guid ProfileId, Guid SessionId, Guid EnrollmentId);

  private sealed class TrackingMemoryClient(Guid enrollmentId, string? activeExerciseId, List<string> calls) : IPolarH10MemoryClient, IPolarH10MemorySession
  {
    private readonly HashSet<string> remotePaths = activeExerciseId is null ? [] : [$"/{activeExerciseId}/SAMPLES.BPB"];
    public string? ActiveExerciseId { get; private set; } = activeExerciseId;
    public bool ReportsAnonymousRecording { get; set; }
    public int StopCalls { get; private set; }
    public int StartMutationCalls { get; private set; }
    public int FetchCalls { get; private set; }
    public bool ThrowAfterStart { get; set; }
    public Action? AfterStart { get; set; }
    public List<string> DeletedPaths { get; } = [];
    public Guid EnrollmentId => enrollmentId;
    public string DeviceId => "001122334455";
    public string DisplayName => "Polar H10";

    public Task<IPolarH10MemorySession> OpenAsync(Guid? requestedEnrollmentId, CancellationToken cancellationToken = default)
    {
      Assert.Equal(enrollmentId, requestedEnrollmentId);
      calls.Add("memory-open");
      return Task.FromResult<IPolarH10MemorySession>(this);
    }

    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default) =>
      Task.FromResult(Status());

    public Task<PolarH10StartResult> StartAsync(string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default)
    {
      calls.Add("start-check");
      PolarH10DeviceRecordingStatus current = Status();
      if (current.IsRecording) return Task.FromResult(new PolarH10StartResult(current, false));
      StartMutationCalls++;
      ActiveExerciseId = exerciseId;
      remotePaths.Add($"/{exerciseId}/SAMPLES.BPB");
      AfterStart?.Invoke();
      cancellationToken.ThrowIfCancellationRequested();
      if (ThrowAfterStart) throw new TimeoutException("synthetic confirmation timeout");
      return Task.FromResult(new PolarH10StartResult(Status(), true, DateTimeOffset.UtcNow));
    }

    public Task StopAsync(CancellationToken cancellationToken = default)
    {
      calls.Add("stop");
      StopCalls++;
      ActiveExerciseId = null;
      ReportsAnonymousRecording = false;
      return Task.CompletedTask;
    }

    public Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<PolarH10RemoteRecording>>(remotePaths.Select(path => new PolarH10RemoteRecording(path, 4)).ToArray());

    public Task<PolarH10MemoryRecord> FetchAsync(string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default)
    {
      FetchCalls++;
      return Task.FromResult(new PolarH10MemoryRecord(
        remotePath.Split('/', StringSplitOptions.RemoveEmptyEntries)[0], remotePath,
        startedAtUtc, startedAtUtc.AddSeconds(2), PolarH10SampleType.HeartRate, 1,
        new byte[] { 90, 91 },
        [new(startedAtUtc, 90), new(startedAtUtc.AddSeconds(1), 91)],
        []));
    }

    public Task DeleteAsync(string remotePath, CancellationToken cancellationToken = default)
    {
      calls.Add("delete");
      Assert.True(remotePaths.Remove(remotePath));
      DeletedPaths.Add(remotePath);
      return Task.CompletedTask;
    }

    public ValueTask DisposeAsync() { calls.Add("memory-dispose"); return ValueTask.CompletedTask; }

    private PolarH10DeviceRecordingStatus Status() => new(
      enrollmentId,
      DeviceId,
      DisplayName,
      ActiveExerciseId is not null || ReportsAnonymousRecording,
      ActiveExerciseId);
  }

  private sealed class TrackingAccessCoordinator(Guid enrollmentId, List<string> calls) : IPolarH10MemoryAccessCoordinator
  {
    public int AcquireCalls { get; private set; }
    public int ReleaseCalls { get; private set; }
    public Task<Guid> ResolveEnrollmentIdAsync(Guid? requested, CancellationToken cancellationToken = default) =>
      Task.FromResult(requested ?? enrollmentId);
    public Task<IPolarH10MemoryAccessLease> AcquireAsync(Guid? requested, CancellationToken cancellationToken = default)
    {
      Assert.Equal(enrollmentId, requested);
      AcquireCalls++;
      calls.Add("access-acquire");
      return Task.FromResult<IPolarH10MemoryAccessLease>(new Lease(enrollmentId, this, calls));
    }

    private sealed class Lease(Guid enrollmentId, TrackingAccessCoordinator owner, List<string> calls) : IPolarH10MemoryAccessLease
    {
      public Guid EnrollmentId { get; } = enrollmentId;
      public ValueTask DisposeAsync()
      {
        owner.ReleaseCalls++;
        calls.Add("access-release");
        return ValueTask.CompletedTask;
      }
    }
  }

  private sealed class ReadyHeartRateCoordinator(Guid enrollmentId) : IReadOnlyDeviceCoordinator
  {
    public DeviceTelemetrySnapshot Current => Snapshot();
    public DeviceTelemetrySnapshot CurrentForProfile(Guid? profileId) => Snapshot();

    private DeviceTelemetrySnapshot Snapshot()
    {
      DateTimeOffset now = DateTimeOffset.UtcNow;
      var treadmill = new DeviceConnectionSnapshot(DeviceRole.Treadmill, DeviceConnectionState.Disconnected, 0, null, null, null, null, null);
      var heartRate = new DeviceConnectionSnapshot(DeviceRole.HeartRate, DeviceConnectionState.Ready, 1, "Polar H10", "heart-rate", "ChestStrap", now, null);
      var source = new HeartRateSourceSnapshot(enrollmentId, "Polar H10", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar,
        DeviceConnectionState.Ready, 1, 90, now, null, 100, now, HeartRateSignalQuality.Valid, HeartRateContactState.Detected);
      return new DeviceTelemetrySnapshot(now, treadmill, heartRate, null, 90, now, null, [source], enrollmentId,
        HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar, 1, "test", 100, now, HeartRateSignalQuality.Valid, HeartRateContactState.Detected);
    }
  }

  private sealed class TrackingWakeSignal : IPolarH10MemoryWakeSignal
  {
    public int Count { get; private set; }
    public void Wake() => Count++;
  }
}
