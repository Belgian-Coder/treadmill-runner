using System.Runtime.CompilerServices;
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
    var client = new PolarH10MemoryClient(store, factory, locator);

    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(enrollment.Id);

    Assert.Equal("AABBCCDDEEFF", Assert.Single(factory.DeviceIds));
    Assert.Equal(enrollment.DeviceId, status.DeviceId);
    Assert.False(status.IsRecording);
  }

  [Fact]
  public async Task Memory_status_reopens_a_fresh_connection_after_transient_response_timeouts()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var store = new EnrollmentStore(enrollment);
    var factory = new ConnectionFactory(transientTimeouts: 3);
    var broker = new AdvertisementBroker(
      new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService]));
    var locator = new PolarH10ConnectionLocator(broker, TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(store, factory, locator);

    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(enrollment.Id);

    Assert.False(status.IsRecording);
    Assert.Equal(4, factory.DeviceIds.Count);
    Assert.Equal(4, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_status_stops_after_the_bounded_number_of_transient_attempts()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new ConnectionFactory(transientTimeouts: 4);
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator);

    await Assert.ThrowsAsync<TimeoutException>(() => client.GetStatusAsync(enrollment.Id));

    Assert.Equal(4, factory.DeviceIds.Count);
    Assert.Equal(4, factory.DisposedConnections);
  }

  [Fact]
  public async Task Memory_status_does_not_retry_a_device_protocol_rejection()
  {
    DeviceEnrollment enrollment = HeartRate("102030405060", "Polar H10");
    var factory = new ConnectionFactory(terminalException: new PolarPftpProtocolException(106));
    var locator = new PolarH10ConnectionLocator(
      new AdvertisementBroker(new BleAdvertisement("AABBCCDDEEFF", "Polar H10", -42, [HeartRateService])),
      TimeSpan.FromMilliseconds(25));
    var client = new PolarH10MemoryClient(new EnrollmentStore(enrollment), factory, locator);

    await Assert.ThrowsAsync<PolarPftpProtocolException>(() => client.GetStatusAsync(enrollment.Id));

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
    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
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
