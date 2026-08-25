using Microsoft.AspNetCore.SignalR;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Hubs;
using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class LatestLiveSnapshotBroadcasterTests
{
  [Fact]
  public async Task Slow_consumer_receives_first_and_latest_snapshot_without_intermediate_queue_growth()
  {
    var proxy = new RecordingClientProxy { BlockFirstSend = true };
    var broadcaster = new LatestLiveSnapshotBroadcaster(
      new RecordingHubContext(proxy),
      NullLogger.Instance,
      TimeProvider.System);
    using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    Task run = broadcaster.RunAsync(cancellation.Token);
    LiveSnapshot first = CreateLive(SessionState.Running, 1);
    LiveSnapshot skipped = CreateLive(SessionState.Running, 2);
    LiveSnapshot latest = CreateLive(SessionState.Running, 3);

    broadcaster.Publish(first, null);
    await proxy.FirstSendStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
    broadcaster.Publish(skipped, null);
    broadcaster.Publish(latest, null);
    proxy.ReleaseFirstSend.TrySetResult(true);

    await WaitForCountAsync(proxy, 2, cancellation.Token);
    broadcaster.Complete();
    await run.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(2, proxy.Messages.Count);
    Assert.Same(first, proxy.Messages[0].Payload);
    Assert.Same(latest, proxy.Messages[1].Payload);
  }

  [Fact]
  public async Task Terminal_session_snapshot_is_retained_independently_of_newer_nonterminal_state()
  {
    var proxy = new RecordingClientProxy { BlockFirstSend = true };
    var broadcaster = new LatestLiveSnapshotBroadcaster(
      new RecordingHubContext(proxy),
      NullLogger.Instance,
      TimeProvider.System);
    using var cancellation = new CancellationTokenSource(TimeSpan.FromSeconds(5));
    Task run = broadcaster.RunAsync(cancellation.Token);
    ActiveSessionSnapshot running = CreateSession(SessionState.Running, 1);
    ActiveSessionSnapshot terminal = CreateSession(SessionState.Completed, 2);

    broadcaster.Publish(null, running);
    await proxy.FirstSendStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
    broadcaster.Publish(null, terminal);
    proxy.ReleaseFirstSend.TrySetResult(true);

    await WaitForCountAsync(proxy, 2, cancellation.Token);
    broadcaster.Complete();
    await run.WaitAsync(TimeSpan.FromSeconds(2));

    Assert.Equal(2, proxy.Messages.Count);
    Assert.Same(running, proxy.Messages[0].Payload);
    Assert.Same(terminal, proxy.Messages[1].Payload);
  }

  private static async Task WaitForCountAsync(RecordingClientProxy proxy, int count, CancellationToken cancellationToken)
  {
    while (proxy.Messages.Count < count)
      await Task.Delay(10, cancellationToken);
  }

  private static LiveSnapshot CreateLive(SessionState state, long generation) =>
    new(
      DateTimeOffset.UtcNow,
      DeviceConnectionState.Ready,
      DeviceConnectionState.Disconnected,
      state,
      5,
      0,
      null,
      TimeSpan.FromSeconds(generation),
      generation * .01,
      null,
      TimeSpan.Zero,
      TreadmillConnectionGeneration: generation);

  private static ActiveSessionSnapshot CreateSession(SessionState state, long version) =>
    new(
      Guid.NewGuid(),
      Guid.NewGuid(),
      "Runner",
      Guid.NewGuid(),
      "Test workout",
      CreateLive(state, version),
      version,
      null,
      null,
      TimeSpan.FromMinutes(10),
      5,
      5,
      0,
      0,
      null,
      HeartRateSource.None,
      null,
      SessionControlAccess.Controller,
      null,
      []);

  private sealed class RecordingHubContext(RecordingClientProxy proxy) : IHubContext<LiveHub>
  {
    public IHubClients Clients { get; } = new RecordingHubClients(proxy);
    public IGroupManager Groups { get; } = new RecordingGroupManager();
  }

  private sealed class RecordingHubClients(RecordingClientProxy proxy) : IHubClients
  {
    public IClientProxy All { get; } = proxy;
    public IClientProxy Caller => proxy;
    public IClientProxy Others => proxy;
    public IClientProxy AllExcept(IReadOnlyList<string> excludedConnectionIds) => proxy;
    public IClientProxy Client(string connectionId) => proxy;
    public IClientProxy Clients(IReadOnlyList<string> connectionIds) => proxy;
    public IClientProxy Group(string groupName) => proxy;
    public IClientProxy GroupExcept(string groupName, IReadOnlyList<string> excludedConnectionIds) => proxy;
    public IClientProxy Groups(IReadOnlyList<string> groupNames) => proxy;
    public IClientProxy User(string userId) => proxy;
    public IClientProxy Users(IReadOnlyList<string> userIds) => proxy;
  }

  private sealed class RecordingGroupManager : IGroupManager
  {
    public Task AddToGroupAsync(string connectionId, string groupName, CancellationToken cancellationToken = default) => Task.CompletedTask;
    public Task RemoveFromGroupAsync(string connectionId, string groupName, CancellationToken cancellationToken = default) => Task.CompletedTask;
  }

  private sealed class RecordingClientProxy : IClientProxy
  {
    public bool BlockFirstSend { get; init; }
    public TaskCompletionSource<bool> FirstSendStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource<bool> ReleaseFirstSend { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public List<Message> Messages { get; } = [];

    public async Task SendCoreAsync(string method, object?[] args, CancellationToken cancellationToken = default)
    {
      bool first = Messages.Count == 0;
      Messages.Add(new Message(method, args.Single()));
      if (first && BlockFirstSend)
      {
        FirstSendStarted.TrySetResult(true);
        await ReleaseFirstSend.Task.WaitAsync(cancellationToken);
      }
    }
  }

  private sealed record Message(string Method, object? Payload);
}
