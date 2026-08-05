using Microsoft.AspNetCore.SignalR;

namespace TreadmillRunner.Gateway.Hubs;

/// <summary>Broadcast-only live telemetry hub; it intentionally exposes no treadmill-control methods.</summary>
public sealed class LiveHub : Hub;
