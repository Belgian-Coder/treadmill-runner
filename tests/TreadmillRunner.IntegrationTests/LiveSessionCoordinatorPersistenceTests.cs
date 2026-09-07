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
  public async Task Terminal_persistence_retry_does_not_append_the_terminal_event_twice()
  {
    var store = DispatchProxy.Create<ISessionStore, FaultInjectingSessionStore>();
    var fault = (FaultInjectingSessionStore)(object)store;
    fault.FinalizeFailuresRemaining = 1;
    await using ServiceProvider services = new ServiceCollection()
      .AddSingleton(store)
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
      [batch, CancellationToken.None, false])!;
    await queued;

    Assert.Equal(1, fault.AppendEventCount);
    Assert.Equal(2, fault.FinalizeCount);
    Assert.Equal(sessionId, fault.AppendedSessionId);
    Assert.Same(terminalEvent, fault.AppendedEvent);
    Assert.Same(summary, fault.FinalizedSummary);
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
}
