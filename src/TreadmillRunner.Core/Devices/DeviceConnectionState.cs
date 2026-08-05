namespace TreadmillRunner.Core.Devices;

public enum DeviceConnectionState
{
  Disconnected,
  Scanning,
  Connecting,
  DiscoveringServices,
  Subscribing,
  Initializing,
  Ready,
  Reconnecting,
  Faulted,
}
