using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.Infrastructure.Bluetooth;

/// <summary>
/// Tracks the response CCCD mode configured for one retained command
/// connection. A new connection starts unconfigured and must configure before
/// its first exchange; repeated exchanges reuse the existing mode.
/// </summary>
internal sealed class CccdConfigurationCache
{
  public GattClientCharacteristicConfigurationDescriptorValue? ConfiguredMode { get; private set; }

  public bool NeedsConfiguration(GattClientCharacteristicConfigurationDescriptorValue mode) =>
    ConfiguredMode != mode;

  public void MarkConfigured(GattClientCharacteristicConfigurationDescriptorValue mode) =>
    ConfiguredMode = mode;

  public void Reset() => ConfiguredMode = null;
}
