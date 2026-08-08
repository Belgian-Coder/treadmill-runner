using System.Globalization;
using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;
using TreadmillRunner.Protocols.Ftms;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal sealed class WindowsBleCommandConnection : IBleCommandConnection
{
  private static readonly Guid FitnessMachineService =
    Guid.Parse("00001826-0000-1000-8000-00805f9b34fb");
  private static readonly Guid FitnessMachineControlPoint =
    Guid.Parse("00002ad9-0000-1000-8000-00805f9b34fb");

  private readonly WindowsBleReadOnlyConnection _readOnly;
  private readonly ulong _bluetoothAddress;
  private readonly CancellationTokenSource _disposeCancellation = new();
  private readonly SemaphoreSlim _exchangeGate = new(1, 1);
  private BluetoothLEDevice? _device;
  private GattDeviceService? _service;
  private GattCharacteristic? _characteristic;
  private int _disposed;

  public WindowsBleCommandConnection(string deviceId)
  {
    if (deviceId is null ||
        deviceId.Length != 12 ||
        !ulong.TryParse(deviceId, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out _bluetoothAddress))
    {
      throw new ArgumentException(
        "A Windows BLE device ID must contain exactly 12 hexadecimal digits.",
        nameof(deviceId));
    }

    DeviceId = deviceId.ToUpperInvariant();
    _readOnly = new WindowsBleReadOnlyConnection(DeviceId);
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
    CancellationToken operationCancellation = linkedCancellation.Token;

    await _exchangeGate.WaitAsync(operationCancellation).ConfigureAwait(false);
    try
    {
      GattCharacteristic characteristic = await GetCharacteristicAsync(
        FitnessMachineService,
        FitnessMachineControlPoint,
        operationCancellation);
      BleCharacteristic mapped = WindowsBleContractMapper.MapCharacteristic(
        FitnessMachineService,
        FitnessMachineControlPoint,
        characteristic.CharacteristicProperties);
      return [new BleService(FitnessMachineService, [mapped])];
    }
    finally
    {
      _exchangeGate.Release();
    }
  }

  public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken = default) =>
    _readOnly.ReadAsync(serviceUuid, characteristicUuid, cancellationToken);

  public IAsyncEnumerable<BleNotification> SubscribeAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken = default) =>
    _readOnly.SubscribeAsync(serviceUuid, characteristicUuid, cancellationToken);

  public async ValueTask<BleNotification> ExchangeAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    ReadOnlyMemory<byte> value,
    TimeSpan responseTimeout,
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    if (responseTimeout <= TimeSpan.Zero || responseTimeout > TimeSpan.FromSeconds(10))
    {
      throw new ArgumentOutOfRangeException(nameof(responseTimeout));
    }

    using var linkedCancellation = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    linkedCancellation.CancelAfter(responseTimeout);
    CancellationToken operationCancellation = linkedCancellation.Token;
    await _exchangeGate.WaitAsync(operationCancellation).ConfigureAwait(false);
    try
    {
      GattCharacteristic characteristic = await GetCharacteristicAsync(
        serviceUuid,
        characteristicUuid,
        operationCancellation);
      GattCharacteristicProperties properties = characteristic.CharacteristicProperties;
      if (!properties.HasFlag(GattCharacteristicProperties.Write) &&
          !properties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse))
      {
        throw new WindowsBleException($"BLE characteristic {characteristicUuid:D} is not writable.");
      }

      GattClientCharacteristicConfigurationDescriptorValue subscriptionMode =
        properties.HasFlag(GattCharacteristicProperties.Indicate)
          ? GattClientCharacteristicConfigurationDescriptorValue.Indicate
          : properties.HasFlag(GattCharacteristicProperties.Notify)
            ? GattClientCharacteristicConfigurationDescriptorValue.Notify
            : throw new WindowsBleException(
              $"BLE characteristic {characteristicUuid:D} does not provide command responses.");

      var responses = Channel.CreateBounded<BleNotification>(new BoundedChannelOptions(4)
      {
        FullMode = BoundedChannelFullMode.DropOldest,
        SingleReader = true,
        SingleWriter = false,
      });
      TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> handler = (_, args) =>
      {
        try
        {
          byte[] responseValue = ReadBuffer(args.CharacteristicValue);
          if (IsResponseForRequest(value.Span, responseValue))
          {
            responses.Writer.TryWrite(new BleNotification(
              serviceUuid,
              characteristicUuid,
              responseValue,
              DateTimeOffset.UtcNow));
          }
        }
        catch (Exception exception)
        {
          responses.Writer.TryComplete(exception);
        }
      };

      characteristic.ValueChanged += handler;
      try
      {
        GattCommunicationStatus subscribeStatus = await characteristic
          .WriteClientCharacteristicConfigurationDescriptorAsync(subscriptionMode)
          .AsTask(operationCancellation)
          .ConfigureAwait(false);
        WindowsBleStatus.ThrowIfFailed(
          subscribeStatus,
          null,
          $"subscribe command response {characteristicUuid:D}");

        using DataWriter writer = new();
        writer.WriteBytes(value.ToArray());
        GattWriteResult writeResult = await characteristic
          .WriteValueWithResultAsync(writer.DetachBuffer(), GattWriteOption.WriteWithResponse)
          .AsTask(operationCancellation)
          .ConfigureAwait(false);
        WindowsBleStatus.ThrowIfFailed(
          writeResult.Status,
          writeResult.ProtocolError,
          $"write command characteristic {characteristicUuid:D}");

        // The command connection deliberately remains open between Request
        // Control and the one motion operation so FTMS control ownership is
        // not lost between exchanges.
        try
        {
          return await responses.Reader.ReadAsync(operationCancellation).ConfigureAwait(false);
        }
        catch (OperationCanceledException exception) when (
          !cancellationToken.IsCancellationRequested &&
          !_disposeCancellation.IsCancellationRequested)
        {
          throw new WindowsBleResponseTimeoutException(
            serviceUuid,
            characteristicUuid,
            exception);
        }
      }
      finally
      {
        characteristic.ValueChanged -= handler;
        responses.Writer.TryComplete();
      }
    }
    finally
    {
      _exchangeGate.Release();
    }
  }

  public async ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) == 0)
    {
      _disposeCancellation.Cancel();
      await _readOnly.DisposeAsync();
      await _exchangeGate.WaitAsync();
      try
      {
        if (_characteristic is not null)
        {
          try
          {
            await _characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(
              GattClientCharacteristicConfigurationDescriptorValue.None);
          }
          catch
          {
            // Native disposal remains the final cleanup boundary.
          }
        }
        _service?.Dispose();
        _device?.Dispose();
      }
      finally
      {
        _exchangeGate.Release();
        _exchangeGate.Dispose();
      }
      _disposeCancellation.Dispose();
    }
  }

  private async Task<GattCharacteristic> GetCharacteristicAsync(
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken)
  {
    if (serviceUuid != FitnessMachineService ||
        characteristicUuid != FitnessMachineControlPoint)
    {
      throw new WindowsBleException(
        "The command connection only exposes the FTMS Fitness Machine Control Point.");
    }

    if (_characteristic is not null) return _characteristic;
    _device = await OpenDeviceAsync(cancellationToken);
    try
    {
      _service = await OpenServiceAsync(_device, serviceUuid, cancellationToken);
      _characteristic = await OpenCharacteristicAsync(
        _service,
        characteristicUuid,
        cancellationToken);
      return _characteristic;
    }
    catch
    {
      _service?.Dispose();
      _service = null;
      _device.Dispose();
      _device = null;
      throw;
    }
  }

  private async Task<BluetoothLEDevice> OpenDeviceAsync(CancellationToken cancellationToken)
  {
    BluetoothLEDevice? device = await BluetoothLEDevice
      .FromBluetoothAddressAsync(_bluetoothAddress)
      .AsTask(cancellationToken)
      .ConfigureAwait(false);
    return device ?? throw new WindowsBleException(
      $"Windows could not open BLE device {DeviceId} for command access.");
  }

  private static async Task<GattDeviceService> OpenServiceAsync(
    BluetoothLEDevice device,
    Guid serviceUuid,
    CancellationToken cancellationToken)
  {
    GattDeviceServicesResult result = await device
      .GetGattServicesForUuidAsync(serviceUuid, BluetoothCacheMode.Uncached)
      .AsTask(cancellationToken)
      .ConfigureAwait(false);
    WindowsBleStatus.ThrowIfFailed(result.Status, result.ProtocolError, $"discover service {serviceUuid:D}");
    GattDeviceService? service = result.Services.FirstOrDefault();
    foreach (GattDeviceService extra in result.Services.Skip(1)) extra.Dispose();
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
          $"Windows BLE could not open service {serviceUuid:D} for shared command access: {openStatus}.");
      }

      // WinRT can report AccessDenied when characteristic discovery starts in
      // the same completion turn as service discovery. Yield once and keep the
      // same shared service handle for discovery and both command exchanges.
      await Task.Yield();
      return service;
    }
    catch
    {
      service.Dispose();
      throw;
    }
  }

  private static async Task<GattCharacteristic> OpenCharacteristicAsync(
    GattDeviceService service,
    Guid characteristicUuid,
    CancellationToken cancellationToken)
  {
    GattCharacteristicsResult result = await service
      .GetCharacteristicsForUuidAsync(characteristicUuid, BluetoothCacheMode.Uncached)
      .AsTask(cancellationToken)
      .ConfigureAwait(false);
    WindowsBleStatus.ThrowIfFailed(
      result.Status,
      result.ProtocolError,
      $"discover characteristic {characteristicUuid:D}");
    return result.Characteristics.FirstOrDefault()
      ?? throw new WindowsBleException($"BLE characteristic {characteristicUuid:D} was not found.");
  }

  private static byte[] ReadBuffer(IBuffer buffer)
  {
    using DataReader reader = DataReader.FromBuffer(buffer);
    var value = new byte[reader.UnconsumedBufferLength];
    reader.ReadBytes(value);
    return value;
  }

  internal static bool IsResponseForRequest(
    ReadOnlySpan<byte> request,
    ReadOnlySpan<byte> response) =>
    request.Length > 0 &&
    FtmsControlPointCodec.TryParseResponse(response, out FtmsControlPointResponse parsed) &&
    (byte)parsed.RequestOpCode == request[0];

  private void ThrowIfDisposed() =>
    ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
}
