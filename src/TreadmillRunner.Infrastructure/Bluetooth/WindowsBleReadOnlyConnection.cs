using System.Globalization;
using System.Runtime.CompilerServices;
using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal sealed class WindowsBleReadOnlyConnection : IBleConnection
{
  private readonly ulong _bluetoothAddress;
  private readonly CancellationTokenSource _disposeCancellation = new();
  private int _disposed;

  public WindowsBleReadOnlyConnection(string deviceId)
  {
    if (deviceId is null ||
        deviceId.Length != 12 ||
        !ulong.TryParse(
          deviceId,
          NumberStyles.AllowHexSpecifier,
          CultureInfo.InvariantCulture,
          out _bluetoothAddress))
    {
      throw new ArgumentException(
        "A Windows BLE device ID must contain exactly 12 hexadecimal digits.",
        nameof(deviceId));
    }

    DeviceId = deviceId.ToUpperInvariant();
  }

  public string DeviceId { get; }

  public async ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    cancellationToken.ThrowIfCancellationRequested();
    using var linkedCancellation = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    var operationCancellation = linkedCancellation.Token;

    using BluetoothLEDevice device = await OpenDeviceAsync(operationCancellation);
    var servicesResult = await device
      .GetGattServicesAsync(BluetoothCacheMode.Uncached)
      .AsTask(operationCancellation)
      .ConfigureAwait(false);
    WindowsBleStatus.ThrowIfFailed(
      servicesResult.Status,
      servicesResult.ProtocolError,
      "service discovery");

    var nativeServices = servicesResult.Services;
    try
    {
      var services = new List<BleService>(nativeServices.Count);
      foreach (GattDeviceService nativeService in nativeServices)
      {
        operationCancellation.ThrowIfCancellationRequested();
        var characteristicsResult = await nativeService
          .GetCharacteristicsAsync(BluetoothCacheMode.Uncached)
          .AsTask(operationCancellation)
          .ConfigureAwait(false);
        WindowsBleStatus.ThrowIfFailed(
          characteristicsResult.Status,
          characteristicsResult.ProtocolError,
          $"characteristic discovery for service {nativeService.Uuid:D}");

        BleCharacteristic[] characteristics = characteristicsResult.Characteristics
          .Select(characteristic => WindowsBleContractMapper.MapCharacteristic(
            nativeService.Uuid,
            characteristic.Uuid,
            characteristic.CharacteristicProperties))
          .ToArray();
        services.Add(new BleService(nativeService.Uuid, characteristics));
      }

      ThrowIfDisposed();
      return services;
    }
    finally
    {
      foreach (GattDeviceService nativeService in nativeServices)
      {
        nativeService.Dispose();
      }
    }
  }

  public async ValueTask<ReadOnlyMemory<byte>> ReadAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    cancellationToken.ThrowIfCancellationRequested();
    using var linkedCancellation = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    using NativeCharacteristicHandle handle = await OpenCharacteristicAsync(
      serviceUuid,
      characteristicUuid,
      linkedCancellation.Token);

    if (!handle.Characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Read))
    {
      throw new WindowsBleException(
        $"BLE characteristic {characteristicUuid:D} is not readable.");
    }

    GattReadResult result = await handle.Characteristic
      .ReadValueAsync(BluetoothCacheMode.Uncached)
      .AsTask(linkedCancellation.Token)
      .ConfigureAwait(false);
    WindowsBleStatus.ThrowIfFailed(
      result.Status,
      result.ProtocolError,
      $"read characteristic {characteristicUuid:D}");
    return ReadBuffer(result.Value);
  }

  public async IAsyncEnumerable<BleNotification> SubscribeAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    [EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    cancellationToken.ThrowIfCancellationRequested();
    using var linkedCancellation = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    CancellationToken operationCancellation = linkedCancellation.Token;
    using NativeCharacteristicHandle handle = await OpenCharacteristicAsync(
      serviceUuid,
      characteristicUuid,
      operationCancellation);

    GattCharacteristicProperties properties = handle.Characteristic.CharacteristicProperties;
    GattClientCharacteristicConfigurationDescriptorValue mode =
      properties.HasFlag(GattCharacteristicProperties.Notify)
        ? GattClientCharacteristicConfigurationDescriptorValue.Notify
        : properties.HasFlag(GattCharacteristicProperties.Indicate)
          ? GattClientCharacteristicConfigurationDescriptorValue.Indicate
          : throw new WindowsBleException(
            $"BLE characteristic {characteristicUuid:D} does not support notifications or indications.");

    var channel = Channel.CreateBounded<BleNotification>(new BoundedChannelOptions(64)
    {
      FullMode = BoundedChannelFullMode.DropOldest,
      SingleReader = true,
      SingleWriter = false,
    });

    TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> handler = (_, args) =>
    {
      try
      {
        channel.Writer.TryWrite(new BleNotification(
          serviceUuid,
          characteristicUuid,
          ReadBuffer(args.CharacteristicValue),
          DateTimeOffset.UtcNow));
      }
      catch (Exception exception)
      {
        channel.Writer.TryComplete(exception);
      }
    };

    TypedEventHandler<BluetoothLEDevice, object> connectionHandler = (device, _) =>
    {
      if (device.ConnectionStatus == BluetoothConnectionStatus.Disconnected)
      {
        channel.Writer.TryComplete(new WindowsBleDisconnectedException());
      }
    };

    handle.Characteristic.ValueChanged += handler;
    handle.Device.ConnectionStatusChanged += connectionHandler;
    try
    {
      GattCommunicationStatus status = await handle.Characteristic
        .WriteClientCharacteristicConfigurationDescriptorAsync(mode)
        .AsTask(operationCancellation)
        .ConfigureAwait(false);
      WindowsBleStatus.ThrowIfFailed(status, null, $"subscribe characteristic {characteristicUuid:D}");
      if (handle.Device.ConnectionStatus == BluetoothConnectionStatus.Disconnected)
      {
        throw new WindowsBleDisconnectedException();
      }

      await foreach (BleNotification notification in channel.Reader
        .ReadAllAsync(operationCancellation)
        .ConfigureAwait(false))
      {
        yield return notification;
      }
    }
    finally
    {
      handle.Characteristic.ValueChanged -= handler;
      handle.Device.ConnectionStatusChanged -= connectionHandler;
      channel.Writer.TryComplete();
      try
      {
        await handle.Characteristic
          .WriteClientCharacteristicConfigurationDescriptorAsync(
            GattClientCharacteristicConfigurationDescriptorValue.None);
      }
      catch
      {
        // Connection disposal remains the final subscription cleanup boundary.
      }
    }
  }

  public ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) == 0)
    {
      _disposeCancellation.Cancel();
      _disposeCancellation.Dispose();
    }

    return ValueTask.CompletedTask;
  }

  private async Task<BluetoothLEDevice> OpenDeviceAsync(CancellationToken cancellationToken)
  {
    BluetoothLEDevice? device = await BluetoothLEDevice
      .FromBluetoothAddressAsync(_bluetoothAddress)
      .AsTask(cancellationToken)
      .ConfigureAwait(false);
    cancellationToken.ThrowIfCancellationRequested();

    return device ?? throw new WindowsBleDeviceUnavailableException();
  }

  private async Task<NativeCharacteristicHandle> OpenCharacteristicAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken)
  {
    BluetoothLEDevice device = await OpenDeviceAsync(cancellationToken);
    try
    {
      GattDeviceServicesResult servicesResult = await device
        .GetGattServicesForUuidAsync(serviceUuid, BluetoothCacheMode.Uncached)
        .AsTask(cancellationToken)
        .ConfigureAwait(false);
      WindowsBleStatus.ThrowIfFailed(
        servicesResult.Status,
        servicesResult.ProtocolError,
        $"discover service {serviceUuid:D}");

      GattDeviceService? service = servicesResult.Services.FirstOrDefault();
      foreach (GattDeviceService extra in servicesResult.Services.Skip(1)) extra.Dispose();
      if (service is null)
      {
        throw new WindowsBleException($"BLE service {serviceUuid:D} was not found.");
      }

      try
      {
        GattOpenStatus openStatus = await service
          .OpenAsync(GattSharingMode.SharedReadAndWrite)
          .AsTask(cancellationToken)
          .ConfigureAwait(false);
        if (openStatus is not (GattOpenStatus.Success or GattOpenStatus.AlreadyOpened))
        {
          throw new WindowsBleException(
            $"Windows BLE could not open service {serviceUuid:D} for shared telemetry access: {openStatus}.");
        }

        // Keep telemetry and the serialized command connection on compatible
        // shared service handles. This also avoids starting characteristic
        // discovery in the same WinRT completion turn as service discovery.
        await Task.Yield();
        GattCharacteristicsResult characteristicsResult = await service
          .GetCharacteristicsForUuidAsync(characteristicUuid, BluetoothCacheMode.Uncached)
          .AsTask(cancellationToken)
          .ConfigureAwait(false);
        WindowsBleStatus.ThrowIfFailed(
          characteristicsResult.Status,
          characteristicsResult.ProtocolError,
          $"discover characteristic {characteristicUuid:D}");
        GattCharacteristic? characteristic = characteristicsResult.Characteristics.FirstOrDefault();
        if (characteristic is null)
        {
          throw new WindowsBleException(
            $"BLE characteristic {characteristicUuid:D} was not found in service {serviceUuid:D}.");
        }

        return new NativeCharacteristicHandle(device, service, characteristic);
      }
      catch
      {
        service.Dispose();
        throw;
      }
    }
    catch
    {
      device.Dispose();
      throw;
    }
  }

  private static byte[] ReadBuffer(IBuffer buffer)
  {
    using DataReader reader = DataReader.FromBuffer(buffer);
    var value = new byte[reader.UnconsumedBufferLength];
    reader.ReadBytes(value);
    return value;
  }

  private void ThrowIfDisposed() =>
    ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);

  private sealed class NativeCharacteristicHandle(
    BluetoothLEDevice device,
    GattDeviceService service,
    GattCharacteristic characteristic) : IDisposable
  {
    public GattCharacteristic Characteristic { get; } = characteristic;
    public BluetoothLEDevice Device { get; } = device;

    public void Dispose()
    {
      service.Dispose();
      Device.Dispose();
    }
  }
}
