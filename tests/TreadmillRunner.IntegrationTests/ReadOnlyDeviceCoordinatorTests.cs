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
using TreadmillRunner.Infrastructure.Bluetooth;

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
    var transport = new ScriptedBleTransport();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(marc.Id, requiresHeartRate: false);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.CurrentForProfile(marc.Id) is not
        { HeartRateSources.Count: 2, SelectedHeartRateEnrollmentId: not null })
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
    var transport = new ScriptedBleTransport();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.RefreshAsync();
      Assert.Equal(DeviceConnectionState.Disconnected, coordinator.Current.Treadmill.State);
      Assert.Null(coordinator.Current.TreadmillTelemetry);

      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current is not
        {
          TreadmillTelemetry: not null,
          HeartRateBpm: not null,
          SelectedHeartRateBatteryPercent: 86,
          ReportedCapabilities: { SpeedRange: not null },
        })
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
      Assert.NotNull(transport.FirstHeartRateNotificationAt);
      Assert.NotNull(transport.FirstBatterySubscriptionAt);
      Assert.True(transport.FirstHeartRateNotificationAt <= transport.FirstBatterySubscriptionAt);
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
      Assert.Equal("OMEGA Z", observed.Enrollment.ModelNumber);
      Assert.Equal("V10.23.17", observed.Enrollment.FirmwareRevision);
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

      await coordinator.ReleaseRunConnectionsAsync();
      Assert.Equal(DeviceConnectionState.Disconnected, coordinator.Current.Treadmill.State);
      Assert.Equal(DeviceConnectionState.Disconnected, coordinator.Current.HeartRate.State);
      Assert.Null(coordinator.Current.TreadmillTelemetry);
      Assert.Null(coordinator.Current.HeartRateBpm);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Passively_rediscovers_an_enrolled_device_before_retrying_a_windows_cache_miss()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill();
    await store.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new CacheDependentBleTransport(treadmill.DeviceId);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(7));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
      {
        await Task.Delay(25, timeout.Token);
      }

      Assert.True(transport.PassiveScanCount >= 1);
      Assert.True(transport.ConnectionAttemptCount >= 2);
      Assert.NotNull(coordinator.Current.TreadmillTelemetry);

      long generation = coordinator.Current.Treadmill.ConnectionGeneration;
      int attempts = transport.ConnectionAttemptCount;
      Assert.True(await coordinator.RetryConnectionAsync(treadmill.Id));
      using var retryTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready ||
             coordinator.Current.Treadmill.ConnectionGeneration <= generation)
      {
        await Task.Delay(25, retryTimeout.Token);
      }
      Assert.True(transport.ConnectionAttemptCount > attempts);

      int attemptsBeforeDisconnect = transport.ConnectionAttemptCount;
      Assert.True(await coordinator.DisconnectAsync(treadmill.Id));
      Assert.Equal(DeviceConnectionState.Disconnected, coordinator.Current.Treadmill.State);
      await Task.Delay(TimeSpan.FromMilliseconds(2250));
      Assert.Equal(attemptsBeforeDisconnect, transport.ConnectionAttemptCount);

      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var reconnectTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
      {
        await Task.Delay(25, reconnectTimeout.Token);
      }
      Assert.True(transport.ConnectionAttemptCount > attemptsBeforeDisconnect);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Marks_heart_rate_contact_loss_unavailable_without_publishing_a_pulse()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(HeartRate(), now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { HeartRateNotificationValue = [0x04, 142] };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: true);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (true)
      {
        DeviceTelemetrySnapshot current = coordinator.Current;
        if (current.HeartRateSources is { Count: 1 } sources &&
            sources[0].Quality == HeartRateSignalQuality.ContactLost) break;
        await Task.Delay(25, timeout.Token);
      }

      DeviceTelemetrySnapshot snapshot = coordinator.Current;
      Assert.Null(snapshot.HeartRateBpm);
      Assert.Equal(HeartRateSignalQuality.ContactLost, snapshot.SelectedHeartRateQuality);
      Assert.Equal(HeartRateContactState.NotDetected, snapshot.SelectedHeartRateContactState);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Theory]
  [InlineData(29)]
  [InlineData(251)]
  public async Task Invalid_heart_rate_values_are_observed_but_never_published(int beatsPerMinute)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(HeartRate(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { HeartRateNotificationValue = [0x00, checked((byte)beatsPerMinute)] };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: true);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.HeartRateSources is not { Count: 1 } sources ||
             sources[0].Quality != HeartRateSignalQuality.Invalid)
        await Task.Delay(25, timeout.Token);

      DeviceTelemetrySnapshot snapshot = coordinator.Current;
      Assert.Null(snapshot.HeartRateBpm);
      Assert.Null(Assert.Single(snapshot.HeartRateSources!).BeatsPerMinute);
      Assert.Equal(HeartRateSignalQuality.Invalid, snapshot.SelectedHeartRateQuality);
      Assert.Equal(HeartRateContactState.NotSupported, snapshot.SelectedHeartRateContactState);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Stale_heart_rate_observation_keeps_diagnostics_but_removes_the_pulse()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(HeartRate(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      HeartRateNotificationValue = [0x00, 142],
      HeartRateObservedAt = now.AddSeconds(-10),
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: true);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.HeartRateSources is not { Count: 1 } sources ||
             sources[0].ObservedAt is null)
        await Task.Delay(25, timeout.Token);

      DeviceTelemetrySnapshot snapshot = coordinator.Current;
      HeartRateSourceSnapshot source = Assert.Single(snapshot.HeartRateSources!);
      Assert.Equal(HeartRateSignalQuality.Valid, source.Quality);
      Assert.NotNull(source.ObservedAt);
      Assert.Null(source.BeatsPerMinute);
      Assert.Null(snapshot.HeartRateBpm);
      Assert.Null(snapshot.SelectedHeartRateEnrollmentId);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Empty_ftms_packet_does_not_publish_ready_or_synthesize_motion_telemetry()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      TreadmillNotificationValues = [[0x01, 0x00], [0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00]],
      ReleaseAdditionalTreadmillNotifications = new(TaskCreationOptions.RunContinuationsAsynchronously),
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstTreadmillNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      Assert.NotEqual(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
      Assert.Null(coordinator.Current.TreadmillTelemetry);

      transport.ReleaseAdditionalTreadmillNotifications.SetResult();
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);
      Assert.Equal(6, coordinator.Current.TreadmillTelemetry?.SpeedKph);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Omitted_incline_does_not_refresh_its_observation_time()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      TreadmillNotificationValues = [[0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00], [0x00, 0x00, 0xBC, 0x02]],
      ReleaseAdditionalTreadmillNotifications = new(TaskCreationOptions.RunContinuationsAsynchronously),
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstTreadmillNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      TreadmillTelemetry first = Assert.IsType<TreadmillTelemetry>(coordinator.Current.TreadmillTelemetry);
      transport.ReleaseAdditionalTreadmillNotifications.SetResult();
      await transport.AllTreadmillNotificationsConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      TreadmillTelemetry second = Assert.IsType<TreadmillTelemetry>(coordinator.Current.TreadmillTelemetry);

      Assert.Equal(7, second.SpeedKph);
      Assert.True(second.SpeedObservedAt > first.SpeedObservedAt);
      Assert.Equal(first.InclineObservedAt, second.InclineObservedAt);
      Assert.Equal(first.InclinePercent, second.InclinePercent);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Implausible_ftms_speed_faults_the_device_instead_of_clamping()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { TreadmillNotificationValues = [[0x00, 0x00, 0xFF, 0xFF]] };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstTreadmillNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      Assert.Equal(DeviceConnectionState.Faulted, coordinator.Current.Treadmill.State);
      Assert.Contains("implausible", coordinator.Current.Treadmill.Fault, StringComparison.OrdinalIgnoreCase);
      Assert.Null(coordinator.Current.TreadmillTelemetry);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Implausible_ftms_sample_clears_previous_telemetry_and_keeps_device_faulted()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      TreadmillNotificationValues =
      [
        [0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00],
        [0x00, 0x00, 0xFF, 0xFF],
      ],
      ReleaseAdditionalTreadmillNotifications = new(TaskCreationOptions.RunContinuationsAsynchronously),
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System,
      new ApplicationMaintenanceState(),
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstTreadmillNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      Assert.Equal(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
      Assert.NotNull(coordinator.Current.TreadmillTelemetry);

      transport.ReleaseAdditionalTreadmillNotifications.SetResult();
      await transport.AllTreadmillNotificationsConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      Assert.Equal(DeviceConnectionState.Faulted, coordinator.Current.Treadmill.State);
      Assert.Contains("implausible", coordinator.Current.Treadmill.Fault, StringComparison.OrdinalIgnoreCase);
      Assert.Null(coordinator.Current.TreadmillTelemetry);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Shutdown_does_not_wait_for_blocked_evidence_or_reliability_persistence()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    await store.EnrollAsync(Treadmill(), now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport();
    transport.DisconnectAfterFirstTreadmillNotification = true;
    await using var broker = new BleAdvertisementBroker(
      transport,
      NullLogger<BleAdvertisementBroker>.Instance);
    var maintenance = new BlockingMaintenanceState();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(),
      transport,
      broker,
      TimeProvider.System,
      maintenance,
      NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var mutationTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (maintenance.MutationAttempts == 0 || !transport.TreadmillFailureObserved)
      {
        await Task.Delay(25, mutationTimeout.Token);
      }

      Task stop = coordinator.StopAsync(CancellationToken.None);
      try
      {
        await stop.WaitAsync(TimeSpan.FromSeconds(2));
      }
      catch
      {
        maintenance.AllowMutations();
        await stop.WaitAsync(TimeSpan.FromSeconds(2));
        throw;
      }
    }
    finally
    {
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

  private sealed class BlockingMaintenanceState : IApplicationMaintenanceState
  {
    private int _mutationAttempts;
    private volatile bool _allowMutations;

    public int MutationAttempts => Volatile.Read(ref _mutationAttempts);

    public bool IsActive => false;

    public bool TryBegin() => false;

    public void End() { }

    public bool TryBeginMutation()
    {
      Interlocked.Increment(ref _mutationAttempts);
      return _allowMutations;
    }

    public void EndMutation() { }

    public void AllowMutations() => _allowMutations = true;
  }

  private sealed class ScriptedBleTransport : IBleCentralTransport
  {
    private static readonly Guid Ftms = Expand(0x1826);
    private static readonly Guid Feature = Expand(0x2ACC);
    private static readonly Guid TreadmillData = Expand(0x2ACD);
    private static readonly Guid SpeedRange = Expand(0x2AD4);
    private static readonly Guid InclineRange = Expand(0x2AD5);
    private static readonly Guid ControlPoint = Expand(0x2AD9);
    private static readonly Guid DeviceInformationService = Expand(0x180A);
    private static readonly Guid ModelNumber = Expand(0x2A24);
    private static readonly Guid FirmwareRevision = Expand(0x2A26);
    private static readonly Guid HeartRateService = Expand(0x180D);
    private static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    private static readonly Guid BatteryService = Expand(0x180F);
    private static readonly Guid BatteryLevel = Expand(0x2A19);

    public DateTimeOffset? FirstHeartRateNotificationAt { get; private set; }
    public DateTimeOffset? FirstBatterySubscriptionAt { get; private set; }
    public bool DisconnectAfterFirstTreadmillNotification { get; set; }
    public byte[] HeartRateNotificationValue { get; set; } = [0x00, 142];
    public DateTimeOffset? HeartRateObservedAt { get; set; }
    public IReadOnlyList<byte[]> TreadmillNotificationValues { get; set; } = [[0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00]];
    public TaskCompletionSource? ReleaseAdditionalTreadmillNotifications { get; set; }
    public TaskCompletionSource FirstTreadmillNotificationConsumed { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource AllTreadmillNotificationsConsumed { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int _treadmillFailureObserved;
    public bool TreadmillFailureObserved => Volatile.Read(ref _treadmillFailureObserved) != 0;

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.Yield();
      yield break;
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult<IBleConnection>(new Connection(deviceId, this));

    private sealed class Connection(string deviceId, ScriptedBleTransport owner) : IBleConnection
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
          ]),
          new BleService(DeviceInformationService,
          [
            new BleCharacteristic(DeviceInformationService, ModelNumber, true, false, false),
            new BleCharacteristic(DeviceInformationService, FirmwareRevision, true, false, false),
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
          : characteristicUuid == ModelNumber
            ? System.Text.Encoding.UTF8.GetBytes("OMEGA Z")
          : characteristicUuid == FirmwareRevision
            ? System.Text.Encoding.UTF8.GetBytes("V10.23.17")
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
        if (characteristicUuid == HeartRateMeasurement)
          owner.FirstHeartRateNotificationAt ??= DateTimeOffset.UtcNow;
        if (characteristicUuid == BatteryLevel)
          owner.FirstBatterySubscriptionAt ??= DateTimeOffset.UtcNow;
        if (characteristicUuid == TreadmillData)
        {
          for (int index = 0; index < owner.TreadmillNotificationValues.Count; index++)
          {
            yield return new BleNotification(serviceUuid, characteristicUuid, owner.TreadmillNotificationValues[index], DateTimeOffset.UtcNow);
            if (index == 0) owner.FirstTreadmillNotificationConsumed.TrySetResult();
            if (characteristicUuid == TreadmillData && owner.DisconnectAfterFirstTreadmillNotification)
            {
              Volatile.Write(ref owner._treadmillFailureObserved, 1);
              throw new WindowsBleDisconnectedException();
            }
            if (index == 0 && owner.ReleaseAdditionalTreadmillNotifications is not null)
              await owner.ReleaseAdditionalTreadmillNotifications.Task.WaitAsync(cancellationToken);
          }
          owner.AllTreadmillNotificationsConsumed.TrySetResult();
          await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
          yield break;
        }

        byte[] value = characteristicUuid == BatteryLevel ? [86] : owner.HeartRateNotificationValue;
        DateTimeOffset observedAt = characteristicUuid == HeartRateMeasurement
          ? owner.HeartRateObservedAt ?? DateTimeOffset.UtcNow
          : DateTimeOffset.UtcNow;
        yield return new BleNotification(serviceUuid, characteristicUuid, value, observedAt);
        if (characteristicUuid == TreadmillData && owner.DisconnectAfterFirstTreadmillNotification)
        {
          Volatile.Write(ref owner._treadmillFailureObserved, 1);
          throw new WindowsBleDisconnectedException();
        }
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class CacheDependentBleTransport(string deviceId) : IBleCentralTransport
  {
    private readonly ScriptedBleTransport _connected = new();
    private bool _observed;

    public int PassiveScanCount { get; private set; }

    public int ConnectionAttemptCount { get; private set; }

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      PassiveScanCount++;
      await Task.Yield();
      cancellationToken.ThrowIfCancellationRequested();
      _observed = true;
      yield return new BleAdvertisement(
        deviceId,
        null,
        -45,
        [Guid.Parse("00001826-0000-1000-8000-00805f9b34fb")]);
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string requestedDeviceId,
      CancellationToken cancellationToken = default)
    {
      ConnectionAttemptCount++;
      return _observed
        ? _connected.ConnectAsync(requestedDeviceId, cancellationToken)
        : ValueTask.FromResult<IBleConnection>(new CacheMissConnection(requestedDeviceId));
    }

    private sealed class CacheMissConnection(string requestedDeviceId) : IBleConnection
    {
      public string DeviceId { get; } = requestedDeviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) =>
        ValueTask.FromException<IReadOnlyList<BleService>>(
          new WindowsBleDeviceUnavailableException());

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) =>
        throw new NotSupportedException();

      public IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) =>
        throw new NotSupportedException();
    }
  }
}
