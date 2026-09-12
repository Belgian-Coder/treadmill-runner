using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal static class WindowsGattSessionPolicy
{
  internal const bool MaintainConnectionForActiveNotifications = true;

  internal static void ApplyForActiveNotifications(GattSession? session)
  {
    if (session is null || !MaintainConnectionForActiveNotifications) return;

    if (session.CanMaintainConnection)
    {
      session.MaintainConnection = true;
    }
  }
}
