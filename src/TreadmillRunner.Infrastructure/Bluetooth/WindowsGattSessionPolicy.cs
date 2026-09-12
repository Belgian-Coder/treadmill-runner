using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal static class WindowsGattSessionPolicy
{
  internal const bool MaintainConnectionForActiveNotifications = true;

  internal static async Task<GattSession?> TryOpenOptionalAsync(
    Func<CancellationToken, Task<GattSession?>> openAsync,
    CancellationToken cancellationToken)
  {
    ArgumentNullException.ThrowIfNull(openAsync);
    cancellationToken.ThrowIfCancellationRequested();
    try
    {
      return await openAsync(cancellationToken).ConfigureAwait(false);
    }
    catch (Exception) when (!cancellationToken.IsCancellationRequested)
    {
      // GattSession is a best-effort Windows connection-lifetime hint. Some
      // service-hosted WinRT contexts cannot create it even though the same
      // BluetoothLEDevice can still discover, subscribe, read, and write GATT.
      return null;
    }
  }

  internal static void ApplyForActiveNotifications(GattSession? session)
  {
    if (session is null || !MaintainConnectionForActiveNotifications) return;

    if (session.CanMaintainConnection)
    {
      session.MaintainConnection = true;
    }
  }
}
