using TreadmillRunner.Core.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal static class WindowsBleContractMapper
{
  public static BleAdvertisement MapAdvertisement(
    ulong bluetoothAddress,
    string? localName,
    short signalStrength,
    IEnumerable<Guid> serviceUuids) =>
    new(
      bluetoothAddress.ToString("X12", System.Globalization.CultureInfo.InvariantCulture),
      string.IsNullOrWhiteSpace(localName) ? null : localName,
      signalStrength,
      serviceUuids.Distinct().ToArray());

  public static BleCharacteristic MapCharacteristic(
    Guid serviceUuid,
    Guid characteristicUuid,
    GattCharacteristicProperties properties) =>
    new(
      serviceUuid,
      characteristicUuid,
      HasAny(properties, GattCharacteristicProperties.Read),
      HasAny(
        properties,
        GattCharacteristicProperties.Write |
        GattCharacteristicProperties.WriteWithoutResponse),
      HasAny(
        properties,
        GattCharacteristicProperties.Notify |
        GattCharacteristicProperties.Indicate));

  private static bool HasAny(
    GattCharacteristicProperties properties,
    GattCharacteristicProperties flags) => (properties & flags) != 0;
}
