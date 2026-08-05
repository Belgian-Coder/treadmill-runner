using System.Runtime.CompilerServices;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Gateway.Operations;

namespace TreadmillRunner.IntegrationTests;

public sealed class ReadOnlyDeviceCoordinatorTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "coordinator.db"));
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  [Fact]
  public async Task Connects_multiple_sensors_and_selects_only_the_active_runners_preferred_source()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var marc = new UserProfile(Guid.NewGuid(), "Marc", UnitSystem.Metric, 75, 190, 18, []);
    var wife = new UserProfile(Guid.NewGuid(), "Runner 2", UnitSystem.Metric, 65, 185, 16, []);
    await profiles.CreateAsync(marc, now, Op("profile.create", now));
    await profiles.CreateAsync(wife, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN", "Garmin fēnix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(marc.Id, 0, true, true), new HeartRateAssignmentPreference(wife.Id, 0, true, true)],
      now, Op("device.enroll", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(marc.Id, 1, true, false)],
      now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), new ScriptedBleTransport(), TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.CurrentForProfile(marc.Id).HeartRateSources?.Count < 2)
      {
        await Task.Delay(25, timeout.Token);
      }
      DeviceTelemetrySnapshot marcStatus = coordinator.CurrentForProfile(marc.Id);
      DeviceTelemetrySnapshot wifeStatus = coordinator.CurrentForProfile(wife.Id);
      Assert.Equal(2, marcStatus.HeartRateSources!.Count);
      Assert.Equal(polar.Id, marcStatus.SelectedHeartRateEnrollmentId);
      Assert.Equal(polar.Id, wifeStatus.SelectedHeartRateEnrollmentId);
      Assert.Equal(HeartRateDeviceFamily.Polar, marcStatus.SelectedHeartRateDeviceFamily);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  public Task DisposeAsync()
  {
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Connects_treadmill_and_heart_rate_and_publishes_fresh_telemetry()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    await store.EnrollAsync(HeartRate(), now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      new ScriptedBleTransport(),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current is not
        { TreadmillTelemetry: not null, HeartRateBpm: not null, SelectedHeartRateBatteryPercent: 86 })
      {
        await Task.Delay(25, timeout.Token);
      }

      DeviceTelemetrySnapshot snapshot = coordinator.Current;
      Assert.Equal(DeviceConnectionState.Ready, snapshot.Treadmill.State);
      Assert.Equal(DeviceConnectionState.Ready, snapshot.HeartRate.State);
      Assert.Equal(6.0, snapshot.TreadmillTelemetry!.SpeedKph);
      Assert.Equal(1.0, snapshot.TreadmillTelemetry.InclinePercent);
      Assert.Equal((ushort)142, snapshot.HeartRateBpm);
      Assert.Equal((byte)86, snapshot.SelectedHeartRateBatteryPercent);
      Assert.NotNull(snapshot.SelectedHeartRateBatteryObservedAt);
      Assert.True(snapshot.Treadmill.ConnectionGeneration > 0);
      Assert.True(snapshot.HeartRate.ConnectionGeneration > 0);
      Assert.NotNull(snapshot.ReportedCapabilities!.SpeedRange);
      Assert.False(snapshot.ReportedCapabilities.CanStartRemotely);

      VersionedDeviceEnrollment? observed = null;
      using var evidenceTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (observed?.Enrollment.Evidence != TreadmillCapabilityEvidence.PassivelyObserved)
      {
        observed = await store.FindActiveAsync(DeviceRole.Treadmill, evidenceTimeout.Token);
        if (observed?.Enrollment.Evidence != TreadmillCapabilityEvidence.PassivelyObserved)
        {
          await Task.Delay(25, evidenceTimeout.Token);
        }
      }

      Assert.NotNull(observed.Enrollment.LastVerifiedAtUtc);
      Assert.NotNull(observed.Enrollment.Capabilities!.SpeedRange);
      Assert.False(observed.Enrollment.Capabilities.CanStartRemotely);

      long generationBeforeCapabilityUpdate = coordinator.Current.Treadmill.ConnectionGeneration;
      await store.UpdateEvidenceAsync(
        observed.Enrollment.Id,
        observed.Version,
        observed.Enrollment.ModelNumber,
        observed.Enrollment.FirmwareRevision,
        observed.Enrollment.Capabilities with { CanStartRemotely = true },
        TreadmillCapabilityEvidence.HardwareVerified,
        DateTimeOffset.UtcNow);
      await Task.Delay(TimeSpan.FromMilliseconds(2250));

      Assert.Equal(generationBeforeCapabilityUpdate, coordinator.Current.Treadmill.ConnectionGeneration);
      Assert.Equal(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);

      await using (TreadmillRunnerDbContext changed = await _factory.CreateDbContextAsync())
      {
        await changed.Database.ExecuteSqlRawAsync(
          "UPDATE DeviceEnrollments SET DeviceId = 'A9B8C7D6E5F4', Version = Version + 1 WHERE Role = 'Treadmill' AND IsArchived = 0;");
      }
      await coordinator.RefreshAsync();
      using var refreshTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready ||
             coordinator.Current.Treadmill.ConnectionGeneration <= generationBeforeCapabilityUpdate)
      {
        await Task.Delay(25, refreshTimeout.Token);
      }

      Assert.True(coordinator.Current.Treadmill.ConnectionGeneration > generationBeforeCapabilityUpdate);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  private static DeviceEnrollment Treadmill() => new(
    Guid.NewGuid(), DeviceRole.Treadmill, "A1B2C3D4E5F6", "horizon-omega-z", new string('a', 64),
    "Horizon Omega Z", null, null, TreadmillTelemetryMode.Ftms,
    new TreadmillCapabilities(), TreadmillCapabilityEvidence.Unknown, null);

  private static DeviceEnrollment HeartRate() => HeartRate("102030405060", "Polar H10");

  private static DeviceEnrollment HeartRate(string id, string name) => new(
    Guid.NewGuid(), DeviceRole.HeartRate, id, "bluetooth-heart-rate", new string('b', 64),
    name, null, null, null, null, TreadmillCapabilityEvidence.Unknown, null);

  private static PersistenceWriteOperation Op(string type, DateTimeOffset now) => new(
    Guid.NewGuid(), type, 200, "{}", now, new string('0', 64));

  private sealed class ScriptedBleTransport : IBleCentralTransport
  {
    private static readonly Guid Ftms = Expand(0x1826);
    private static readonly Guid Feature = Expand(0x2ACC);
    private static readonly Guid TreadmillData = Expand(0x2ACD);
    private static readonly Guid SpeedRange = Expand(0x2AD4);
    private static readonly Guid InclineRange = Expand(0x2AD5);
    private static readonly Guid ControlPoint = Expand(0x2AD9);
    private static readonly Guid HeartRateService = Expand(0x180D);
    private static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    private static readonly Guid BatteryService = Expand(0x180F);
    private static readonly Guid BatteryLevel = Expand(0x2A19);

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.Yield();
      yield break;
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult<IBleConnection>(new Connection(deviceId));

    private sealed class Connection(string deviceId) : IBleConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default)
      {
        IReadOnlyList<BleService> result = DeviceId.StartsWith('A')
          ? [new BleService(Ftms,
          [
            new BleCharacteristic(Ftms, Feature, true, false, false),
            new BleCharacteristic(Ftms, SpeedRange, true, false, false),
            new BleCharacteristic(Ftms, InclineRange, true, false, false),
            new BleCharacteristic(Ftms, ControlPoint, false, true, true),
            new BleCharacteristic(Ftms, TreadmillData, false, false, true),
          ])]
          :
          [
            new BleService(HeartRateService,
              [new BleCharacteristic(HeartRateService, HeartRateMeasurement, false, false, true)]),
            new BleService(BatteryService,
              [new BleCharacteristic(BatteryService, BatteryLevel, true, false, true)]),
          ];
        return ValueTask.FromResult(result);
      }

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default)
      {
        byte[] value = characteristicUuid == BatteryLevel
          ? [87]
          : characteristicUuid == Feature
          ? [0, 0, 0, 0, 3, 0, 0, 0]
          : characteristicUuid == SpeedRange
            ? [0, 0, 0xD0, 0x07, 10, 0]
            : [0, 0, 0xC8, 0, 1, 0];
        return ValueTask.FromResult<ReadOnlyMemory<byte>>(value);
      }

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        await Task.Yield();
        byte[] value = characteristicUuid == BatteryLevel
          ? [86]
          : characteristicUuid == TreadmillData
          ? [0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00]
          : [0x00, DeviceId.Contains("GARMIN", StringComparison.Ordinal) ? (byte)135 : (byte)142];
        yield return new BleNotification(serviceUuid, characteristicUuid, value, DateTimeOffset.UtcNow);
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }
}
