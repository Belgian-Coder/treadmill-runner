namespace TreadmillRunner.Core.Bluetooth;

public sealed record BleAdvertisement(
    string DeviceId,
    string? Name,
    short? SignalStrength,
    IReadOnlyList<Guid> ServiceUuids);

public sealed record BleService(Guid Uuid, IReadOnlyList<BleCharacteristic> Characteristics);

public sealed record BleCharacteristic(
    Guid ServiceUuid,
    Guid CharacteristicUuid,
    bool CanRead,
    bool CanWrite,
    bool CanNotify);

public sealed record BleNotification(
    Guid ServiceUuid,
    Guid CharacteristicUuid,
    ReadOnlyMemory<byte> Value,
    DateTimeOffset ObservedAt);

public interface IBleCentralTransport
{
  IAsyncEnumerable<BleAdvertisement> ScanAsync(CancellationToken cancellationToken = default);

  ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default);
}

/// <summary>
/// Multiplexes bounded active read-only BLE advertisements so concurrent callers share one
/// underlying adapter scan.
/// </summary>
public interface IBleAdvertisementBroker
{
  IAsyncEnumerable<BleAdvertisement> ScanAsync(CancellationToken cancellationToken = default);
}

public interface IBleCommandCentralTransport
{
  ValueTask<IBleCommandConnection> ConnectCommandAsync(
      string deviceId,
      CancellationToken cancellationToken = default);
}

public interface IBleConnection : IAsyncDisposable
{
  string DeviceId { get; }

  ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
      CancellationToken cancellationToken = default);

  ValueTask<ReadOnlyMemory<byte>> ReadAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      CancellationToken cancellationToken = default);

  IAsyncEnumerable<BleNotification> SubscribeAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      CancellationToken cancellationToken = default);

}

/// <summary>
/// Optional capability for peripherals that expose many or protected GATT
/// services. Callers can enumerate only the standard services they consume.
/// </summary>
public interface IBleTargetedServiceDiscoveryConnection
{
  ValueTask<IReadOnlyList<BleService>> DiscoverServicesForUuidsAsync(
    IReadOnlyCollection<Guid> serviceUuids,
    CancellationToken cancellationToken = default);
}

/// <summary>
/// A separately obtained command-capable BLE connection. Read-only discovery,
/// enrollment, and diagnostics never receive this interface.
/// </summary>
public interface IBleCommandConnection : IBleConnection
{
  ValueTask<BleNotification> ExchangeAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      ReadOnlyMemory<byte> value,
      TimeSpan responseTimeout,
      CancellationToken cancellationToken = default);
}
