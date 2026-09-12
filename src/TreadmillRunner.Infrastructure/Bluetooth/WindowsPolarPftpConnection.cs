using System.Globalization;
using System.Threading.Channels;
using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace TreadmillRunner.Infrastructure.Bluetooth;

/// <summary>Windows transport for Polar PS-FTP. It opens existing GATT access and never initiates pairing.</summary>
public sealed class WindowsPolarPftpConnection : IPolarPftpConnection
{
  private readonly ulong _bluetoothAddress;
  private readonly BluetoothAddressType? _addressType;
  private readonly SemaphoreSlim _exchangeGate = new(1, 1);
  private readonly CancellationTokenSource _disposeCancellation = new();
  private BluetoothLEDevice? _device;
  private GattSession? _session;
  private GattDeviceService? _service;
  private GattCharacteristic? _mtu;
  private GattCharacteristic? _d2h;
  private GattCharacteristic? _h2d;
  private bool _notificationsEnabled;
  private int _disposed;

  public WindowsPolarPftpConnection(string deviceId, BluetoothAddressType? addressType = null)
  {
    if (deviceId is null || deviceId.Length != 12 ||
        !ulong.TryParse(deviceId, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out _bluetoothAddress))
      throw new ArgumentException("A Windows BLE device ID must contain exactly 12 hexadecimal digits.", nameof(deviceId));
    DeviceId = deviceId.ToUpperInvariant();
    _addressType = addressType;
  }

  public string DeviceId { get; }
  internal BluetoothAddressType? AddressType => _addressType;

  public async ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
    ReadOnlyMemory<byte> request,
    TimeSpan responseTimeout,
    int maximumResponseBytes,
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    if (request.IsEmpty) throw new ArgumentException("A Polar PFTP request is required.", nameof(request));
    if (responseTimeout <= TimeSpan.Zero || responseTimeout > TimeSpan.FromMinutes(2)) throw new ArgumentOutOfRangeException(nameof(responseTimeout));
    if (maximumResponseBytes is < 0 or > PolarPftpClient.MaximumExerciseBytes) throw new ArgumentOutOfRangeException(nameof(maximumResponseBytes));

    using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, _disposeCancellation.Token);
    linked.CancelAfter(responseTimeout);
    await _exchangeGate.WaitAsync(linked.Token).ConfigureAwait(false);
    try
    {
      await OpenAsync(linked.Token).ConfigureAwait(false);
      var packets = Channel.CreateBounded<ReadOnlyMemory<byte>>(new BoundedChannelOptions(128)
      {
        SingleReader = true,
        SingleWriter = false,
        FullMode = BoundedChannelFullMode.Wait,
      });
      Exception? receiveFailure = null;
      TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs> handler = (_, args) =>
      {
        if (!packets.Writer.TryWrite(ReadBuffer(args.CharacteristicValue)))
        {
          receiveFailure = new IOException("Polar PFTP response exceeded the receive queue capacity.");
          packets.Writer.TryComplete(receiveFailure);
        }
      };
      _mtu!.ValueChanged += handler;
      try
      {
        byte sequence = 0;
        int frameSize = GetFrameSize();
        GattWriteOption writeOption = _mtu.CharacteristicProperties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse)
          ? GattWriteOption.WriteWithoutResponse
          : GattWriteOption.WriteWithResponse;
        foreach (byte[] packet in PolarPftpFrameCodec.EncodeRequest(request.Span, frameSize, ref sequence))
        {
          using var writer = new DataWriter();
          writer.WriteBytes(packet);
          GattWriteResult result = await _mtu.WriteValueWithResultAsync(writer.DetachBuffer(), writeOption)
            .AsTask(linked.Token).ConfigureAwait(false);
          if (result.Status != GattCommunicationStatus.Success)
            throw new WindowsBleException($"Could not write Polar PFTP request: {result.Status}.");
        }

        var received = new List<ReadOnlyMemory<byte>>();
        while (await packets.Reader.WaitToReadAsync(linked.Token).ConfigureAwait(false))
        {
          while (packets.Reader.TryRead(out ReadOnlyMemory<byte> packet))
          {
            received.Add(packet);
            PolarPftpFrame frame = PolarPftpFrameCodec.Decode(packet.Span);
            if (frame.Status is PolarPftpFrameStatus.Last or PolarPftpFrameStatus.ResponseOrError)
              return PolarPftpFrameCodec.Reassemble(received, 0, maximumResponseBytes);
          }
        }
        throw receiveFailure ?? new IOException("Polar PFTP response ended without a final frame.");
      }
      finally
      {
        _mtu.ValueChanged -= handler;
        packets.Writer.TryComplete();
      }
    }
    catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested && !_disposeCancellation.IsCancellationRequested)
    {
      throw new TimeoutException("Polar PFTP response timed out.");
    }
    finally
    {
      _exchangeGate.Release();
    }
  }

  public ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) != 0) return ValueTask.CompletedTask;
    _disposeCancellation.Cancel();
    _mtu = null; _d2h = null; _h2d = null;
    _service?.Dispose(); _service = null;
    _session?.Dispose(); _session = null;
    _device?.Dispose(); _device = null;
    _exchangeGate.Dispose(); _disposeCancellation.Dispose();
    return ValueTask.CompletedTask;
  }

  private async Task OpenAsync(CancellationToken cancellationToken)
  {
    if (_notificationsEnabled) return;
    _device = (_addressType is { } type
      ? await BluetoothLEDevice.FromBluetoothAddressAsync(_bluetoothAddress, type).AsTask(cancellationToken).ConfigureAwait(false)
      : await BluetoothLEDevice.FromBluetoothAddressAsync(_bluetoothAddress).AsTask(cancellationToken).ConfigureAwait(false))
      ?? throw new InvalidOperationException("Windows could not open the Polar BLE device. Confirm that Windows already has access to the sensor.");
    _session = await WindowsGattSessionPolicy.TryOpenOptionalAsync(
      token => GattSession.FromDeviceIdAsync(_device.BluetoothDeviceId).AsTask(token),
      cancellationToken).ConfigureAwait(false);
    WindowsGattSessionPolicy.ApplyForActiveNotifications(_session);
    GattDeviceServicesResult services = await _device.GetGattServicesForUuidAsync(PolarPftpConstants.ServiceUuid, BluetoothCacheMode.Uncached)
      .AsTask(cancellationToken).ConfigureAwait(false);
    if (services.Status != GattCommunicationStatus.Success || services.Services.Count == 0)
      throw new InvalidOperationException("Polar PFTP service was not found. The sensor may require existing Windows pairing or may not expose memory access.");
    _service = services.Services[0];
    GattOpenStatus open = await _service.OpenAsync(GattSharingMode.SharedReadAndWrite).AsTask(cancellationToken).ConfigureAwait(false);
    if (open is not (GattOpenStatus.Success or GattOpenStatus.AlreadyOpened))
      throw new InvalidOperationException($"Could not open Polar PFTP service: {open}.");

    _mtu = await GetRequiredCharacteristicAsync(PolarPftpConstants.MtuCharacteristicUuid, "MTU", cancellationToken).ConfigureAwait(false);
    _d2h = await GetRequiredCharacteristicAsync(PolarPftpConstants.DeviceToHostCharacteristicUuid, "D2H", cancellationToken).ConfigureAwait(false);
    _h2d = await GetRequiredCharacteristicAsync(PolarPftpConstants.HostToDeviceCharacteristicUuid, "H2D", cancellationToken).ConfigureAwait(false);
    if (!_mtu.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Write) &&
        !_mtu.CharacteristicProperties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse))
      throw new InvalidOperationException("Polar PFTP MTU characteristic is not writable.");
    if (!CanNotify(_mtu)) throw new InvalidOperationException("Polar PFTP MTU characteristic does not notify or indicate responses.");
    if (!CanNotify(_d2h)) throw new InvalidOperationException("Polar PFTP D2H characteristic does not notify or indicate.");
    if (!_h2d.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Write) &&
        !_h2d.CharacteristicProperties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse))
      throw new InvalidOperationException("Polar PFTP H2D characteristic is not writable.");

    await EnableNotificationsAsync(_mtu, cancellationToken).ConfigureAwait(false);
    await EnableNotificationsAsync(_d2h, cancellationToken).ConfigureAwait(false);
    _notificationsEnabled = true;
  }

  private async Task<GattCharacteristic> GetRequiredCharacteristicAsync(Guid uuid, string name, CancellationToken cancellationToken)
  {
    GattCharacteristicsResult result = await _service!.GetCharacteristicsForUuidAsync(uuid, BluetoothCacheMode.Uncached)
      .AsTask(cancellationToken).ConfigureAwait(false);
    if (result.Status != GattCommunicationStatus.Success || result.Characteristics.Count == 0)
      throw new InvalidOperationException($"Polar PFTP {name} characteristic was not found.");
    return result.Characteristics[0];
  }

  private static async Task EnableNotificationsAsync(GattCharacteristic characteristic, CancellationToken cancellationToken)
  {
    GattClientCharacteristicConfigurationDescriptorValue value = characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Indicate)
      ? GattClientCharacteristicConfigurationDescriptorValue.Indicate
      : GattClientCharacteristicConfigurationDescriptorValue.Notify;
    GattCommunicationStatus status = await characteristic.WriteClientCharacteristicConfigurationDescriptorAsync(value)
      .AsTask(cancellationToken).ConfigureAwait(false);
    if (status != GattCommunicationStatus.Success)
      throw new WindowsBleException($"Could not enable Polar PFTP responses: {status}.");
  }

  private int GetFrameSize()
  {
    if (_session is null) return PolarPftpConstants.DefaultFrameSize;
    return Math.Clamp(checked((int)_session.MaxPduSize) - 3, PolarPftpConstants.DefaultFrameSize, PolarPftpConstants.MaximumFrameSize);
  }

  private static bool CanNotify(GattCharacteristic characteristic) =>
    characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Notify) ||
    characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Indicate);

  private static byte[] ReadBuffer(IBuffer buffer)
  {
    using DataReader reader = DataReader.FromBuffer(buffer);
    var value = new byte[reader.UnconsumedBufferLength];
    reader.ReadBytes(value);
    return value;
  }

  private void ThrowIfDisposed() => ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
}
