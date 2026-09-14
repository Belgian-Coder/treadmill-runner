using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Polar;
using TreadmillRunner.Infrastructure.Persistence;
using Microsoft.Extensions.Logging.Abstractions;

namespace TreadmillRunner.IntegrationTests;

public sealed class PolarH10MemoryAccessCoordinatorTests
{
  [Fact]
  public async Task Exact_H10_live_connection_is_resumed_after_memory_access()
  {
    Guid h10Id = Guid.NewGuid();
    var devices = new StubEnrollmentStore([
      Enrollment(h10Id, "Polar H10", "H10"),
      Enrollment(Guid.NewGuid(), "Garmin watch", null),
    ]);
    var live = new TrackingDeviceCoordinator();
    var coordinator = new PolarH10MemoryAccessCoordinator(devices, live, NullLogger<PolarH10MemoryAccessCoordinator>.Instance);

    IPolarH10MemoryAccessLease lease = await coordinator.AcquireAsync(null);

    Assert.Equal(h10Id, lease.EnrollmentId);
    Assert.Equal([h10Id], live.Suspended);
    Assert.Empty(live.Resumed);

    await lease.DisposeAsync();
    await lease.DisposeAsync();

    Assert.Equal([h10Id], live.Resumed);
  }

  [Fact]
  public async Task Access_is_denied_when_live_connection_cannot_be_released()
  {
    Guid h10Id = Guid.NewGuid();
    var coordinator = new PolarH10MemoryAccessCoordinator(
      new StubEnrollmentStore([Enrollment(h10Id, "Polar H10", "H10")]),
      new TrackingDeviceCoordinator(suspendResult: false),
      NullLogger<PolarH10MemoryAccessCoordinator>.Instance);

    InvalidOperationException error = await Assert.ThrowsAsync<InvalidOperationException>(
      () => coordinator.AcquireAsync(h10Id));

    Assert.Contains("could not be released", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  private static VersionedDeviceEnrollment Enrollment(Guid id, string displayName, string? modelNumber) => new(
    new DeviceEnrollment(
      id,
      DeviceRole.HeartRate,
      $"device-{id:N}",
      "bluetooth-heart-rate",
      new string('a', 64),
      displayName,
      modelNumber,
      null,
      null,
      null,
      TreadmillCapabilityEvidence.Unknown,
      null),
    1,
    false,
    null);

  private sealed class TrackingDeviceCoordinator(bool suspendResult = true) : IReadOnlyDeviceCoordinator
  {
    public DeviceTelemetrySnapshot Current => null!;
    public List<Guid> Suspended { get; } = [];
    public List<Guid> Resumed { get; } = [];

    public Task<bool> SuspendConnectionAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
    {
      Suspended.Add(enrollmentId);
      return Task.FromResult(suspendResult);
    }

    public Task ResumeConnectionAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
    {
      Resumed.Add(enrollmentId);
      return Task.CompletedTask;
    }
  }

  private sealed class StubEnrollmentStore(IReadOnlyList<VersionedDeviceEnrollment> enrollments) : IDeviceEnrollmentStore
  {
    public Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(CancellationToken cancellationToken = default) =>
      Task.FromResult(enrollments);

    public Task<VersionedDeviceEnrollment?> FindActiveAsync(DeviceRole role, CancellationToken cancellationToken = default) =>
      Task.FromResult(enrollments.FirstOrDefault(item => item.Enrollment.Role == role));

    public Task<VersionedDeviceEnrollment> EnrollAsync(DeviceEnrollment enrollment, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();

    public Task<bool> ForgetAsync(DeviceRole role, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();

    public Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(Guid id, int expectedVersion, string? modelNumber, string? firmwareRevision, TreadmillCapabilities? capabilities, TreadmillCapabilityEvidence evidence, DateTimeOffset verifiedAtUtc, CancellationToken cancellationToken = default) =>
      throw new NotSupportedException();
  }
}
