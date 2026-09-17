using System.Runtime.CompilerServices;
using System.Text;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class PolarH10MemoryClientTests
{
  private static readonly Guid HeartRateService =
    Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");

  [Fact]
  public async Task Memory_status_connects_to_the_unique_fresh_Polar_locator()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var store = new EnrollmentStore(enrollment);
    var factory = new ConnectionFactory();
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService]));
    var locator = new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(store, factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    PolarH10DeviceRecordingStatus status = await session.GetStatusAsync();

    Assert.Equal("AABBCCDDEEFF", Assert.Single(factory.DeviceIds));
    Assert.Equal(enrollment.DeviceId, status.DeviceId);
    Assert.False(status.IsRecording);
  }

  [Fact]
  public async Task Operation_session_reuses_one_locator_and_connection_for_status_and_listing()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new ConnectionFactory();
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService]));
    var client = new PolarH10MemoryClient(
      new EnrollmentStore(enrollment), factory, new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25)), TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    Assert.False((await session.GetStatusAsync()).IsRecording);
    Assert.Empty(await session.ListAsync());
    await session.DisposeAsync();

    Assert.Equal(1, broker.ScanCalls);
    Assert.Single(factory.DeviceIds);
    Assert.Equal(1, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_status_reopens_a_fresh_connection_after_transient_response_timeouts()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var store = new EnrollmentStore(enrollment);
    var factory = new ConnectionFactory(transientTimeouts: 1);
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService]));
    var locator = new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(store, factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    PolarH10DeviceRecordingStatus status = await session.GetStatusAsync();
    await session.DisposeAsync();

    Assert.False(status.IsRecording);
    Assert.Equal(2, broker.ScanCalls);
    Assert.Equal(2, factory.DeviceIds.Count);
    Assert.Equal(2, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_status_stops_after_the_bounded_number_of_transient_attempts()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new ConnectionFactory(transientTimeouts: 2);
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    await Assert.ThrowsAsync<TimeoutException>(() => session.GetStatusAsync());
    await session.DisposeAsync();

    Assert.Equal(2, factory.DeviceIds.Count);
    Assert.Equal(2, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_status_does_not_retry_a_device_protocol_rejection()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new ConnectionFactory(terminalException: new PolarPftpProtocolException(106));
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    await Assert.ThrowsAsync<PolarPftpProtocolException>(() => session.GetStatusAsync());
    await session.DisposeAsync();

    Assert.Single(factory.DeviceIds);
    Assert.Equal(1, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_start_checks_starts_and_confirms_on_one_PFTP_connection()
  {
    const string exerciseId = "tr-0123456789abcdef";
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new RecordingConnectionFactory(exerciseId);
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    PolarH10StartResult start = await session.StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1);
    await session.DisposeAsync();

    Assert.True(start.StartIssued);
    Assert.NotNull(start.StartIssuedAtUtc);
    Assert.True(start.Status.IsRecording);
    Assert.Equal(exerciseId, start.Status.ExerciseId);
    Assert.Equal([PolarPftpQueries.GetStatus, PolarPftpQueries.Start, PolarPftpQueries.GetStatus], factory.QueryIds);
    Assert.Single(factory.DeviceIds);
    Assert.Equal(1, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_start_leaves_a_different_active_recording_untouched()
  {
    const string requestedExerciseId = "tr-requested";
    const string activeExerciseId = "other-owner";
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new RecordingConnectionFactory(requestedExerciseId, activeExerciseId);
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator, TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    PolarH10StartResult start = await session.StartAsync(requestedExerciseId, PolarH10SampleType.HeartRate, 1);
    await session.DisposeAsync();

    Assert.False(start.StartIssued);
    Assert.True(start.Status.IsRecording);
    Assert.Equal(activeExerciseId, start.Status.ExerciseId);
    Assert.Equal([PolarPftpQueries.GetStatus], factory.QueryIds);
    Assert.Single(factory.DeviceIds);
    Assert.Equal(1, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_start_never_reconnects_or_replays_after_the_mutation_was_dispatched()
  {
    const string exerciseId = "tr-uncertain-start";
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new RecordingConnectionFactory(exerciseId, timeoutOnConfirmation: true);
    var client = new PolarH10MemoryClient(
      new EnrollmentStore(enrollment),
      factory,
      new PolarH10ConnectionLocator(
        new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
        TimeSpan.FromMilliseconds(25)),
      TimeProvider.System);

    await using IPolarH10MemorySession session = await client.OpenAsync(enrollment.Id);
    await Assert.ThrowsAsync<TimeoutException>(() => session.StartAsync(exerciseId, PolarH10SampleType.HeartRate, 1));
    await session.DisposeAsync();

    Assert.Equal(1, factory.QueryIds.Count(query => query == PolarPftpQueries.Start));
    Assert.Single(factory.DeviceIds);
    Assert.Equal(1, factory.DisposedConnections);
  }

  private static DeviceEnrollment HeartRate(string deviceId, string displayName) => new(
    Guid.NewGuid(),
    DeviceRole.HeartRate,
    deviceId,
    "bluetooth-heart-rate",
    new string('a', 64),
    displayName,
    "H10",
    null,
    null,
    null,
    TreadmillCapabilityEvidence.Unknown,
    null,
    HeartRateDeviceKind.ChestStrap,
    HeartRateDeviceFamily.Polar);

  private sealed class EnrollmentStore(params DeviceEnrollment[] enrollments) : IDeviceEnrollmentStore
  {
    public Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(
      CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<VersionedDeviceEnrollment>>(enrollments
        .Select(enrollment => new VersionedDeviceEnrollment(enrollment, 1, false, null))
        .ToArray());

    public Task<VersionedDeviceEnrollment?> FindActiveAsync(
      DeviceRole role,
      CancellationToken cancellationToken = default) =>
      Task.FromResult(enrollments
        .Where(enrollment => enrollment.Role == role)
        .Select(enrollment => new VersionedDeviceEnrollment(enrollment, 1, false, null))
        .Cast<VersionedDeviceEnrollment?>()
        .FirstOrDefault());

    public Task<VersionedDeviceEnrollment> EnrollAsync(
      DeviceEnrollment enrollment,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();

    public Task<bool> ForgetAsync(
      DeviceRole role,
      int expectedVersion,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();

    public Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(
      Guid id,
      int expectedVersion,
      string? modelNumber,
      string? firmwareRevision,
      TreadmillCapabilities? capabilities,
      TreadmillCapabilityEvidence evidence,
      DateTimeOffset verifiedAtUtc,
      CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();
  }

  private sealed class AdvertisementBroker(params BleAdvertisement[] advertisements) : IBleAdvertisementBroker
  {
    public int ScanCalls { get; private set; }

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      ScanCalls++;
      foreach (BleAdvertisement advertisement in advertisements)
      {
        cancellationToken.ThrowIfCancellationRequested();
        yield return advertisement;
      }
      await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
    }
  }

  private sealed class ConnectionFactory(int transientTimeouts = 0, Exception? terminalException = null) : IPolarPftpConnectionFactory
  {
    private int remainingTimeouts = transientTimeouts;
    public List<string> DeviceIds { get; } = [];
    public int DisposedConnections { get; private set; }

    public ValueTask<IPolarPftpConnection> ConnectAsync(
      string deviceId,
      BluetoothAddressType? addressType = null,
      CancellationToken cancellationToken = default)
    {
      DeviceIds.Add(deviceId);
      bool shouldTimeout = Interlocked.Decrement(ref remainingTimeouts) >= 0;
      return ValueTask.FromResult<IPolarPftpConnection>(new Connection(
        deviceId, shouldTimeout, terminalException, () => DisposedConnections++));
    }
  }

  private sealed class RecordingConnectionFactory(
    string requestedExerciseId,
    string? activeExerciseId = null,
    bool timeoutOnConfirmation = false) : IPolarPftpConnectionFactory
  {
    private bool recording = activeExerciseId is not null;
    private string? ExerciseId { get; set; } = activeExerciseId;
    private string RequestedExerciseId { get; } = requestedExerciseId;
    public List<string> DeviceIds { get; } = [];
    public List<ushort> QueryIds { get; } = [];
    public int DisposedConnections { get; private set; }
    private int StatusCalls { get; set; }
    private bool TimeoutOnConfirmation { get; } = timeoutOnConfirmation;

    public ValueTask<IPolarPftpConnection> ConnectAsync(
      string deviceId,
      BluetoothAddressType? addressType = null,
      CancellationToken cancellationToken = default)
    {
      DeviceIds.Add(deviceId);
      return ValueTask.FromResult<IPolarPftpConnection>(new RecordingConnection(deviceId, this));
    }

    private sealed class RecordingConnection(string deviceId, RecordingConnectionFactory owner) : IPolarPftpConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
        ReadOnlyMemory<byte> request,
        TimeSpan responseTimeout,
        int maximumResponseBytes,
        CancellationToken cancellationToken = default)
      {
        ushort queryId = PolarPftpRfc60Codec.ReadQueryId(request.Span);
        owner.QueryIds.Add(queryId);
        if (queryId == PolarPftpQueries.Start)
        {
          owner.recording = true;
          owner.ExerciseId = owner.RequestedExerciseId;
          return ValueTask.FromResult(ReadOnlyMemory<byte>.Empty);
        }
        if (queryId != PolarPftpQueries.GetStatus)
          return ValueTask.FromException<ReadOnlyMemory<byte>>(new InvalidOperationException("Unexpected PFTP query."));
        owner.StatusCalls++;
        if (owner.TimeoutOnConfirmation && owner.StatusCalls > 1)
          return ValueTask.FromException<ReadOnlyMemory<byte>>(new TimeoutException("synthetic confirmation timeout"));
        if (!owner.recording)
          return ValueTask.FromResult(ReadOnlyMemory<byte>.Empty);
        byte[] status = PolarPftpProtobuf.EncodeFields((1, 1))
          .Concat(PolarPftpProtobuf.EncodeBytesField(2, Encoding.UTF8.GetBytes(owner.ExerciseId!)))
          .ToArray();
        return ValueTask.FromResult<ReadOnlyMemory<byte>>(status);
      }

      public ValueTask DisposeAsync()
      {
        owner.DisposedConnections++;
        return ValueTask.CompletedTask;
      }
    }
  }

  private sealed class Connection(string deviceId, bool shouldTimeout, Exception? terminalException, Action disposed) : IPolarPftpConnection
  {
    public string DeviceId { get; } = deviceId;

    public ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
      ReadOnlyMemory<byte> request,
      TimeSpan responseTimeout,
      int maximumResponseBytes,
      CancellationToken cancellationToken = default) =>
      shouldTimeout
        ? ValueTask.FromException<ReadOnlyMemory<byte>>(new TimeoutException("transient test timeout"))
        : terminalException is not null
          ? ValueTask.FromException<ReadOnlyMemory<byte>>(terminalException)
          : ValueTask.FromResult(ReadOnlyMemory<byte>.Empty);

    public ValueTask DisposeAsync() { disposed(); return ValueTask.CompletedTask; }
  }
}
