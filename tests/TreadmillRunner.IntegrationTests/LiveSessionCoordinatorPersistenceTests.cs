using System.Reflection;
using Microsoft.Extensions.DependencyInjection;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class LiveSessionCoordinatorPersistenceTests
{
  [Fact]
  public void Moving_terminal_session_keeps_observing_until_stopped_then_freezes_after_release()
  {
    Assert.False(LiveSessionCoordinator.ShouldFreezeTerminalSnapshot(
      SessionState.Completed,
      deviceConnectionsReleased: false));
    Assert.False(LiveSessionCoordinator.ShouldReleaseDeviceConnections(
      SessionState.Completed,
      deviceConnectionsReleased: false,
      isMoving: true,
      measuredSpeedKph: 6.5));
    Assert.True(LiveSessionCoordinator.ShouldReleaseDeviceConnections(
      SessionState.Completed,
      deviceConnectionsReleased: false,
      isMoving: false,
      measuredSpeedKph: 0));
    Assert.False(LiveSessionCoordinator.ShouldReleaseDeviceConnections(
      SessionState.Interrupted,
      deviceConnectionsReleased: false,
      isMoving: false,
      measuredSpeedKph: 0,
      resetPersistencePending: true));
    Assert.True(LiveSessionCoordinator.ShouldFreezeTerminalSnapshot(
      SessionState.Completed,
      deviceConnectionsReleased: true));
  }

  [Fact]
  public void Session_accepts_prior_telemetry_versions_and_connections_within_the_same_authority_epoch()
  {
    Guid sessionId = Guid.NewGuid();
    Guid authorityId = Guid.NewGuid();
    var write = new SessionTelemetryWrite(
      sessionId,
      null!,
      null!,
      SessionVersion: 4,
      ConnectionGeneration: 7,
      AuthorityId: authorityId);

    Assert.True(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write, sessionId, 5, 7, authorityId));
    Assert.True(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write, sessionId, 6, 7, authorityId));
    Assert.True(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write, sessionId, 6, 8, authorityId));
    Assert.False(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write with { SessionVersion = 7 }, sessionId, 6, 7, authorityId));
    Assert.False(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write, Guid.NewGuid(), 6, 7, authorityId));
    Assert.False(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write with { ConnectionGeneration = 8 }, sessionId, 6, 7, authorityId));
    Assert.False(LiveSessionCoordinator.IsTelemetryWriteCurrent(
      write, sessionId, 6, 7, Guid.NewGuid()));
  }

  [Fact]
  public async Task Terminal_persistence_cannot_overtake_a_pending_telemetry_flush()
  {
    var flush = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var terminalPersisted = false;
    Task ordered = LiveSessionCoordinator.PersistAfterTelemetryFlushAsync(
      flush.Task,
      () =>
      {
        terminalPersisted = true;
        return Task.CompletedTask;
      });

    await Task.Yield();
    Assert.False(terminalPersisted);
    Assert.False(ordered.IsCompleted);
    flush.SetResult();
    await ordered;
    Assert.True(terminalPersisted);

    terminalPersisted = false;
    await Assert.ThrowsAsync<IOException>(() => LiveSessionCoordinator.PersistAfterTelemetryFlushAsync(
      Task.FromException(new IOException("telemetry persistence failed")),
      () =>
      {
        terminalPersisted = true;
        return Task.CompletedTask;
      }));
    Assert.False(terminalPersisted);

    terminalPersisted = false;
    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => LiveSessionCoordinator.PersistAfterTelemetryFlushAsync(
      Task.FromCanceled(new CancellationToken(canceled: true)),
      () =>
      {
        terminalPersisted = true;
        return Task.CompletedTask;
      }));
    Assert.False(terminalPersisted);
  }

  [Fact]
  public async Task Slow_terminal_barrier_is_reported_as_a_retryable_session_conflict()
  {
    var barrier = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);

    InvalidOperationException error = await Assert.ThrowsAsync<InvalidOperationException>(() =>
      LiveSessionCoordinator.WaitForTerminalPersistenceBarrierAsync(
        barrier.Task,
        TimeSpan.FromMilliseconds(25),
        CancellationToken.None));

    Assert.Contains("still finishing", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Terminal_persistence_retry_does_not_append_the_terminal_event_twice()
  {
    var store = DispatchProxy.Create<ISessionStore, FaultInjectingSessionStore>();
    var fault = (FaultInjectingSessionStore)(object)store;
    fault.FinalizeFailuresRemaining = 1;
    var polarStore = DispatchProxy.Create<IPolarH10RecordingStore, SuccessfulPolarRecordingStore>();
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(store)
      .AddSingleton(polarStore)
      .BuildServiceProvider();
    var coordinator = new LiveSessionCoordinator(
      TimeProvider.System,
      services.GetRequiredService<IServiceScopeFactory>(),
      null!,
      null!,
      null!,
      null!,
      null!,
      new ApplicationMaintenanceState(),
      Microsoft.Extensions.Logging.Abstractions.NullLogger<LiveSessionCoordinator>.Instance);

    Guid sessionId = Guid.NewGuid();
    DateTimeOffset startedAt = DateTimeOffset.UtcNow.AddMinutes(-1);
    DateTimeOffset endedAt = DateTimeOffset.UtcNow;
    var summary = new SessionSummary(
      sessionId,
      Guid.NewGuid(),
      "Test profile",
      Guid.NewGuid(),
      "Test workout",
      SessionState.Completed,
      startedAt,
      endedAt,
      endedAt - startedAt,
      1.25,
      80,
      null,
      null,
      5,
      1,
      SessionOrigin.Simulator);
    var terminalEvent = new SessionCompletedEvent(endedAt);
    // The terminal effect is deliberately kept private because it closes over
    // per-session retry state. Exercise that production closure and its queue
    // rather than duplicating the closure in a test-only effect.
    MethodInfo method = typeof(LiveSessionCoordinator).GetMethod(
      "CreateTerminalPersistenceEffect",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "CreateTerminalPersistenceEffect");
    var effect = (Func<CancellationToken, Task>)method.Invoke(
      coordinator,
      [sessionId, terminalEvent, summary])!;

    var batch = new LiveEffectBatch();
    batch.Add(
      new LiveEffectMetadata(sessionId, 1, 1, Guid.NewGuid()),
      effect,
      terminal: true);
    MethodInfo queueMethod = typeof(LiveSessionCoordinator).GetMethod(
      "QueueEffects",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "QueueEffects");
    var queued = (Task)queueMethod.Invoke(
      coordinator,
      [batch, CancellationToken.None, false, false])!;
    await queued;

    Assert.Equal(1, fault.AppendEventCount);
    Assert.Equal(2, fault.FinalizeCount);
    Assert.Equal(sessionId, fault.AppendedSessionId);
    Assert.Same(terminalEvent, fault.AppendedEvent);
    Assert.Same(summary, fault.FinalizedSummary);
  }

  [Fact]
  public async Task Failure_propagating_reset_batch_stops_cleanup_and_does_not_poison_the_effect_queue()
  {
    await using ServiceProvider services = new ServiceCollection().BuildServiceProvider();
    var coordinator = new LiveSessionCoordinator(
      TimeProvider.System,
      services.GetRequiredService<IServiceScopeFactory>(),
      null!,
      null!,
      null!,
      null!,
      null!,
      new ApplicationMaintenanceState(),
      Microsoft.Extensions.Logging.Abstractions.NullLogger<LiveSessionCoordinator>.Instance);
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 1, 1, Guid.NewGuid());
    var cleanupRan = false;
    var failing = new LiveEffectBatch(TimeSpan.FromMilliseconds(100));
    failing.Add(metadata, _ => Task.FromException(new IOException("persistent")), terminal: true);
    failing.Add(metadata, _ =>
    {
      cleanupRan = true;
      return Task.CompletedTask;
    }, terminal: true);
    MethodInfo queueMethod = typeof(LiveSessionCoordinator).GetMethod(
      "QueueEffects",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "QueueEffects");

    var failed = (Task)queueMethod.Invoke(
      coordinator,
      [failing, CancellationToken.None, false, true])!;
    await Assert.ThrowsAsync<IOException>(() => failed);
    Assert.False(cleanupRan);

    var succeeding = new LiveEffectBatch();
    succeeding.Add(metadata, _ =>
    {
      cleanupRan = true;
      return Task.CompletedTask;
    }, terminal: true);
    var recovered = (Task)queueMethod.Invoke(
      coordinator,
      [succeeding, CancellationToken.None, false, true])!;
    await recovered;
    Assert.True(cleanupRan);
  }

  [Fact]
  public async Task Failed_terminal_persistence_effect_can_be_reused_by_reset_before_cleanup()
  {
    var store = DispatchProxy.Create<ISessionStore, FaultInjectingSessionStore>();
    var fault = (FaultInjectingSessionStore)(object)store;
    fault.FinalizeFailuresRemaining = int.MaxValue;
    var polarStore = DispatchProxy.Create<IPolarH10RecordingStore, SuccessfulPolarRecordingStore>();
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(store)
      .AddSingleton(polarStore)
      .BuildServiceProvider();
    var coordinator = new LiveSessionCoordinator(
      TimeProvider.System,
      services.GetRequiredService<IServiceScopeFactory>(),
      null!, null!, null!, null!, null!,
      new ApplicationMaintenanceState(),
      Microsoft.Extensions.Logging.Abstractions.NullLogger<LiveSessionCoordinator>.Instance);
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset startedAt = DateTimeOffset.UtcNow.AddMinutes(-1);
    DateTimeOffset endedAt = DateTimeOffset.UtcNow;
    var summary = new SessionSummary(
      sessionId, Guid.NewGuid(), "Test profile", Guid.NewGuid(), "Test workout",
      SessionState.Completed, startedAt, endedAt, endedAt - startedAt,
      1.25, 80, null, null, 5, 1, SessionOrigin.Simulator);
    MethodInfo createMethod = typeof(LiveSessionCoordinator).GetMethod(
      "CreateTerminalPersistenceEffect",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "CreateTerminalPersistenceEffect");
    var effect = (Func<CancellationToken, Task>)createMethod.Invoke(
      coordinator,
      [sessionId, new SessionCompletedEvent(endedAt), summary])!;
    MethodInfo queueMethod = typeof(LiveSessionCoordinator).GetMethod(
      "QueueEffects",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "QueueEffects");
    var metadata = new LiveEffectMetadata(sessionId, 1, 1, Guid.NewGuid());
    var first = new LiveEffectBatch(TimeSpan.FromMilliseconds(100));
    first.Add(metadata, effect, terminal: true);
    var failed = (Task)queueMethod.Invoke(
      coordinator,
      [first, CancellationToken.None, false, true])!;
    await Assert.ThrowsAsync<IOException>(() => failed);

    fault.FinalizeFailuresRemaining = 0;
    var cleanupRan = false;
    var retry = new LiveEffectBatch(TimeSpan.FromMilliseconds(100));
    retry.Add(metadata, effect, terminal: true);
    retry.Add(metadata, _ =>
    {
      cleanupRan = true;
      return Task.CompletedTask;
    }, terminal: true);
    var recovered = (Task)queueMethod.Invoke(
      coordinator,
      [retry, CancellationToken.None, false, true])!;
    await recovered;

    Assert.True(cleanupRan);
    Assert.Equal(1, fault.AppendEventCount);
    Assert.Same(summary, fault.FinalizedSummary);
  }

  [Fact]
  public async Task Reset_interruption_treats_an_already_deleted_session_as_idempotent_and_still_sweeps()
  {
    var store = DispatchProxy.Create<ISessionStore, MissingSessionStore>();
    var missing = (MissingSessionStore)(object)store;
    var polarStore = DispatchProxy.Create<IPolarH10RecordingStore, SuccessfulPolarRecordingStore>();
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(store)
      .AddSingleton(polarStore)
      .BuildServiceProvider();
    var coordinator = new LiveSessionCoordinator(
      TimeProvider.System,
      services.GetRequiredService<IServiceScopeFactory>(),
      null!, null!, null!, null!, null!,
      new ApplicationMaintenanceState(),
      Microsoft.Extensions.Logging.Abstractions.NullLogger<LiveSessionCoordinator>.Instance);
    MethodInfo createMethod = typeof(LiveSessionCoordinator).GetMethod(
      "CreateInterruptionPersistenceEffect",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "CreateInterruptionPersistenceEffect");
    var effect = (Func<CancellationToken, Task>)createMethod.Invoke(
      coordinator,
      [Guid.NewGuid(), DateTimeOffset.UtcNow, "Simulator reset.", true, true, false])!;

    await effect(CancellationToken.None);

    Assert.Equal(1, missing.InterruptCount);
    Assert.Equal(1, missing.SweepCount);
  }

  [Fact]
  public async Task Terminal_persistence_treats_an_intentionally_deleted_session_as_already_durable()
  {
    var store = DispatchProxy.Create<ISessionStore, MissingTerminalSessionStore>();
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(store)
      .BuildServiceProvider();
    var coordinator = new LiveSessionCoordinator(
      TimeProvider.System,
      services.GetRequiredService<IServiceScopeFactory>(),
      null!, null!, null!, null!, null!,
      new ApplicationMaintenanceState(),
      Microsoft.Extensions.Logging.Abstractions.NullLogger<LiveSessionCoordinator>.Instance);
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var summary = new SessionSummary(
      sessionId, Guid.NewGuid(), "Runner", Guid.NewGuid(), "Workout",
      SessionState.Stopped, now.AddMinutes(-1), now, TimeSpan.FromMinutes(1),
      0.1, 10, null, null, 6, 0);
    MethodInfo createMethod = typeof(LiveSessionCoordinator).GetMethod(
      "CreateTerminalPersistenceEffect",
      BindingFlags.Instance | BindingFlags.NonPublic)
      ?? throw new MissingMethodException(nameof(LiveSessionCoordinator), "CreateTerminalPersistenceEffect");
    var effect = (Func<CancellationToken, Task>)createMethod.Invoke(
      coordinator,
      [sessionId, new SessionStoppedEvent(now), summary])!;

    await effect(CancellationToken.None);
    await effect(CancellationToken.None);
  }

  [Fact]
  public async Task Internally_canceled_terminal_barrier_is_reported_as_retryable_not_caller_cancellation()
  {
    Task canceled = Task.FromCanceled(new CancellationToken(canceled: true));

    InvalidOperationException error = await Assert.ThrowsAsync<InvalidOperationException>(() =>
      LiveSessionCoordinator.WaitForTerminalPersistenceBarrierAsync(
        canceled,
        TimeSpan.FromSeconds(1),
        CancellationToken.None));

    Assert.Contains("interrupted", error.Message, StringComparison.Ordinal);
  }

  private class FaultInjectingSessionStore : DispatchProxy
  {
    public int FinalizeFailuresRemaining { get; set; }
    public int AppendEventCount { get; private set; }
    public int FinalizeCount { get; private set; }
    public Guid AppendedSessionId { get; private set; }
    public SessionEvent? AppendedEvent { get; private set; }
    public SessionSummary? FinalizedSummary { get; private set; }

    protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
    {
      ArgumentNullException.ThrowIfNull(targetMethod);
      if (targetMethod.Name == nameof(ISessionStore.AppendEventAsync))
      {
        AppendEventCount++;
        AppendedSessionId = (Guid)args![0]!;
        AppendedEvent = (SessionEvent)args[1]!;
        return Task.CompletedTask;
      }

      if (targetMethod.Name == nameof(ISessionStore.FinalizeAsync))
      {
        FinalizeCount++;
        if (FinalizeFailuresRemaining-- > 0)
          return Task.FromException(new IOException("transient terminal persistence failure"));

        FinalizedSummary = (SessionSummary)args![0]!;
        return Task.CompletedTask;
      }

      throw new NotSupportedException(targetMethod.Name);
    }
  }

  private class SuccessfulPolarRecordingStore : DispatchProxy
  {
    protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
    {
      ArgumentNullException.ThrowIfNull(targetMethod);
      if (targetMethod.Name == nameof(IPolarH10RecordingStore.QueueStopAsync))
        return Task.FromResult(true);
      throw new NotSupportedException(targetMethod.Name);
    }
  }

  private class MissingSessionStore : DispatchProxy
  {
    public int InterruptCount { get; private set; }
    public int SweepCount { get; private set; }

    protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
    {
      ArgumentNullException.ThrowIfNull(targetMethod);
      if (targetMethod.Name == nameof(ISessionStore.InterruptAsync))
      {
        InterruptCount++;
        return Task.FromException<bool>(new KeyNotFoundException("already deleted"));
      }
      if (targetMethod.Name == nameof(ISessionStore.InterruptUnfinishedAsync))
      {
        SweepCount++;
        return Task.FromResult(0);
      }
      throw new NotSupportedException(targetMethod.Name);
    }
  }

  private class MissingTerminalSessionStore : DispatchProxy
  {
    protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
    {
      ArgumentNullException.ThrowIfNull(targetMethod);
      if (targetMethod.Name == nameof(ISessionStore.AppendEventAsync))
        return Task.FromException(new KeyNotFoundException("intentionally deleted"));
      throw new NotSupportedException(targetMethod.Name);
    }
  }
}
