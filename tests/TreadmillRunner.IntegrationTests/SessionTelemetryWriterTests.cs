using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionTelemetryWriterTests
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
    DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
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

  private static SessionTelemetryWrite CreateWrite(Guid sessionId, long sequence, DateTimeOffset capturedAt, Guid authorityId) =>
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
        heartRateBpm: null,
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
      authorityId);

  private sealed class RecordingSessionStore : ISessionStore
  {
    public List<IReadOnlyList<SessionSample>> Batches { get; } = [];
    public SessionRecoveryCheckpoint? LastCheckpoint { get; private set; }
    public TaskCompletionSource<bool> Persisted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    public Task AppendSamplesAndRecoveryCheckpointAsync(
      IReadOnlyList<SessionSample> samples,
      SessionRecoveryCheckpoint checkpoint,
      CancellationToken cancellationToken = default)
    {
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
