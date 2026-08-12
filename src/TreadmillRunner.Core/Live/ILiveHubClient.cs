using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Live;

public enum LiveHubConnectionState
{
  Disconnected,
  Connected,
}

public interface ILiveHubClient : IAsyncDisposable
{
  LiveHubConnectionState State { get; }
  void Configure(
    Func<LiveSnapshot, Task> receiveLive,
    Func<ActiveSessionSnapshot, Task> receiveSession,
    Func<Task> reconnecting,
    Func<Task> reconnected,
    Func<Task> closed);
  Task StartAsync(CancellationToken cancellationToken);
  Task StopAsync(CancellationToken cancellationToken);
}
