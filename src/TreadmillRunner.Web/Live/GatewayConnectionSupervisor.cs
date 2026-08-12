using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Web.Live;

public enum GatewayClientConnectionPhase
{
  Starting,
  Connected,
  Reconnecting,
  Disconnected,
  UpdateRequired,
  Disposed,
}

public sealed record GatewayClientSnapshot(
  GatewayClientConnectionPhase ConnectionPhase,
  int RetryAttempt,
  ActiveSessionSnapshot? Session,
  LiveSnapshot? Live,
  DateTimeOffset? LastSnapshotReceivedAtUtc,
  Guid? ServiceInstanceId,
  string? ServerBuildFingerprint,
  ControlLease? Lease,
  bool WantsControl,
  string? ControlsBlockedReason)
{
  public bool IsConnected => ConnectionPhase == GatewayClientConnectionPhase.Connected;
  public bool HasController => Lease is not null;
  public bool IsStale => !IsConnected || ControlsBlockedReason is not null;
}

public sealed class GatewayConnectionSupervisor(
  HttpClient http,
  NavigationManager navigation,
  IJSRuntime js,
  ClientRuntimeState runtime,
  TimeProvider timeProvider) : IAsyncDisposable
{
  private const string HolderStorageKey = "treadmillrunner.controller-holder";
  private static readonly TimeSpan HeartbeatInterval = TimeSpan.FromSeconds(5);
  private readonly SemaphoreSlim lifecycleGate = new(1, 1);
  private ILiveHubClient? hubConnection;
  private CancellationTokenSource? lifetimeCancellation;
  private CancellationTokenSource? heartbeatCancellation;
  private Task? connectionTask;
  private Task? heartbeatTask;
  private bool initialized;
  private bool disposed;
  private bool forcingRestart;
  private string holderId = string.Empty;

  public GatewayClientSnapshot Current { get; private set; } = new(
    GatewayClientConnectionPhase.Starting,
    0,
    null,
    null,
    null,
    null,
    null,
    null,
    false,
    "Connecting to the gateway.");

  public string HolderId => holderId;
  public event Action? ConnectionChanged;
  public event Action? SessionChanged;
  public event Action? HeartRateChanged;
  public event Action? LeaseChanged;

  public async Task EnsureStartedAsync(CancellationToken cancellationToken = default)
  {
    await lifecycleGate.WaitAsync(cancellationToken);
    try
    {
      if (disposed) throw new ObjectDisposedException(nameof(GatewayConnectionSupervisor));
      if (initialized) return;
      initialized = true;
      holderId = await js.InvokeAsync<string?>("localStorage.getItem", cancellationToken, HolderStorageKey) ?? string.Empty;
      if (string.IsNullOrWhiteSpace(holderId))
      {
        holderId = Guid.NewGuid().ToString("N");
        await js.InvokeVoidAsync("localStorage.setItem", cancellationToken, HolderStorageKey, holderId);
      }

      hubConnection = CreateLiveHubClient(navigation.ToAbsoluteUri("/hubs/live"));
      hubConnection.Configure(
        ReceiveLiveSnapshotAsync,
        ReceiveSessionSnapshotAsync,
        () => MarkDisconnectedAsync(
          GatewayClientConnectionPhase.Reconnecting,
          "Live updates were interrupted. Last measurements may be stale; treadmill controls remain disabled."),
        () => RecoverAuthoritativeStateAsync(),
        RestartAfterCloseAsync);
      lifetimeCancellation = new CancellationTokenSource();
      connectionTask = ConnectUntilAvailableAsync(lifetimeCancellation.Token);
    }
    finally
    {
      lifecycleGate.Release();
    }
  }

  public async Task<ControlLease?> RequestControlAsync(bool restoring = false, CancellationToken cancellationToken = default)
  {
    await EnsureStartedAsync(cancellationToken);
    Update(Current with { WantsControl = true });
    if (!CanAttemptControl()) return null;

    try
    {
      using HttpResponseMessage response = await http.PostAsJsonAsync(
        "api/live/lease/acquire",
        new { holderId },
        cancellationToken);
      if (!response.IsSuccessStatusCode)
      {
        SetLease(null, "Another browser currently controls manual actions.");
        return null;
      }

      ControlLease? lease = await response.Content.ReadFromJsonAsync<ControlLease>(cancellationToken: cancellationToken);
      SetLease(lease, lease is null ? "The gateway returned no controller lease." : null);
      if (lease is not null) StartHeartbeat();
      return lease;
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
    {
      SetLease(null, "Manual controls are unavailable while the gateway reconnects.");
      await MarkDisconnectedAsync(
        GatewayClientConnectionPhase.Reconnecting,
        "The controller request was interrupted. No treadmill command was sent.");
      return null;
    }
  }

  public void ClearControlIntent()
  {
    heartbeatCancellation?.Cancel();
    Update(Current with { WantsControl = false, Lease = null });
    LeaseChanged?.Invoke();
  }

  public async Task RefreshAuthoritativeStateAsync(CancellationToken cancellationToken = default)
  {
    await EnsureStartedAsync(cancellationToken);
    await RecoverAuthoritativeStateAsync(cancellationToken);
  }

  private async Task ConnectUntilAvailableAsync(CancellationToken cancellationToken)
  {
    int attempt = 0;
    while (!cancellationToken.IsCancellationRequested && hubConnection is { State: LiveHubConnectionState.Disconnected })
    {
      SetConnectionPhase(
        attempt == 0 ? GatewayClientConnectionPhase.Starting : GatewayClientConnectionPhase.Reconnecting,
        attempt,
        attempt == 0 ? "Connecting to the gateway." : "Gateway unavailable; retrying automatically.");
      try
      {
        await hubConnection.StartAsync(cancellationToken);
        await RecoverAuthoritativeStateAsync(cancellationToken);
        return;
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        return;
      }
      catch (Exception exception) when (exception is HttpRequestException or InvalidOperationException or TimeoutException)
      {
        attempt++;
        SetConnectionPhase(
          GatewayClientConnectionPhase.Disconnected,
          attempt,
          "Gateway unavailable; retrying automatically. The treadmill may still be moving.");
        await Task.Delay(RetryDelay(attempt), timeProvider, cancellationToken);
      }
    }
  }

  private async Task RecoverAuthoritativeStateAsync(CancellationToken cancellationToken = default)
  {
    await lifecycleGate.WaitAsync(cancellationToken);
    try
    {
      if (disposed || hubConnection?.State != LiveHubConnectionState.Connected)
      {
        SetConnectionPhase(
          GatewayClientConnectionPhase.Reconnecting,
          Current.RetryAttempt,
          "Live updates are not connected; controls remain disabled.");
        return;
      }

      heartbeatCancellation?.Cancel();
      SetLease(null, "Verifying authoritative gateway state.", notify: false);
      await runtime.CheckAsync(http, cancellationToken);
      if (runtime.UpdateRequired)
      {
        SetConnectionPhase(
          GatewayClientConnectionPhase.UpdateRequired,
          Current.RetryAttempt,
          "The browser version is stale. Reload before using treadmill controls.");
        return;
      }

      using HttpResponseMessage sessionResponse = await http.GetAsync("api/live/session", cancellationToken);
      ActiveSessionSnapshot? session = sessionResponse.StatusCode == HttpStatusCode.NoContent
        ? null
        : await sessionResponse.Content.ReadFromJsonAsync<ActiveSessionSnapshot>(cancellationToken: cancellationToken);
      using HttpResponseMessage liveResponse = await http.GetAsync("api/live/snapshot", cancellationToken);
      liveResponse.EnsureSuccessStatusCode();
      LiveSnapshot? live = await liveResponse.Content.ReadFromJsonAsync<LiveSnapshot>(cancellationToken: cancellationToken);

      Guid? previousService = Current.ServiceInstanceId;
      Guid? service = session?.ServiceInstanceId;
      bool serviceChanged = previousService is not null && service is not null && previousService != service;
      GatewayClientSnapshot previous = Current;
      Update(Current with
      {
        ConnectionPhase = GatewayClientConnectionPhase.Connected,
        RetryAttempt = 0,
        Session = session,
        Live = live ?? session?.Live ?? Current.Live,
        LastSnapshotReceivedAtUtc = timeProvider.GetUtcNow(),
        ServiceInstanceId = service,
        ServerBuildFingerprint = runtime.ServerFingerprint,
        Lease = null,
        ControlsBlockedReason = serviceChanged
          ? "The gateway restarted. Authoritative session state was reloaded before controls can return."
          : null,
      });
      NotifySnapshotChanges(previous, Current, sessionChanged: true);

      if (Current.WantsControl && !runtime.UpdateRequired && IsNonTerminal(session))
        await RequestControlWithoutGateAsync(restoring: true, cancellationToken);
      else
        LeaseChanged?.Invoke();
      ConnectionChanged?.Invoke();
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // The active supervisor lifetime or caller owns cancellation.
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException or System.Text.Json.JsonException)
    {
      await MarkDisconnectedAsync(
        GatewayClientConnectionPhase.Reconnecting,
        "The gateway returned before authoritative state could be verified. Controls remain disabled.");
    }
    finally
    {
      lifecycleGate.Release();
    }
  }

  private async Task<ControlLease?> RequestControlWithoutGateAsync(bool restoring, CancellationToken cancellationToken)
  {
    if (!CanAttemptControl()) return null;
    try
    {
      using HttpResponseMessage response = await http.PostAsJsonAsync(
        "api/live/lease/acquire",
        new { holderId },
        cancellationToken);
      ControlLease? lease = response.IsSuccessStatusCode
        ? await response.Content.ReadFromJsonAsync<ControlLease>(cancellationToken: cancellationToken)
        : null;
      SetLease(lease, lease is null
        ? "Another browser currently controls manual actions."
        : null);
      if (lease is not null) StartHeartbeat();
      return lease;
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
    {
      SetLease(null, restoring
        ? "Controller access could not be restored; live measurements remain available."
        : "Controller access is unavailable.");
      return null;
    }
  }

  private Task ReceiveLiveSnapshotAsync(LiveSnapshot incoming)
  {
    GatewayClientSnapshot previous = Current;
    Update(Current with
    {
      Live = incoming,
      LastSnapshotReceivedAtUtc = timeProvider.GetUtcNow(),
    });
    if (!Equals(HeartRateTuple(previous.Live), HeartRateTuple(incoming))) HeartRateChanged?.Invoke();
    return Task.CompletedTask;
  }

  private Task ReceiveSessionSnapshotAsync(ActiveSessionSnapshot incoming)
  {
    GatewayClientSnapshot previous = Current;
    Update(Current with
    {
      Session = incoming,
      Live = incoming.Live,
      LastSnapshotReceivedAtUtc = timeProvider.GetUtcNow(),
      ServiceInstanceId = incoming.ServiceInstanceId,
    });
    NotifySnapshotChanges(previous, Current, sessionChanged: true);
    return Task.CompletedTask;
  }

  private async Task MarkDisconnectedAsync(GatewayClientConnectionPhase phase, string reason)
  {
    heartbeatCancellation?.Cancel();
    bool leaseChanged = Current.Lease is not null;
    Update(Current with
    {
      ConnectionPhase = phase,
      Lease = null,
      ControlsBlockedReason = reason,
    });
    runtime.SetConnected(false);
    if (leaseChanged) LeaseChanged?.Invoke();
    ConnectionChanged?.Invoke();
    await Task.CompletedTask;
  }

  private async Task RestartAfterCloseAsync()
  {
    await MarkDisconnectedAsync(
      GatewayClientConnectionPhase.Disconnected,
      "Live updates stopped. Retrying automatically; the treadmill may still be moving.");
    if (forcingRestart || lifetimeCancellation?.IsCancellationRequested != false || connectionTask is { IsCompleted: false }) return;
    connectionTask = ConnectUntilAvailableAsync(lifetimeCancellation.Token);
  }

  private void StartHeartbeat()
  {
    heartbeatCancellation?.Cancel();
    heartbeatCancellation?.Dispose();
    heartbeatCancellation = CancellationTokenSource.CreateLinkedTokenSource(lifetimeCancellation?.Token ?? CancellationToken.None);
    heartbeatTask = RenewLeaseAsync(heartbeatCancellation.Token);
  }

  private async Task RenewLeaseAsync(CancellationToken cancellationToken)
  {
    using var timer = new PeriodicTimer(HeartbeatInterval, timeProvider);
    try
    {
      while (await timer.WaitForNextTickAsync(cancellationToken))
      {
        ControlLease? lease = Current.Lease;
        if (lease is null) return;
        using HttpResponseMessage response = await http.PostAsJsonAsync(
          "api/live/lease/heartbeat",
          lease,
          cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
          SetLease(null, "Controller lease expired. Controls remain disabled until ownership is restored.");
          return;
        }

        ControlLease? renewed = await response.Content.ReadFromJsonAsync<ControlLease>(cancellationToken: cancellationToken);
        SetLease(renewed, renewed is null ? "The gateway returned no renewed controller lease." : null);
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Reconnect, replacement heartbeat, or disposal owns cancellation.
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
    {
      SetLease(null, "The lease heartbeat was interrupted. No treadmill command was sent.");
      await ForceReconnectAsync(cancellationToken);
    }
  }

  private async Task ForceReconnectAsync(CancellationToken cancellationToken)
  {
    await MarkDisconnectedAsync(
      GatewayClientConnectionPhase.Reconnecting,
      "The controller heartbeat failed. Reconnecting and reloading authoritative state.");
    if (hubConnection is null || lifetimeCancellation?.IsCancellationRequested != false) return;
    forcingRestart = true;
    try
    {
      if (hubConnection.State != LiveHubConnectionState.Disconnected)
        await hubConnection.StopAsync(cancellationToken);
    }
    catch (Exception exception) when (exception is HttpRequestException or InvalidOperationException)
    {
      // The disconnected state below is sufficient; the initial loop remains authoritative.
    }
    finally
    {
      forcingRestart = false;
    }
    connectionTask = ConnectUntilAvailableAsync(lifetimeCancellation.Token);
  }

  private bool CanAttemptControl() =>
    !disposed &&
    hubConnection?.State == LiveHubConnectionState.Connected &&
    Current.ConnectionPhase == GatewayClientConnectionPhase.Connected &&
    runtime.IsConnected &&
    !runtime.UpdateRequired;

  private void SetLease(ControlLease? lease, string? blockedReason, bool notify = true)
  {
    bool changed = !Equals(Current.Lease, lease) || !string.Equals(Current.ControlsBlockedReason, blockedReason, StringComparison.Ordinal);
    Update(Current with { Lease = lease, ControlsBlockedReason = blockedReason });
    if (notify && changed) LeaseChanged?.Invoke();
  }

  private void SetConnectionPhase(GatewayClientConnectionPhase phase, int attempt, string? blockedReason)
  {
    Update(Current with
    {
      ConnectionPhase = phase,
      RetryAttempt = attempt,
      Lease = phase == GatewayClientConnectionPhase.Connected ? Current.Lease : null,
      ControlsBlockedReason = blockedReason,
    });
    ConnectionChanged?.Invoke();
  }

  private void Update(GatewayClientSnapshot snapshot) => Current = snapshot;

  private void NotifySnapshotChanges(GatewayClientSnapshot previous, GatewayClientSnapshot current, bool sessionChanged)
  {
    if (sessionChanged && !Equals(previous.Session, current.Session)) SessionChanged?.Invoke();
    if (!Equals(HeartRateTuple(previous.Live), HeartRateTuple(current.Live))) HeartRateChanged?.Invoke();
  }

  private static bool IsNonTerminal(ActiveSessionSnapshot? session) => session?.Live.SessionState is
    SessionState.ArmedWaitingForPhysicalStart or SessionState.Running or SessionState.PausedWaitingForPhysicalResume;

  private static object? HeartRateTuple(LiveSnapshot? snapshot) => snapshot is null
    ? null
    : (snapshot.HeartRateConnectionState,
      snapshot.HeartRateBpm,
      snapshot.HeartRateTelemetryAge,
      snapshot.HeartRateDeviceName,
      snapshot.HeartRateDeviceKind,
      snapshot.HeartRateBatteryPercent);

  private static TimeSpan RetryDelay(int attempt)
  {
    int bounded = Math.Min(attempt, 4);
    double seconds = bounded switch { 0 => 0, 1 => 1, 2 => 2, 3 => 5, _ => 10 };
    return TimeSpan.FromSeconds(seconds) + TimeSpan.FromMilliseconds((attempt % 5) * 137);
  }

  private static ILiveHubClient CreateLiveHubClient(Uri hubUrl)
  {
    const string typeName =
      "TreadmillRunner.Web.SignalR.SignalRLiveHubClient, TreadmillRunner.Web.SignalR";
    Type type = Type.GetType(typeName, throwOnError: true)
      ?? throw new InvalidOperationException("The live SignalR transport is unavailable.");
    return Activator.CreateInstance(type, hubUrl.AbsoluteUri) as ILiveHubClient
      ?? throw new InvalidOperationException("The live SignalR transport could not be created.");
  }

  public async ValueTask DisposeAsync()
  {
    if (disposed) return;
    disposed = true;
    Update(Current with
    {
      ConnectionPhase = GatewayClientConnectionPhase.Disposed,
      Lease = null,
      ControlsBlockedReason = "The browser connection supervisor was disposed.",
    });
    heartbeatCancellation?.Cancel();
    lifetimeCancellation?.Cancel();
    if (heartbeatTask is not null)
    {
      try { await heartbeatTask; } catch (OperationCanceledException) { }
    }
    if (connectionTask is not null)
    {
      try { await connectionTask; } catch (OperationCanceledException) { }
    }
    heartbeatCancellation?.Dispose();
    lifetimeCancellation?.Dispose();
    if (hubConnection is not null) await hubConnection.DisposeAsync();
    lifecycleGate.Dispose();
  }
}
