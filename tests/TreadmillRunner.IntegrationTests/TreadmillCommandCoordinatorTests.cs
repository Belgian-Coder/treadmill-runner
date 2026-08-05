using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class TreadmillCommandCoordinatorTests
{
  private static readonly Guid FtmsService = Expand(0x1826);
  private static readonly Guid ControlPoint = Expand(0x2AD9);

  [Fact]
  public async Task Confirms_start_once_from_response_and_new_minimum_speed_telemetry()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x07)
      {
        devices.Set(ReadySnapshot(7, 0.8, observedAt.AddMilliseconds(1)));
      }
    });
    var transport = new FakeCommandTransport(connection);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, transport, VerifiedEnrollment());
    TreadmillCommandIntent intent = StartIntent(7);

    TreadmillCommandResult result = await coordinator.ExecuteAsync(intent, AlwaysCurrent.Instance);
    TreadmillCommandResult duplicate = await coordinator.ExecuteAsync(intent, AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Confirmed, result.Disposition);
    Assert.Equal(0.8, result.AcceptedValue);
    Assert.Equal(0.8, result.MeasuredValue);
    Assert.Equal(new[] { "00", "07" }, connection.Payloads.Select(Convert.ToHexString));
    Assert.Equal(TreadmillCommandDisposition.Rejected, duplicate.Disposition);
    Assert.Equal(2, connection.Payloads.Count);
  }

  [Fact]
  public async Task Missing_request_control_notification_can_continue_but_start_still_requires_confirmation()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0));
    var connection = new FakeCommandConnection(
      (payload, observedAt) =>
      {
        if (payload.Span[0] == 0x07)
        {
          devices.Set(ReadySnapshot(7, 0.8, observedAt.AddMilliseconds(1)));
        }
      },
      omitRequestControlResponse: true);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, PassiveEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteCommissioningAsync(
      StartIntent(7),
      new TreadmillCommissioningApproval("OMEGA Z", "V10.23.17", "owner"),
      AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Confirmed, result.Disposition);
    Assert.Equal(0.8, result.MeasuredValue);
    Assert.Equal(new[] { "00", "07" }, connection.Payloads.Select(Convert.ToHexString));
  }

  [Fact]
  public async Task Rejects_stale_telemetry_without_connecting_or_writing()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(
      7,
      speedKph: 0,
      observedAt: DateTimeOffset.UtcNow.AddSeconds(-10)));
    var connection = new FakeCommandConnection();
    var transport = new FakeCommandTransport(connection);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(
      devices,
      transport,
      VerifiedEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteAsync(StartIntent(7), AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Rejected, result.Disposition);
    Assert.Contains("Fresh treadmill telemetry", result.Reason, StringComparison.Ordinal);
    Assert.Equal(0, transport.ConnectCount);
    Assert.Empty(connection.Payloads);
  }

  [Fact]
  public async Task Reconnect_after_control_acquisition_expires_intent_before_start_write()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x00)
      {
        devices.Set(ReadySnapshot(8, 0, observedAt));
      }
    });
    var transport = new FakeCommandTransport(connection);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, transport, VerifiedEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteAsync(StartIntent(7), AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Rejected, result.Disposition);
    Assert.Single(connection.Payloads);
    Assert.Equal("00", Convert.ToHexString(connection.Payloads[0]));
  }

  [Fact]
  public async Task Successful_response_without_new_telemetry_is_unknown_and_not_retried()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0));
    var connection = new FakeCommandConnection();
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, VerifiedEnrollment());
    TreadmillCommandIntent intent = StartIntent(7);

    TreadmillCommandResult result = await coordinator.ExecuteAsync(intent, AlwaysCurrent.Instance);
    TreadmillCommandResult duplicate = await coordinator.ExecuteAsync(intent, AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Unknown, result.Disposition);
    Assert.Equal(TreadmillCommandDisposition.Rejected, duplicate.Disposition);
    Assert.Equal(new[] { "00", "07" }, connection.Payloads.Select(Convert.ToHexString));
  }

  [Fact]
  public async Task Request_cancellation_after_success_response_still_returns_unknown_without_retry()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0));
    using var requestCancellation = new CancellationTokenSource();
    var connection = new FakeCommandConnection((payload, _) =>
    {
      if (payload.Span[0] == 0x07) requestCancellation.Cancel();
    });
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, VerifiedEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteAsync(
      StartIntent(7),
      AlwaysCurrent.Instance,
      requestCancellation.Token);

    Assert.Equal(TreadmillCommandDisposition.Unknown, result.Disposition);
    Assert.Equal(new[] { "00", "07" }, connection.Payloads.Select(Convert.ToHexString));
  }

  [Fact]
  public async Task Confirms_stop_only_after_new_stopped_telemetry()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0.8));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x08)
      {
        devices.Set(ReadySnapshot(7, 0, observedAt.AddMilliseconds(1)));
      }
    });
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, VerifiedEnrollment());
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var intent = new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Stop,
      now,
      now.AddSeconds(4),
      2,
      SessionState.Running,
      Guid.NewGuid(),
      "browser-a",
      7,
      null);

    TreadmillCommandResult result = await coordinator.ExecuteAsync(intent, AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Confirmed, result.Disposition);
    Assert.Equal(0, result.MeasuredValue);
    Assert.Equal(new[] { "00", "0801" }, connection.Payloads.Select(Convert.ToHexString));
  }

  [Fact]
  public async Task Confirms_target_speed_from_response_and_fresh_measured_speed()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0.8));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x02)
      {
        devices.Set(ReadySnapshot(7, 1.0, observedAt.AddMilliseconds(1)));
      }
    });
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, VerifiedEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteAsync(
      TargetIntent(TreadmillCommandKind.SetSpeed, 1.0, 7),
      AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Confirmed, result.Disposition);
    Assert.Equal(1.0, result.AcceptedValue);
    Assert.Equal(1.0, result.MeasuredValue);
    Assert.Equal(new[] { "00", "026400" }, connection.Payloads.Select(Convert.ToHexString));
  }

  [Fact]
  public async Task Confirms_target_incline_and_pause_with_matching_fresh_telemetry()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(7, speedKph: 0.8));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x03)
      {
        devices.Set(ReadySnapshot(7, 0.8, observedAt.AddMilliseconds(1), inclinePercent: 2.5));
      }
      else if (payload.Span.SequenceEqual(new byte[] { 0x08, 0x02 }))
      {
        devices.Set(ReadySnapshot(7, 0, observedAt.AddMilliseconds(1), inclinePercent: 2.5));
      }
    });
    var transport = new FakeCommandTransport(connection);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, transport, VerifiedEnrollment());

    TreadmillCommandResult incline = await coordinator.ExecuteAsync(
      TargetIntent(TreadmillCommandKind.SetIncline, 2.5, 7),
      AlwaysCurrent.Instance);
    TreadmillCommandResult pause = await coordinator.ExecuteAsync(
      TargetIntent(TreadmillCommandKind.Pause, null, 7),
      AlwaysCurrent.Instance);

    Assert.True(
      incline.Disposition == TreadmillCommandDisposition.Confirmed,
      incline.Reason);
    Assert.Equal(2.5, incline.MeasuredValue);
    Assert.True(
      pause.Disposition == TreadmillCommandDisposition.Confirmed,
      pause.Reason);
    Assert.Equal(0, pause.MeasuredValue);
    Assert.Equal(
      new[] { "00", "031900", "0802" },
      connection.Payloads.Select(Convert.ToHexString));
    Assert.Equal(1, transport.ConnectCount);
  }

  [Fact]
  public async Task Commissioning_stop_allows_exact_passively_observed_device_once()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(11, speedKph: 0));
    var connection = new FakeCommandConnection((payload, observedAt) =>
    {
      if (payload.Span[0] == 0x08)
      {
        devices.Set(ReadySnapshot(11, 0, observedAt.AddMilliseconds(1)));
      }
    });
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, connection, PassiveEnrollment());
    TreadmillCommandIntent intent = StopIntent(11);
    var approval = new TreadmillCommissioningApproval("OMEGA Z", "V10.23.17", "owner");

    TreadmillCommandResult result = await coordinator.ExecuteCommissioningAsync(
      intent,
      approval,
      AlwaysCurrent.Instance);
    TreadmillCommandResult duplicate = await coordinator.ExecuteCommissioningAsync(
      intent,
      approval,
      AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Confirmed, result.Disposition);
    Assert.Equal(0, result.MeasuredValue);
    Assert.Equal(new[] { "00", "0801" }, connection.Payloads.Select(Convert.ToHexString));
    Assert.Equal(TreadmillCommandDisposition.Rejected, duplicate.Disposition);
    Assert.Equal(2, connection.Payloads.Count);
  }

  [Fact]
  public async Task Commissioning_stop_rejects_identity_mismatch_before_connecting()
  {
    var devices = new FakeDeviceCoordinator(ReadySnapshot(11, speedKph: 0));
    var connection = new FakeCommandConnection();
    var transport = new FakeCommandTransport(connection);
    TreadmillCommandCoordinator coordinator = CreateCoordinator(devices, transport, PassiveEnrollment());

    TreadmillCommandResult result = await coordinator.ExecuteCommissioningAsync(
      StopIntent(11),
      new TreadmillCommissioningApproval("OMEGA Z", "different", "owner"),
      AlwaysCurrent.Instance);

    Assert.Equal(TreadmillCommandDisposition.Rejected, result.Disposition);
    Assert.Contains("model and firmware", result.Reason, StringComparison.OrdinalIgnoreCase);
    Assert.Equal(0, transport.ConnectCount);
    Assert.Empty(connection.Payloads);
  }

  private static TreadmillCommandIntent StartIntent(long generation)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Start,
      now,
      now.AddSeconds(4),
      1,
      SessionState.ArmedWaitingForPhysicalStart,
      Guid.NewGuid(),
      "browser-a",
      generation,
      0.8);
  }

  private static TreadmillCommandIntent StopIntent(long generation)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Stop,
      now,
      now.AddSeconds(4),
      0,
      SessionState.ArmedWaitingForPhysicalStart,
      Guid.NewGuid(),
      "commissioning-owner",
      generation,
      null);
  }

  private static TreadmillCommandIntent TargetIntent(
    TreadmillCommandKind kind,
    double? requestedValue,
    long generation)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    return new TreadmillCommandIntent(
      Guid.NewGuid(), Guid.NewGuid(), kind, now, now.AddSeconds(4), 2,
      SessionState.Running, Guid.NewGuid(), "browser-a", generation, requestedValue);
  }

  private static TreadmillCommandCoordinator CreateCoordinator(
    FakeDeviceCoordinator devices,
    FakeCommandConnection connection,
    VersionedDeviceEnrollment enrollment) =>
    CreateCoordinator(devices, new FakeCommandTransport(connection), enrollment);

  private static TreadmillCommandCoordinator CreateCoordinator(
    FakeDeviceCoordinator devices,
    FakeCommandTransport transport,
    VersionedDeviceEnrollment enrollment)
  {
    var services = new ServiceCollection();
    services.AddSingleton<IDeviceEnrollmentStore>(new FakeEnrollmentStore(enrollment));
    ServiceProvider provider = services.BuildServiceProvider();
    return new TreadmillCommandCoordinator(
      TimeProvider.System,
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      devices,
      new TreadmillCommandPolicy(
        TimeSpan.FromSeconds(5),
        TimeSpan.FromMilliseconds(10),
        TimeSpan.FromMilliseconds(100),
        TimeSpan.FromMilliseconds(35),
        TimeSpan.FromMilliseconds(5)),
      NullLogger<TreadmillCommandCoordinator>.Instance);
  }

  private static VersionedDeviceEnrollment VerifiedEnrollment()
  {
    var capabilities = new TreadmillCapabilities(
      CanSetSpeedRemotely: true,
      CanSetInclineRemotely: true,
      CanPauseRemotely: true,
      CanStopRemotely: true,
      CanStartRemotely: true,
      ReportsStandardStartResume: true,
      SpeedRange: new TreadmillOperatingRange(
        0.8m,
        20m,
        0.1m,
        TreadmillCapabilityEvidence.ProtocolReported),
      InclineRange: new TreadmillOperatingRange(
        0m,
        12m,
        0.1m,
        TreadmillCapabilityEvidence.ProtocolReported));
    var enrollment = new DeviceEnrollment(
      Guid.NewGuid(),
      DeviceRole.Treadmill,
      "A0BB3E102117",
      "horizon-omega-z",
      new string('a', 64),
      "Horizon Omega Z",
      "Omega Z",
      "1.0",
      TreadmillTelemetryMode.Ftms,
      capabilities,
      TreadmillCapabilityEvidence.HardwareVerified,
      DateTimeOffset.UtcNow);
    return new VersionedDeviceEnrollment(enrollment, 2, false, null);
  }

  private static VersionedDeviceEnrollment PassiveEnrollment()
  {
    var capabilities = new TreadmillCapabilities(
      ReportsSpeedTargetSupport: true,
      ReportsInclineTargetSupport: true,
      ReportsStandardStartResume: true,
      SpeedRange: new TreadmillOperatingRange(
        0.8m,
        20m,
        0.1m,
        TreadmillCapabilityEvidence.ProtocolReported),
      InclineRange: new TreadmillOperatingRange(
        0m,
        12m,
        0.1m,
        TreadmillCapabilityEvidence.ProtocolReported));
    var enrollment = new DeviceEnrollment(
      Guid.NewGuid(),
      DeviceRole.Treadmill,
      "A0BB3E102117",
      "horizon-omega-z",
      new string('a', 64),
      "Horizon Omega Z",
      "OMEGA Z",
      "V10.23.17",
      TreadmillTelemetryMode.Ftms,
      capabilities,
      TreadmillCapabilityEvidence.PassivelyObserved,
      DateTimeOffset.UtcNow);
    return new VersionedDeviceEnrollment(enrollment, 2, false, null);
  }

  private static DeviceTelemetrySnapshot ReadySnapshot(
    long generation,
    double speedKph,
    DateTimeOffset? observedAt = null,
    double inclinePercent = 0)
  {
    DateTimeOffset observed = observedAt ?? DateTimeOffset.UtcNow;
    return new DeviceTelemetrySnapshot(
      observed,
      new DeviceConnectionSnapshot(
        DeviceRole.Treadmill,
        DeviceConnectionState.Ready,
        generation,
        "Horizon Omega Z",
        "horizon-omega-z",
        "Ftms",
        observed,
        null),
      new DeviceConnectionSnapshot(
        DeviceRole.HeartRate,
        DeviceConnectionState.Disconnected,
        0,
        null,
        null,
        null,
        null,
        null),
      new TreadmillTelemetry(observed, speedKph, inclinePercent),
      null,
      null,
      null);
  }

  private static Guid Expand(ushort shortUuid) =>
    Guid.Parse($"0000{shortUuid:x4}-0000-1000-8000-00805f9b34fb");

  private sealed class AlwaysCurrent : ITreadmillCommandContextValidator
  {
    public static AlwaysCurrent Instance { get; } = new();
    public bool IsCurrent(TreadmillCommandIntent intent) => true;
  }

  private sealed class FakeDeviceCoordinator(DeviceTelemetrySnapshot initial) : IReadOnlyDeviceCoordinator
  {
    private DeviceTelemetrySnapshot _current = initial;
    public DeviceTelemetrySnapshot Current => _current with { CapturedAt = DateTimeOffset.UtcNow };
    public void Set(DeviceTelemetrySnapshot value) => _current = value;
  }

  private sealed class FakeCommandTransport(FakeCommandConnection connection) : IBleCommandCentralTransport
  {
    public int ConnectCount { get; private set; }

    public ValueTask<IBleCommandConnection> ConnectCommandAsync(
      string deviceId,
      CancellationToken cancellationToken = default)
    {
      ConnectCount++;
      return ValueTask.FromResult<IBleCommandConnection>(connection);
    }
  }

  private sealed class FakeCommandConnection(
    Action<ReadOnlyMemory<byte>, DateTimeOffset>? onExchange = null,
    bool omitRequestControlResponse = false) : IBleCommandConnection
  {
    public string DeviceId => "A0BB3E102117";
    public List<byte[]> Payloads { get; } = [];

    public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult<IReadOnlyList<BleService>>([
        new BleService(FtmsService, [new BleCharacteristic(FtmsService, ControlPoint, false, true, true)]),
      ]);

    public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult<ReadOnlyMemory<byte>>(ReadOnlyMemory<byte>.Empty);

    public async IAsyncEnumerable<BleNotification> SubscribeAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.CompletedTask;
      yield break;
    }

    public ValueTask<BleNotification> ExchangeAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      ReadOnlyMemory<byte> value,
      TimeSpan responseTimeout,
      CancellationToken cancellationToken = default)
    {
      byte[] payload = value.ToArray();
      Payloads.Add(payload);
      DateTimeOffset observed = DateTimeOffset.UtcNow;
      onExchange?.Invoke(payload, observed);
      if (omitRequestControlResponse && payload[0] == 0x00)
      {
        throw new WindowsBleResponseTimeoutException(
          serviceUuid,
          characteristicUuid,
          new OperationCanceledException());
      }
      return ValueTask.FromResult(new BleNotification(
        serviceUuid,
        characteristicUuid,
        new byte[] { 0x80, payload[0], 0x01 },
        observed));
    }

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }

  private sealed class FakeEnrollmentStore(VersionedDeviceEnrollment enrollment) : IDeviceEnrollmentStore
  {
    public Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<VersionedDeviceEnrollment>>([enrollment]);

    public Task<VersionedDeviceEnrollment?> FindActiveAsync(
      DeviceRole role,
      CancellationToken cancellationToken = default) =>
      Task.FromResult<VersionedDeviceEnrollment?>(role == DeviceRole.Treadmill ? enrollment : null);

    public Task<VersionedDeviceEnrollment> EnrollAsync(
      DeviceEnrollment value,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<bool> ForgetAsync(
      DeviceRole role,
      int expectedVersion,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) => throw new NotSupportedException();

    public Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(
      Guid id,
      int expectedVersion,
      string? modelNumber,
      string? firmwareRevision,
      TreadmillCapabilities? capabilities,
      TreadmillCapabilityEvidence evidence,
      DateTimeOffset verifiedAtUtc,
      CancellationToken cancellationToken = default) => throw new NotSupportedException();
  }
}
