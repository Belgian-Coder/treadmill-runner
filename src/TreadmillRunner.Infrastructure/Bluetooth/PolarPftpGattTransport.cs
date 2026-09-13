using System.Globalization;
using System.Runtime.ExceptionServices;
using System.Threading.Channels;
using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Foundation;
using Windows.Storage.Streams;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal interface IPolarPftpGattTransport : IAsyncDisposable
{
  string DeviceId { get; }

  int FrameSize { get; }

  ValueTask InitializeAsync(CancellationToken cancellationToken = default);

  void DiscardPendingResponses();

  ValueTask WriteAsync(ReadOnlyMemory<byte> packet, CancellationToken cancellationToken = default);

  ValueTask<ReadOnlyMemory<byte>> ReceiveAsync(CancellationToken cancellationToken = default);
}

/// <summary>
/// Owns the native Windows GATT objects used by one Polar PFTP connection. The
/// MTU receive callback is installed before either CCCD is configured so a
/// response can never outrun receiver registration.
/// </summary>
internal sealed class WindowsPolarPftpGattTransport : IPolarPftpGattTransport
{
  private readonly ulong _bluetoothAddress;
  private readonly BluetoothAddressType? _addressType;
  private readonly CancellationTokenSource _disposeCancellation = new();
  private readonly Channel<ReadOnlyMemory<byte>> _responses = Channel.CreateBounded<ReadOnlyMemory<byte>>(
    new BoundedChannelOptions(128)
    {
      SingleReader = true,
      SingleWriter = false,
      FullMode = BoundedChannelFullMode.Wait,
    });
  private BluetoothLEDevice? _device;
  private GattSession? _session;
  private GattDeviceService? _service;
  private GattCharacteristic? _mtu;
  private GattCharacteristic? _d2h;
  private GattCharacteristic? _h2d;
  private TypedEventHandler<GattCharacteristic, GattValueChangedEventArgs>? _mtuHandler;
  private TypedEventHandler<BluetoothLEDevice, object>? _connectionHandler;
  private TypedEventHandler<GattSession, GattSessionStatusChangedEventArgs>? _sessionHandler;
  private bool _initialized;
  private int _disposed;

  internal WindowsPolarPftpGattTransport(
    string deviceId,
    BluetoothAddressType? addressType = null)
  {
    if (deviceId is null || deviceId.Length != 12 ||
        !ulong.TryParse(deviceId, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out _bluetoothAddress))
      throw new ArgumentException("A Windows BLE device ID must contain exactly 12 hexadecimal digits.", nameof(deviceId));

    DeviceId = deviceId.ToUpperInvariant();
    _addressType = addressType;
  }

  public string DeviceId { get; }

  public int FrameSize { get; private set; } = PolarPftpConstants.DefaultFrameSize;

  public async ValueTask InitializeAsync(CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    if (_initialized) return;

    using var linked = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    CancellationToken operationCancellation = linked.Token;

    _device = (_addressType is { } type
      ? await BluetoothLEDevice.FromBluetoothAddressAsync(_bluetoothAddress, type)
        .AsTask(operationCancellation).ConfigureAwait(false)
      : await BluetoothLEDevice.FromBluetoothAddressAsync(_bluetoothAddress)
        .AsTask(operationCancellation).ConfigureAwait(false))
      ?? throw new InvalidOperationException(
        "Windows could not open the Polar BLE device. Confirm that Windows already has access to the sensor.");

    _session = await WindowsGattSessionPolicy.TryOpenOptionalAsync(
      token => GattSession.FromDeviceIdAsync(_device.BluetoothDeviceId).AsTask(token),
      operationCancellation).ConfigureAwait(false);
    WindowsGattSessionPolicy.ApplyForActiveNotifications(_session);

    GattDeviceServicesResult services = await _device
      .GetGattServicesForUuidAsync(PolarPftpConstants.ServiceUuid, BluetoothCacheMode.Uncached)
      .AsTask(operationCancellation).ConfigureAwait(false);
    if (services.Status != GattCommunicationStatus.Success || services.Services.Count == 0)
      throw new InvalidOperationException(
        "Polar PFTP service was not found. The sensor may require existing Windows pairing or may not expose memory access.");

    _service = services.Services[0];
    for (int index = 1; index < services.Services.Count; index++) services.Services[index].Dispose();
    GattOpenStatus open = await _service.OpenAsync(GattSharingMode.SharedReadAndWrite)
      .AsTask(operationCancellation).ConfigureAwait(false);
    if (open is not (GattOpenStatus.Success or GattOpenStatus.AlreadyOpened))
      throw new InvalidOperationException($"Could not open Polar PFTP service: {open}.");

    _mtu = await GetRequiredCharacteristicAsync(
      PolarPftpConstants.MtuCharacteristicUuid,
      "MTU",
      operationCancellation).ConfigureAwait(false);
    _d2h = await GetRequiredCharacteristicAsync(
      PolarPftpConstants.DeviceToHostCharacteristicUuid,
      "D2H",
      operationCancellation).ConfigureAwait(false);
    _h2d = await GetRequiredCharacteristicAsync(
      PolarPftpConstants.HostToDeviceCharacteristicUuid,
      "H2D",
      operationCancellation).ConfigureAwait(false);

    _ = SelectWriteOption(_mtu.CharacteristicProperties);
    if (!CanNotify(_mtu))
      throw new InvalidOperationException("Polar PFTP MTU characteristic does not notify or indicate responses.");
    if (!CanNotify(_d2h))
      throw new InvalidOperationException("Polar PFTP D2H characteristic does not notify or indicate.");
    if (!_h2d.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Write) &&
        !_h2d.CharacteristicProperties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse))
      throw new InvalidOperationException("Polar PFTP H2D characteristic is not writable.");

    RegisterNativeHandlers();

    // The two awaited descriptor writes are the PFTP-ready barrier. They are
    // deliberately performed only after the MTU ValueChanged handler exists.
    await EnableNotificationsAsync(_mtu, operationCancellation).ConfigureAwait(false);
    await EnableNotificationsAsync(_d2h, operationCancellation).ConfigureAwait(false);

    if (_device.ConnectionStatus == BluetoothConnectionStatus.Disconnected)
      throw new WindowsBleDisconnectedException(new WindowsBleDisconnectContext(
        WindowsBleDisconnectOrigin.PostCccdConnectionStatusCheck,
        DateTimeOffset.UtcNow,
        ReadSessionStatus(_session),
        SessionError: null,
        operationCancellation.IsCancellationRequested,
        Volatile.Read(ref _disposed) != 0));

    FrameSize = _session is null
      ? PolarPftpConstants.DefaultFrameSize
      : Math.Clamp(
        checked((int)_session.MaxPduSize) - 3,
        PolarPftpConstants.DefaultFrameSize,
        PolarPftpConstants.MaximumFrameSize);
    _initialized = true;
  }

  public void DiscardPendingResponses()
  {
    ThrowIfDisposed();
    while (_responses.Reader.TryRead(out _)) { }
  }

  public async ValueTask WriteAsync(
    ReadOnlyMemory<byte> packet,
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    if (!_initialized || _mtu is null)
      throw new InvalidOperationException("Polar PFTP GATT transport is not initialized.");

    using var linked = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    using var writer = new DataWriter();
    writer.WriteBytes(packet.ToArray());
    GattWriteResult result = await _mtu.WriteValueWithResultAsync(
        writer.DetachBuffer(),
        SelectWriteOption(_mtu.CharacteristicProperties))
      .AsTask(linked.Token).ConfigureAwait(false);
    WindowsBleStatus.ThrowIfFailed(
      result.Status,
      result.ProtocolError,
      "write Polar PFTP request");
  }

  public async ValueTask<ReadOnlyMemory<byte>> ReceiveAsync(
    CancellationToken cancellationToken = default)
  {
    ThrowIfDisposed();
    using var linked = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    try
    {
      return await _responses.Reader.ReadAsync(linked.Token).ConfigureAwait(false);
    }
    catch (ChannelClosedException exception)
    {
      if (exception.InnerException is { } innerException)
      {
        ExceptionDispatchInfo.Capture(innerException).Throw();
      }
      throw new IOException("Polar PFTP response channel closed before a final frame arrived.", exception);
    }
  }

  public ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) != 0) return ValueTask.CompletedTask;
    NativeResourceOwnership.RunCleanupActions(
      suppressExceptions: true,
      () => _disposeCancellation.Cancel(),
      () => { if (_mtu is not null && _mtuHandler is not null) _mtu.ValueChanged -= _mtuHandler; },
      () => { if (_device is not null && _connectionHandler is not null) _device.ConnectionStatusChanged -= _connectionHandler; },
      () => { if (_session is not null && _sessionHandler is not null) _session.SessionStatusChanged -= _sessionHandler; },
      () => _responses.Writer.TryComplete(),
      () => _service?.Dispose(),
      () => _session?.Dispose(),
      () => _device?.Dispose(),
      () => _disposeCancellation.Dispose());
    _mtu = null;
    _d2h = null;
    _h2d = null;
    _service = null;
    _session = null;
    _device = null;
    _mtuHandler = null;
    _connectionHandler = null;
    _sessionHandler = null;
    return ValueTask.CompletedTask;
  }

  internal static GattWriteOption SelectWriteOption(GattCharacteristicProperties properties)
  {
    if (properties.HasFlag(GattCharacteristicProperties.WriteWithoutResponse))
      return GattWriteOption.WriteWithoutResponse;
    if (properties.HasFlag(GattCharacteristicProperties.Write))
      return GattWriteOption.WriteWithResponse;
    throw new InvalidOperationException("Polar PFTP MTU characteristic is not writable.");
  }

  internal static GattClientCharacteristicConfigurationDescriptorValue SelectNotificationMode(
    GattCharacteristicProperties properties)
  {
    if (properties.HasFlag(GattCharacteristicProperties.Notify))
      return GattClientCharacteristicConfigurationDescriptorValue.Notify;
    if (properties.HasFlag(GattCharacteristicProperties.Indicate))
      return GattClientCharacteristicConfigurationDescriptorValue.Indicate;
    throw new InvalidOperationException("Polar PFTP characteristic does not notify or indicate.");
  }

  private void RegisterNativeHandlers()
  {
    _mtuHandler = (_, args) =>
    {
      try
      {
        if (!_responses.Writer.TryWrite(ReadBuffer(args.CharacteristicValue)))
          _responses.Writer.TryComplete(
            new IOException("Polar PFTP response exceeded the receive queue capacity."));
      }
      catch (Exception exception)
      {
        _responses.Writer.TryComplete(exception);
      }
    };
    _connectionHandler = (device, _) =>
    {
      if (device.ConnectionStatus != BluetoothConnectionStatus.Disconnected) return;
      CompleteForDisconnect(
        WindowsBleDisconnectOrigin.ConnectionStatusChanged,
        ReadSessionStatus(_session),
        sessionError: null);
    };
    _mtu!.ValueChanged += _mtuHandler;
    _device!.ConnectionStatusChanged += _connectionHandler;

    if (_session is not null)
    {
      _sessionHandler = (_, args) =>
      {
        if (args.Status == GattSessionStatus.Closed)
          CompleteForDisconnect(
            WindowsBleDisconnectOrigin.GattSessionStatusChanged,
            args.Status,
            args.Error);
      };
      _session.SessionStatusChanged += _sessionHandler;
    }
  }

  private void CompleteForDisconnect(
    WindowsBleDisconnectOrigin origin,
    GattSessionStatus? sessionStatus,
    BluetoothError? sessionError)
  {
    bool disposalRequested = Volatile.Read(ref _disposed) != 0 || _disposeCancellation.IsCancellationRequested;
    _responses.Writer.TryComplete(
      disposalRequested
        ? null
        : new WindowsBleDisconnectedException(new WindowsBleDisconnectContext(
          origin,
          DateTimeOffset.UtcNow,
          sessionStatus,
          sessionError,
          CancellationRequested: false,
          DisposalRequested: false)));
  }

  private async Task<GattCharacteristic> GetRequiredCharacteristicAsync(
    Guid uuid,
    string name,
    CancellationToken cancellationToken)
  {
    GattCharacteristicsResult result = await _service!
      .GetCharacteristicsForUuidAsync(uuid, BluetoothCacheMode.Uncached)
      .AsTask(cancellationToken).ConfigureAwait(false);
    if (result.Status != GattCommunicationStatus.Success || result.Characteristics.Count == 0)
      throw new InvalidOperationException($"Polar PFTP {name} characteristic was not found.");
    return result.Characteristics[0];
  }

  private static async Task EnableNotificationsAsync(
    GattCharacteristic characteristic,
    CancellationToken cancellationToken)
  {
    GattCommunicationStatus status = await characteristic
      .WriteClientCharacteristicConfigurationDescriptorAsync(
        SelectNotificationMode(characteristic.CharacteristicProperties))
      .AsTask(cancellationToken).ConfigureAwait(false);
    if (status != GattCommunicationStatus.Success)
      throw new WindowsBleException($"Could not enable Polar PFTP responses: {status}.");
  }

  private static bool CanNotify(GattCharacteristic characteristic) =>
    characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Notify) ||
    characteristic.CharacteristicProperties.HasFlag(GattCharacteristicProperties.Indicate);

  private static GattSessionStatus? ReadSessionStatus(GattSession? session)
  {
    try { return session?.SessionStatus; }
    catch { return null; }
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
}
