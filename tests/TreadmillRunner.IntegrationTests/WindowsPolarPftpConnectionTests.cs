using System.Threading.Channels;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.IntegrationTests;

public sealed class WindowsPolarPftpConnectionTests
{
  [Fact]
  public void Pftp_requests_prefer_acknowledged_writes_when_both_modes_are_available()
  {
    GattCharacteristicProperties properties =
      GattCharacteristicProperties.Write |
      GattCharacteristicProperties.WriteWithoutResponse;

    Assert.Equal(
      GattWriteOption.WriteWithResponse,
      WindowsPolarPftpGattTransport.SelectWriteOption(properties));
    Assert.Equal(
      GattWriteOption.WriteWithoutResponse,
      WindowsPolarPftpGattTransport.SelectWriteOption(
        GattCharacteristicProperties.WriteWithoutResponse));
  }

  [Fact]
  public void Pftp_ready_barrier_prefers_notifications_over_indications()
  {
    GattCharacteristicProperties properties =
      GattCharacteristicProperties.Notify |
      GattCharacteristicProperties.Indicate;

    Assert.Equal(
      GattClientCharacteristicConfigurationDescriptorValue.Notify,
      WindowsPolarPftpGattTransport.SelectNotificationMode(properties));
  }

  [Fact]
  public async Task Exchange_does_not_write_before_the_transport_is_ready()
  {
    var transport = new ScriptedGattTransport
    {
      HoldInitialization = true,
      OnWrite = current => current.Enqueue(Response("ready"u8)),
    };
    await using var connection = new WindowsPolarPftpConnection(transport);

    Task<ReadOnlyMemory<byte>> exchange = connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(2),
      128).AsTask();

    await transport.InitializationStarted.Task.WaitAsync(TimeSpan.FromSeconds(1));
    Assert.Empty(transport.Writes);

    transport.ReleaseInitialization.TrySetResult();

    Assert.Equal("ready"u8.ToArray(), (await exchange).ToArray());
  }

  [Fact]
  public async Task Exchange_captures_a_response_published_during_the_acknowledged_write()
  {
    var transport = new ScriptedGattTransport
    {
      OnWrite = current => current.Enqueue(Response("immediate"u8)),
    };
    await using var connection = new WindowsPolarPftpConnection(transport);

    ReadOnlyMemory<byte> response = await connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128);

    Assert.Equal("immediate"u8.ToArray(), response.ToArray());
    Assert.Single(transport.Writes);
  }

  [Fact]
  public async Task Exchange_discards_setup_or_obsolete_packets_before_writing()
  {
    var transport = new ScriptedGattTransport
    {
      OnInitialize = current => current.Enqueue(Response("obsolete"u8)),
      OnWrite = current => current.Enqueue(Response("current"u8)),
    };
    await using var connection = new WindowsPolarPftpConnection(transport);

    ReadOnlyMemory<byte> response = await connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128);

    Assert.Equal("current"u8.ToArray(), response.ToArray());
    Assert.Equal(1, transport.DiscardCalls);
  }

  [Fact]
  public async Task Exchange_reassembles_a_multi_frame_MTU_response()
  {
    var transport = new ScriptedGattTransport
    {
      OnWrite = current =>
      {
        current.Enqueue(Frame(0, PolarPftpFrameStatus.More, next: false, "part-"u8));
        current.Enqueue(Frame(1, PolarPftpFrameStatus.Last, next: true, "two"u8));
      },
    };
    await using var connection = new WindowsPolarPftpConnection(transport);

    ReadOnlyMemory<byte> response = await connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128);

    Assert.Equal("part-two"u8.ToArray(), response.ToArray());
  }

  [Fact]
  public async Task Missing_response_is_a_bounded_timeout_and_disposal_cancels_the_receiver()
  {
    var transport = new ScriptedGattTransport();
    var connection = new WindowsPolarPftpConnection(transport);

    await Assert.ThrowsAsync<TimeoutException>(() => connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromMilliseconds(40),
      128).AsTask());

    await Assert.ThrowsAsync<IOException>(() => connection.ExchangeAsync(
      "must-use-fresh-connection"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128).AsTask());

    await connection.DisposeAsync();
    Assert.True(transport.Disposed);
  }

  [Fact]
  public async Task Caller_queued_behind_a_timed_out_exchange_cannot_reuse_that_connection_generation()
  {
    var transport = new ScriptedGattTransport();
    await using var connection = new WindowsPolarPftpConnection(transport);

    Task<ReadOnlyMemory<byte>> first = connection.ExchangeAsync(
      "first"u8.ToArray(),
      TimeSpan.FromMilliseconds(80),
      128).AsTask();
    await transport.WriteObserved.Task.WaitAsync(TimeSpan.FromSeconds(1));
    Task<ReadOnlyMemory<byte>> queued = connection.ExchangeAsync(
      "queued"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128).AsTask();

    await Assert.ThrowsAsync<TimeoutException>(() => first);
    await Assert.ThrowsAsync<IOException>(() => queued);
    Assert.Single(transport.Writes);
  }

  [Fact]
  public async Task Caller_cancellation_is_not_reclassified_as_a_response_timeout()
  {
    var transport = new ScriptedGattTransport();
    await using var connection = new WindowsPolarPftpConnection(transport);
    using var cancellation = new CancellationTokenSource();

    Task<ReadOnlyMemory<byte>> exchange = connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128,
      cancellation.Token).AsTask();
    await transport.WriteObserved.Task.WaitAsync(TimeSpan.FromSeconds(1));
    cancellation.Cancel();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => exchange);

    await Assert.ThrowsAsync<IOException>(() => connection.ExchangeAsync(
      "must-use-fresh-connection"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128).AsTask());
  }

  [Fact]
  public async Task Disposal_waits_for_an_in_flight_receiver_to_unwind_before_disposing_its_gate()
  {
    var transport = new ScriptedGattTransport();
    var connection = new WindowsPolarPftpConnection(transport);
    Task<ReadOnlyMemory<byte>> exchange = connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(2),
      128).AsTask();
    await transport.WriteObserved.Task.WaitAsync(TimeSpan.FromSeconds(1));

    Task disposal = connection.DisposeAsync().AsTask();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => exchange);
    await disposal;
    Assert.True(transport.Disposed);
  }

  [Fact]
  public async Task Malformed_current_response_is_rejected_instead_of_treated_as_success()
  {
    var transport = new ScriptedGattTransport
    {
      OnWrite = current => current.Enqueue(new byte[] { 0x04 }),
    };
    await using var connection = new WindowsPolarPftpConnection(transport);

    await Assert.ThrowsAsync<FormatException>(() => connection.ExchangeAsync(
      "request"u8.ToArray(),
      TimeSpan.FromSeconds(1),
      128).AsTask());
  }

  private static byte[] Response(ReadOnlySpan<byte> payload) =>
    Frame(0, PolarPftpFrameStatus.Last, next: false, payload);

  private static byte[] Frame(
    byte sequence,
    PolarPftpFrameStatus status,
    bool next,
    ReadOnlySpan<byte> payload)
  {
    var frame = new byte[payload.Length + 1];
    frame[0] = PolarPftpFrameCodec.ComposeHeader(sequence, status, next);
    payload.CopyTo(frame.AsSpan(1));
    return frame;
  }

  private sealed class ScriptedGattTransport : IPolarPftpGattTransport
  {
    private readonly Channel<ReadOnlyMemory<byte>> _responses = Channel.CreateUnbounded<ReadOnlyMemory<byte>>();
    private int _initialized;

    public string DeviceId => "A1B2C3D4E5F6";

    public int FrameSize => 64;

    public bool HoldInitialization { get; init; }

    public TaskCompletionSource InitializationStarted { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);

    public TaskCompletionSource ReleaseInitialization { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);

    public Action<ScriptedGattTransport>? OnInitialize { get; init; }

    public Action<ScriptedGattTransport>? OnWrite { get; init; }

    public List<byte[]> Writes { get; } = [];

    public TaskCompletionSource WriteObserved { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);

    public int DiscardCalls { get; private set; }

    public bool Disposed { get; private set; }

    public async ValueTask InitializeAsync(CancellationToken cancellationToken = default)
    {
      if (Interlocked.Exchange(ref _initialized, 1) != 0) return;
      InitializationStarted.TrySetResult();
      if (HoldInitialization)
        await ReleaseInitialization.Task.WaitAsync(cancellationToken);
      OnInitialize?.Invoke(this);
    }

    public void DiscardPendingResponses()
    {
      DiscardCalls++;
      while (_responses.Reader.TryRead(out _)) { }
    }

    public ValueTask WriteAsync(
      ReadOnlyMemory<byte> packet,
      CancellationToken cancellationToken = default)
    {
      cancellationToken.ThrowIfCancellationRequested();
      Writes.Add(packet.ToArray());
      WriteObserved.TrySetResult();
      OnWrite?.Invoke(this);
      return ValueTask.CompletedTask;
    }

    public ValueTask<ReadOnlyMemory<byte>> ReceiveAsync(
      CancellationToken cancellationToken = default) =>
      _responses.Reader.ReadAsync(cancellationToken);

    public ValueTask DisposeAsync()
    {
      Disposed = true;
      _responses.Writer.TryComplete();
      return ValueTask.CompletedTask;
    }

    public void Enqueue(ReadOnlyMemory<byte> packet) =>
      Assert.True(_responses.Writer.TryWrite(packet));
  }
}
