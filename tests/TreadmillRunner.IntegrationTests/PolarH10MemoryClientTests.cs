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

  private sealed class ConnectionFactory : IPolarPftpConnectionFactory
  {
    public List<string> DeviceIds { get; } = [];

    public ValueTask<IPolarPftpConnection> ConnectAsync(
      string deviceId,
      BluetoothAddressType? addressType = null,
      CancellationToken cancellationToken = default)
    {
      DeviceIds.Add(deviceId);
      return ValueTask.FromResult<IPolarPftpConnection>(new Connection(deviceId));
    }
  }

  private sealed class Connection(string deviceId) : IPolarPftpConnection
  {
    public string DeviceId { get; } = deviceId;

    public ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
      ReadOnlyMemory<byte> request,
      TimeSpan responseTimeout,
      int maximumResponseBytes,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult(ReadOnlyMemory<byte>.Empty);

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }
}
