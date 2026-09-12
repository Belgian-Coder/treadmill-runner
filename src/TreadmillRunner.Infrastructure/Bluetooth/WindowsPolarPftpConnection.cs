using System.Globalization;
using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth;

namespace TreadmillRunner.Infrastructure.Bluetooth;

/// <summary>Windows transport for Polar PS-FTP. It opens existing GATT access and never initiates pairing.</summary>
public sealed class WindowsPolarPftpConnection : IPolarPftpConnection
{
  private readonly BluetoothAddressType? _addressType;
  private readonly IPolarPftpGattTransport _transport;
  private readonly SemaphoreSlim _exchangeGate = new(1, 1);
  private readonly CancellationTokenSource _disposeCancellation = new();
  private int _faulted;
  private int _disposed;

  public WindowsPolarPftpConnection(string deviceId, BluetoothAddressType? addressType = null)
    : this(CreateTransport(deviceId, addressType), addressType)
  {
  }

  internal WindowsPolarPftpConnection(IPolarPftpGattTransport transport)
    : this(transport, addressType: null)
  {
  }

  private WindowsPolarPftpConnection(
    IPolarPftpGattTransport transport,
    BluetoothAddressType? addressType)
  {
    _transport = transport ?? throw new ArgumentNullException(nameof(transport));
    DeviceId = transport.DeviceId;
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
    if (Volatile.Read(ref _faulted) != 0)
      throw new IOException("The Polar PFTP connection cannot be reused after an incomplete exchange.");
    if (request.IsEmpty) throw new ArgumentException("A Polar PFTP request is required.", nameof(request));
    if (responseTimeout <= TimeSpan.Zero || responseTimeout > TimeSpan.FromMinutes(2))
      throw new ArgumentOutOfRangeException(nameof(responseTimeout));
    if (maximumResponseBytes is < 0 or > PolarPftpClient.MaximumExerciseBytes)
      throw new ArgumentOutOfRangeException(nameof(maximumResponseBytes));

    using var linked = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _disposeCancellation.Token);
    linked.CancelAfter(responseTimeout);
    await _exchangeGate.WaitAsync(linked.Token).ConfigureAwait(false);
    try
    {
      ThrowIfDisposed();
      if (Volatile.Read(ref _faulted) != 0)
        throw new IOException("The Polar PFTP connection cannot be reused after an incomplete exchange.");

      await _transport.InitializeAsync(linked.Token).ConfigureAwait(false);

      // A completed exchange has consumed its final frame. Anything still in
      // the native callback queue belongs to connection setup or an obsolete
      // operation and must not satisfy the next request.
      _transport.DiscardPendingResponses();

      byte sequence = 0;
      foreach (byte[] packet in PolarPftpFrameCodec.EncodeRequest(
        request.Span,
        _transport.FrameSize,
        ref sequence))
      {
        await _transport.WriteAsync(packet, linked.Token).ConfigureAwait(false);
      }

      var received = new List<ReadOnlyMemory<byte>>();
      while (true)
      {
        ReadOnlyMemory<byte> packet = await _transport.ReceiveAsync(linked.Token)
          .ConfigureAwait(false);
        received.Add(packet);
        PolarPftpFrame frame = PolarPftpFrameCodec.Decode(packet.Span);
        if (frame.Status is PolarPftpFrameStatus.Last or PolarPftpFrameStatus.ResponseOrError)
          return PolarPftpFrameCodec.Reassemble(received, 0, maximumResponseBytes);
      }
    }
    catch (OperationCanceledException) when (
      !cancellationToken.IsCancellationRequested &&
      !_disposeCancellation.IsCancellationRequested)
    {
      Interlocked.Exchange(ref _faulted, 1);
      throw new TimeoutException("Polar PFTP response timed out.");
    }
    catch (Exception exception) when (exception is not PolarPftpProtocolException)
    {
      Interlocked.Exchange(ref _faulted, 1);
      throw;
    }
    finally
    {
      _exchangeGate.Release();
    }
  }

  public async ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
    _disposeCancellation.Cancel();
    await _exchangeGate.WaitAsync().ConfigureAwait(false);
    try
    {
      await _transport.DisposeAsync().ConfigureAwait(false);
    }
    finally
    {
      _exchangeGate.Dispose();
      _disposeCancellation.Dispose();
    }
  }

  private static IPolarPftpGattTransport CreateTransport(
    string deviceId,
    BluetoothAddressType? addressType)
  {
    if (deviceId is null || deviceId.Length != 12 ||
        !ulong.TryParse(deviceId, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out _))
      throw new ArgumentException(
        "A Windows BLE device ID must contain exactly 12 hexadecimal digits.",
        nameof(deviceId));
    return new WindowsPolarPftpGattTransport(deviceId, addressType);
  }

  private void ThrowIfDisposed() =>
    ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
}
