using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Live;
using Xunit.Abstractions;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionTelemetryWriterTests(ITestOutputHelper output)
{
  [Fact]
  public async Task Samples_captured_in_one_second_are_coalesced_and_checkpoint_is_durable_within_one_second_window()
  {
    var store = new RecordingSessionStore();
    using ServiceProvider services = new ServiceCollection()
      .AddSingleton<ISessionStore>(store)
      .BuildServiceProvider();
    var writer = new SessionTelemetryWriter(
      services.GetRequiredService<IServiceScopeFactory>(),
      NullLogger.Instance,
      TimeProvider.System,
      static (_, _) => Task.FromResult(true));
    Guid sessionId = Guid.NewGuid();
    Guid authorityId = Guid.NewGuid();
    DateTimeOffset capturedAt = DateTimeOffset
      .FromUnixTimeSeconds(DateTimeOffset.UtcNow.ToUnixTimeSeconds())
      .AddMilliseconds(100);
    var writes = Enumerable.Range(0, 3)
      .Select(sequence => CreateWrite(sessionId, sequence, capturedAt.AddMilliseconds(sequence * 100), authorityId))
      .ToArray();

    Assert.All(writes, write => Assert.True(writer.TryEnqueue(write)));
    Task run = writer.RunAsync(CancellationToken.None);

    await store.Persisted.Task.WaitAsync(TimeSpan.FromSeconds(2));
    Assert.Single(store.Batches);
    Assert.Equal(3, store.Batches.Single().Count);
    Assert.Equal(2, store.LastCheckpoint!.SessionVersion);
    Assert.Equal(2, store.LastCheckpoint.Progression.CurrentStepIndex);

    writer.Complete();
    await run.WaitAsync(TimeSpan.FromSeconds(2));
  }

  [Fact]
  public async Task Queued_write_is_discarded_when_generation_is_stale_before_database_write()
  {
    var store = new RecordingSessionStore();
    using ServiceProvider services = new ServiceCollection()
      .AddSingleton<ISessionStore>(store)
      .BuildServiceProvider();
    var checkedCurrent = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    var writer = new SessionTelemetryWriter(
      services.GetRequiredService<IServiceScopeFactory>(),
      NullLogger.Instance,
      TimeProvider.System,
      (_, _) =>
      {
        checkedCurrent.TrySetResult(true);
        return Task.FromResult(false);
      });

    Assert.True(writer.TryEnqueue(CreateWrite(Guid.NewGuid(), 0, DateTimeOffset.UtcNow, Guid.NewGuid())));
    Task run = writer.RunAsync(CancellationToken.None);
    await checkedCurrent.Task.WaitAsync(TimeSpan.FromSeconds(2));
    Assert.Empty(store.Batches);

    writer.Complete();
    await run.WaitAsync(TimeSpan.FromSeconds(2));
  }

  [Fact]
  public async Task Diagnostic_metadata_follows_sample_from_enqueue_to_commit_without_heart_rate_value()
  {
    string directory = Path.Combine(Path.GetTempPath(), $"telemetry-diagnostics-{Guid.NewGuid():N}");
    var store = new RecordingSessionStore();
    using ServiceProvider services = new ServiceCollection().AddSingleton<ISessionStore>(store).BuildServiceProvider();
    using var journal = new BleDiagnosticJournal(directory, NullLogger<BleDiagnosticJournal>.Instance);
    await journal.StartAsync(CancellationToken.None);
    try
    {
      Guid sessionId = Guid.NewGuid();
      Guid profileId = Guid.NewGuid();
      Guid enrollmentId = Guid.NewGuid();
      DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
      var diagnostic = new SessionHeartRateDiagnostic(
        profileId, enrollmentId, 17, 0.25, "Valid", true, "Available");
      var writer = new SessionTelemetryWriter(
        services.GetRequiredService<IServiceScopeFactory>(), NullLogger.Instance, TimeProvider.System,
        static (_, _) => Task.FromResult(true), journal);
      Assert.True(writer.TryEnqueue(CreateWrite(
        sessionId, 41, capturedAt, Guid.NewGuid(), diagnostic, heartRateBpm: 120)));
      Task run = writer.RunAsync(CancellationToken.None);
      await store.Persisted.Task.WaitAsync(TimeSpan.FromSeconds(2));
      writer.Complete();
      await run.WaitAsync(TimeSpan.FromSeconds(2));
      await journal.StopAsync(CancellationToken.None);

      JsonElement[] events = await ReadEventsAsync(directory, sessionId);
      int serializedEventBytes = System.Text.Encoding.UTF8.GetByteCount(
        (await File.ReadAllLinesAsync(Path.Combine(directory, "bluetooth.jsonl")))
          .Single(line => line.Contains("\"sample-committed\"", StringComparison.Ordinal)));
      output.WriteLine("Serialized committed sample diagnostic: {0} UTF-8 bytes.", serializedEventBytes);
      Assert.InRange(serializedEventBytes, 1, 1024);
      Assert.Equal(new[] { "sample-enqueued", "sample-committed" },
        events.Select(entry => entry.GetProperty("Phase").GetString()));
      Assert.All(events, entry =>
      {
        Assert.Equal(enrollmentId, entry.GetProperty("EnrollmentId").GetGuid());
        Assert.Equal(profileId, entry.GetProperty("ProfileId").GetGuid());
        Assert.Equal(17, entry.GetProperty("Generation").GetInt64());
        Assert.Equal(41, entry.GetProperty("SampleSequence").GetInt64());
        Assert.Equal(capturedAt, entry.GetProperty("CapturedAtUtc").GetDateTimeOffset());
        Assert.True(entry.GetProperty("HasHeartRate").GetBoolean());
        Assert.Equal("Available", entry.GetProperty("Reason").GetString());
        Assert.False(entry.TryGetProperty("HeartRateBpm", out _));
      });
    }
    finally
    {
      if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    }
  }

  [Fact]
  public async Task Stale_generation_discard_is_recorded_for_the_captured_sample()
  {
    string directory = Path.Combine(Path.GetTempPath(), $"telemetry-diagnostics-{Guid.NewGuid():N}");
    var store = new RecordingSessionStore();
    using ServiceProvider services = new ServiceCollection().AddSingleton<ISessionStore>(store).BuildServiceProvider();
    using var journal = new BleDiagnosticJournal(directory, NullLogger<BleDiagnosticJournal>.Instance);
    await journal.StartAsync(CancellationToken.None);
    try
    {
      Guid sessionId = Guid.NewGuid();
      var diagnostic = new SessionHeartRateDiagnostic(
        Guid.NewGuid(), Guid.NewGuid(), 4, 7, "Valid", false, "ObservationStale");
      var writer = new SessionTelemetryWriter(
        services.GetRequiredService<IServiceScopeFactory>(), NullLogger.Instance, TimeProvider.System,
        static (_, _) => Task.FromResult(false), journal);
      Assert.True(writer.TryEnqueue(CreateWrite(
        sessionId, 9, DateTimeOffset.UtcNow, Guid.NewGuid(), diagnostic)));
      writer.Complete();
      await writer.RunAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(2));
      await journal.StopAsync(CancellationToken.None);

      JsonElement stale = Assert.Single(await ReadEventsAsync(directory, sessionId),
        entry => entry.GetProperty("Phase").GetString() == "sample-stale-generation-discarded");
      Assert.Equal(9, stale.GetProperty("SampleSequence").GetInt64());
      Assert.Equal("ObservationStale", stale.GetProperty("Reason").GetString());
      Assert.Empty(store.Batches);
    }
    finally
    {
      if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    }
  }

  [Fact]
  public async Task Retry_and_nonretryable_discard_have_distinct_sanitized_phases()
  {
    string directory = Path.Combine(Path.GetTempPath(), $"telemetry-diagnostics-{Guid.NewGuid():N}");
    var retryStore = new RecordingSessionStore { Failure = new IOException("sensitive"), FailuresRemaining = 1 };
    using ServiceProvider retryServices = new ServiceCollection().AddSingleton<ISessionStore>(retryStore).BuildServiceProvider();
    using var journal = new BleDiagnosticJournal(directory, NullLogger<BleDiagnosticJournal>.Instance);
    await journal.StartAsync(CancellationToken.None);
    try
    {
      Guid retrySession = Guid.NewGuid();
      var diagnostic = new SessionHeartRateDiagnostic(Guid.NewGuid(), Guid.NewGuid(), 2, null, "Unavailable", false, "NoSelectedSource");
      var retryWriter = new SessionTelemetryWriter(
        retryServices.GetRequiredService<IServiceScopeFactory>(), NullLogger.Instance, TimeProvider.System,
        static (_, _) => Task.FromResult(true), journal);
      Assert.True(retryWriter.TryEnqueue(CreateWrite(retrySession, 1, DateTimeOffset.UtcNow, Guid.NewGuid(), diagnostic)));
      Task retryRun = retryWriter.RunAsync(CancellationToken.None);
      await retryStore.Persisted.Task.WaitAsync(TimeSpan.FromSeconds(3));
      retryWriter.Complete();
      await retryRun.WaitAsync(TimeSpan.FromSeconds(2));

      Guid nonretryableSession = Guid.NewGuid();
      var nonretryableStore = new RecordingSessionStore { Failure = new InvalidOperationException("sensitive"), FailuresRemaining = 1 };
      using ServiceProvider nonretryableServices = new ServiceCollection().AddSingleton<ISessionStore>(nonretryableStore).BuildServiceProvider();
      var nonretryableWriter = new SessionTelemetryWriter(
        nonretryableServices.GetRequiredService<IServiceScopeFactory>(), NullLogger.Instance, TimeProvider.System,
        static (_, _) => Task.FromResult(true), journal);
      Assert.True(nonretryableWriter.TryEnqueue(CreateWrite(nonretryableSession, 3, DateTimeOffset.UtcNow, Guid.NewGuid(), diagnostic)));
      nonretryableWriter.Complete();
      await nonretryableWriter.RunAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(2));
      await journal.StopAsync(CancellationToken.None);

      JsonElement[] retryEvents = await ReadEventsAsync(directory, retrySession);
      Assert.Contains(retryEvents, entry => entry.GetProperty("Phase").GetString() == "sample-retry" &&
        entry.GetProperty("Failure").GetString() == nameof(IOException));
      Assert.Contains(retryEvents, entry => entry.GetProperty("Phase").GetString() == "sample-committed");
      JsonElement nonretryable = Assert.Single(await ReadEventsAsync(directory, nonretryableSession),
        entry => entry.GetProperty("Phase").GetString() == "sample-nonretryable-discarded");
      Assert.Equal(nameof(InvalidOperationException), nonretryable.GetProperty("Failure").GetString());
      Assert.DoesNotContain("sensitive", string.Join('\n', await File.ReadAllLinesAsync(Path.Combine(directory, "bluetooth.jsonl"))));
    }
    finally
    {
      if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    }
  }

  [Fact]
  public async Task Overflow_replacement_and_completed_writer_discard_are_recorded()
  {
    string directory = Path.Combine(Path.GetTempPath(), $"telemetry-diagnostics-{Guid.NewGuid():N}");
    var store = new RecordingSessionStore();
    using ServiceProvider services = new ServiceCollection().AddSingleton<ISessionStore>(store).BuildServiceProvider();
    using var journal = new BleDiagnosticJournal(directory, NullLogger<BleDiagnosticJournal>.Instance);
    await journal.StartAsync(CancellationToken.None);
    try
    {
      var writer = new SessionTelemetryWriter(
        services.GetRequiredService<IServiceScopeFactory>(), NullLogger.Instance, TimeProvider.System,
        static (_, _) => Task.FromResult(true), journal);
      var diagnostic = new SessionHeartRateDiagnostic(Guid.NewGuid(), Guid.NewGuid(), 1, 6, "Unavailable", false, "ObservationStale");
      DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
      for (int index = 0; index < 256; index++)
        Assert.True(writer.TryEnqueue(CreateWrite(Guid.NewGuid(), index, capturedAt.AddTicks(index), Guid.NewGuid(), diagnostic)));
      Guid overflowSession = Guid.NewGuid();
      Assert.True(writer.TryEnqueue(CreateWrite(overflowSession, 256, capturedAt, Guid.NewGuid(), diagnostic)));
      Assert.True(writer.TryEnqueue(CreateWrite(overflowSession, 257, capturedAt, Guid.NewGuid(), diagnostic)));
      writer.Complete();
      Assert.False(writer.TryEnqueue(CreateWrite(overflowSession, 258, capturedAt, Guid.NewGuid(), diagnostic)));
      await journal.StopAsync(CancellationToken.None);

      JsonElement[] events = await ReadEventsAsync(directory, overflowSession);
      Assert.Contains(events, entry => entry.GetProperty("Phase").GetString() == "sample-overflow-replaced" &&
        entry.GetProperty("SampleSequence").GetInt64() == 256);
      Assert.Contains(events, entry => entry.GetProperty("Phase").GetString() == "sample-writer-completed-discarded" &&
        entry.GetProperty("SampleSequence").GetInt64() == 258);
    }
    finally
    {
      if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    }
  }

  [Theory]
  [InlineData(false, false, "NoSelectedSource")]
  [InlineData(true, false, "SampleMissingHeartRate")]
  [InlineData(true, true, "Available")]
  public void Capture_classifies_the_same_snapshot_used_for_the_session_sample(
    bool selected,
    bool sampleHasHeartRate,
    string expectedReason)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    Guid profileId = Guid.NewGuid();
    Guid enrollmentId = Guid.NewGuid();
    HeartRateSourceSnapshot source = new(
      enrollmentId, "must-not-be-journaled", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar,
      DeviceConnectionState.Ready, 12, 120, now, null, Quality: HeartRateSignalQuality.Valid,
      ContactState: HeartRateContactState.Detected);
    DeviceConnectionSnapshot connection = new(
      DeviceRole.HeartRate, DeviceConnectionState.Ready, 12, "must-not-be-journaled", null, null, now, null);
    DeviceTelemetrySnapshot snapshot = new(
      now,
      new DeviceConnectionSnapshot(DeviceRole.Treadmill, DeviceConnectionState.Ready, 1, null, null, null, now, null),
      connection,
      null,
      selected ? (ushort)120 : null,
      selected ? now : null,
      null,
      selected ? [source] : [],
      selected ? enrollmentId : null,
      SelectedHeartRateQuality: selected ? HeartRateSignalQuality.Valid : HeartRateSignalQuality.Unavailable);
    SessionSample sample = CreateWrite(
      Guid.NewGuid(), 5, now, Guid.NewGuid(), heartRateBpm: sampleHasHeartRate ? (ushort)120 : null).Sample;

    SessionHeartRateDiagnostic result = LiveSessionCoordinator.CaptureHeartRateDiagnostic(
      profileId, snapshot, sample, TimeSpan.FromSeconds(5));

    Assert.Equal(expectedReason, result.Reason);
    Assert.Equal(sampleHasHeartRate, result.HasHeartRate);
    Assert.Equal(selected ? enrollmentId : null, result.EnrollmentId);
    Assert.Equal(12, result.SourceGeneration);
  }

  [Theory]
  [InlineData(DeviceConnectionState.Disconnected, HeartRateSignalQuality.Valid, 0, "SourceNotReady")]
  [InlineData(DeviceConnectionState.Ready, HeartRateSignalQuality.ContactLost, 0, "QualityNotValid")]
  [InlineData(DeviceConnectionState.Ready, HeartRateSignalQuality.Valid, null, "MissingObservation")]
  [InlineData(DeviceConnectionState.Ready, HeartRateSignalQuality.Valid, 1, "ObservationInFuture")]
  [InlineData(DeviceConnectionState.Ready, HeartRateSignalQuality.Valid, -6, "ObservationStale")]
  public void Capture_reports_the_displayed_source_rejection_cause_when_no_source_is_selected(
    DeviceConnectionState state,
    HeartRateSignalQuality quality,
    int? observedOffsetSeconds,
    string expectedReason)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    DateTimeOffset? observedAt = observedOffsetSeconds is { } offset ? now.AddSeconds(offset) : null;
    Guid enrollmentId = Guid.NewGuid();
    HeartRateSourceSnapshot source = new(
      enrollmentId, "candidate", HeartRateDeviceKind.ChestStrap, HeartRateDeviceFamily.Polar,
      state, 21, null, observedAt, null, Quality: quality);
    DeviceTelemetrySnapshot snapshot = new(
      now,
      new DeviceConnectionSnapshot(DeviceRole.Treadmill, DeviceConnectionState.Ready, 1, null, null, null, now, null),
      new DeviceConnectionSnapshot(DeviceRole.HeartRate, state, 21, "candidate", null, null, observedAt, null),
      null, null, observedAt, null, [source], SelectedHeartRateQuality: quality);
    SessionSample sample = CreateWrite(Guid.NewGuid(), 1, now, Guid.NewGuid()).Sample;

    SessionHeartRateDiagnostic result = LiveSessionCoordinator.CaptureHeartRateDiagnostic(
      Guid.NewGuid(), snapshot, sample, TimeSpan.FromSeconds(5));

    Assert.Equal(expectedReason, result.Reason);
    Assert.Equal(enrollmentId, result.EnrollmentId);
    Assert.Equal(21, result.SourceGeneration);
  }

  private static SessionTelemetryWrite CreateWrite(
    Guid sessionId,
    long sequence,
    DateTimeOffset capturedAt,
    Guid authorityId,
    SessionHeartRateDiagnostic? diagnostic = null,
    ushort? heartRateBpm = null) =>
    new(
      sessionId,
      new SessionSample(
        sessionId,
        sequence,
        capturedAt,
        TimeSpan.FromSeconds(sequence),
        plannedSpeedKph: 5,
        requestedSpeedKph: 5,
        measuredSpeedKph: 5,
        plannedInclinePercent: 0,
        requestedInclinePercent: 0,
        measuredInclinePercent: 0,
        heartRateBpm,
        distanceKilometers: sequence * .001,
        estimatedKilocalories: sequence * .1,
        telemetryAge: TimeSpan.Zero,
        metricAlgorithmVersion: SessionMetricAlgorithms.EstimatedCaloriesV2),
      new SessionRecoveryCheckpoint(
        sessionId,
        capturedAt,
        SessionState.Running,
        sequence,
        capturedAt,
        new WorkoutProgressionCheckpoint((int)sequence, TimeSpan.FromSeconds(sequence), sequence * .001, TimeSpan.Zero, 0),
        sequence * .001,
        5,
        0,
        null,
        null,
        HeartRateAutomationMode.Disabled,
        1),
      1,
      1,
      authorityId,
      diagnostic);

  private static async Task<JsonElement[]> ReadEventsAsync(string directory, Guid sessionId)
  {
    var events = new List<JsonElement>();
    foreach (string line in await File.ReadAllLinesAsync(Path.Combine(directory, "bluetooth.jsonl")))
    {
      using JsonDocument document = JsonDocument.Parse(line);
      JsonElement entry = document.RootElement.GetProperty("Event");
      if (entry.TryGetProperty("SessionId", out JsonElement session) &&
          session.ValueKind == JsonValueKind.String && session.GetGuid() == sessionId)
        events.Add(entry.Clone());
    }
    return events.ToArray();
  }

  private sealed class RecordingSessionStore : ISessionStore
  {
    public List<IReadOnlyList<SessionSample>> Batches { get; } = [];
    public SessionRecoveryCheckpoint? LastCheckpoint { get; private set; }
    public TaskCompletionSource<bool> Persisted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public Exception? Failure { get; init; }
    public int FailuresRemaining { get; set; }

    public Task AppendSamplesAndRecoveryCheckpointAsync(
      IReadOnlyList<SessionSample> samples,
      SessionRecoveryCheckpoint checkpoint,
      CancellationToken cancellationToken = default)
    {
      if (FailuresRemaining > 0)
      {
        FailuresRemaining--;
        return Task.FromException(Failure ?? new IOException());
      }
      Batches.Add(samples);
      LastCheckpoint = checkpoint;
      Persisted.TrySetResult(true);
      return Task.CompletedTask;
    }

    public Task CreateAsync(NewWorkoutSession session, CancellationToken cancellationToken = default) => Unsupported();
    public Task MarkRunningAsync(Guid sessionId, DateTimeOffset startedAt, CancellationToken cancellationToken = default) => Unsupported();
    public Task AppendSampleAsync(SessionSample sample, CancellationToken cancellationToken = default) => Unsupported();
    public Task AppendSampleAndRecoveryCheckpointAsync(SessionSample sample, SessionRecoveryCheckpoint checkpoint, CancellationToken cancellationToken = default) => Unsupported();
    public Task AppendEventAsync(Guid sessionId, SessionEvent sessionEvent, CancellationToken cancellationToken = default) => Unsupported();
    public Task FinalizeAsync(SessionSummary summary, CancellationToken cancellationToken = default) => Unsupported();
    public Task SaveDebriefAsync(SessionDebrief debrief, CancellationToken cancellationToken = default) => Unsupported();
    public Task<StoredWorkoutSession?> FindAsync(Guid sessionId, CancellationToken cancellationToken = default) => Task.FromResult<StoredWorkoutSession?>(null);
    public Task<StoredWorkoutSessionDisplay?> FindDisplayAsync(Guid sessionId, CancellationToken cancellationToken = default) => Task.FromResult<StoredWorkoutSessionDisplay?>(null);
    public Task<SessionAnalytics?> CalculateAnalyticsAsync(Guid sessionId, IReadOnlyList<HeartRateZone> heartRateZones, CancellationToken cancellationToken = default) => Task.FromResult<SessionAnalytics?>(null);
    public Task<SessionSampleStatistics?> CalculateSampleStatisticsAsync(Guid sessionId, CancellationToken cancellationToken = default) => Task.FromResult<SessionSampleStatistics?>(null);
    public Task<SessionHistoryDetails?> GetHistoryDetailsAsync(Guid sessionId, IReadOnlyList<HeartRateZone>? heartRateZones = null, CancellationToken cancellationToken = default) => Task.FromResult<SessionHistoryDetails?>(null);
    public Task<IReadOnlyList<SessionSummary>> ListSummariesAsync(Guid userProfileId, int take = 50, CancellationToken cancellationToken = default, bool includeSystemTests = false) => Task.FromResult<IReadOnlyList<SessionSummary>>([]);
    public Task<HistoryDeletionPreview?> PreviewDeletionAsync(Guid sessionId, Guid userProfileId, CancellationToken cancellationToken = default) => Task.FromResult<HistoryDeletionPreview?>(null);
    public Task<HistoryDeletionResult> DeleteAsync(DeleteHistorySessionOperation operation, CancellationToken cancellationToken = default) => Unsupported<HistoryDeletionResult>();
    public Task<int> InterruptUnfinishedAsync(DateTimeOffset interruptedAt, string reason, CancellationToken cancellationToken = default) => Unsupported<int>();
    public Task SaveRecoveryCheckpointAsync(SessionRecoveryCheckpoint checkpoint, CancellationToken cancellationToken = default) => Unsupported();
    public Task<RecoverableWorkoutSession?> FindRecoverableAsync(CancellationToken cancellationToken = default) => Task.FromResult<RecoverableWorkoutSession?>(null);
    public Task<int> ReconcileActiveSessionsAsync(DateTimeOffset reconciledAtUtc, CancellationToken cancellationToken = default) => Unsupported<int>();

    private static Task Unsupported() => Task.FromException(new NotSupportedException());
    private static Task<T> Unsupported<T>() => Task.FromException<T>(new NotSupportedException());
  }
}
