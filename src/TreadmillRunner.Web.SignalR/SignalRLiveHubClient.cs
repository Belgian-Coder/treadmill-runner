using Microsoft.AspNetCore.SignalR.Client;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Web.SignalR;

public sealed class SignalRLiveHubClient : ILiveHubClient
{
  private readonly HubConnection connection;

  public SignalRLiveHubClient(string hubUrl)
  {
    connection = new HubConnectionBuilder()
      .WithUrl(hubUrl)
      .WithAutomaticReconnect(new IndefiniteHubRetryPolicy())
      .Build();
  }

  public LiveHubConnectionState State => connection.State == HubConnectionState.Connected
    ? LiveHubConnectionState.Connected
    : LiveHubConnectionState.Disconnected;

  public void Configure(
    Func<LiveSnapshot, Task> receiveLive,
    Func<ActiveSessionSnapshot, Task> receiveSession,
    Func<Task> reconnecting,
    Func<Task> reconnected,
    Func<Task> closed)
  {
    connection.On<LiveSnapshot>("snapshot", receiveLive);
    connection.On<ActiveSessionSnapshot>("sessionSnapshot", receiveSession);
    connection.Reconnecting += _ => reconnecting();
    connection.Reconnected += _ => reconnected();
    connection.Closed += _ => closed();
  }

  public Task StartAsync(CancellationToken cancellationToken) => connection.StartAsync(cancellationToken);
  public Task StopAsync(CancellationToken cancellationToken) => connection.StopAsync(cancellationToken);
  public ValueTask DisposeAsync() => connection.DisposeAsync();

}
