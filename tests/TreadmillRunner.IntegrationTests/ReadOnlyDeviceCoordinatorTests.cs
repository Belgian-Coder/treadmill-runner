using System.Runtime.CompilerServices;
using System.Diagnostics;
using System.Collections.Concurrent;
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

  [Fact]
  public async Task Gatt_timeout_cancels_the_underlying_operation_without_waiting_for_late_completion()
  {
    var neverCompletes = new TaskCompletionSource<int>(
      TaskCreationOptions.RunContinuationsAsynchronously);
    CancellationToken operationToken = default;
    long started = Stopwatch.GetTimestamp();

    await Assert.ThrowsAsync<TimeoutException>(() =>
      ReadOnlyDeviceCoordinator.AwaitGattOperationAsync(
        cancellationToken =>
        {
          operationToken = cancellationToken;
          return neverCompletes.Task;
        },
        TimeSpan.FromMilliseconds(25),
        TimeProvider.System,
        CancellationToken.None));

    Assert.True(operationToken.IsCancellationRequested);
    Assert.True(Stopwatch.GetElapsedTime(started) < TimeSpan.FromSeconds(2));
  }

  [Fact]
  public async Task Subscription_disposal_is_bounded_when_the_native_iterator_ignores_cancellation()
  {
    var subscription = new NonCooperativeAsyncDisposable();
    long started = Stopwatch.GetTimestamp();

    await ReadOnlyDeviceCoordinator.DisposeSubscriptionBoundedAsync(
      subscription,
      TimeSpan.FromMilliseconds(25),
      TimeProvider.System);

    Assert.True(subscription.DisposeCalled);
    Assert.True(Stopwatch.GetElapsedTime(started) < TimeSpan.FromSeconds(2));
  }

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "coordinator.db"));
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  [Fact]
  public async Task Connects_only_the_active_runners_healthy_preferred_source()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var marc = new UserProfile(Guid.NewGuid(), "Marc", UnitSystem.Metric, 75, 190, 18, []);
    var wife = new UserProfile(Guid.NewGuid(), "Runner 2", UnitSystem.Metric, 65, 185, 16, []);
    await profiles.CreateAsync(marc, now, Op("profile.create", now));
    await profiles.CreateAsync(wife, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN", "Garmin fÄ“nix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(marc.Id, 5, true, true), new HeartRateAssignmentPreference(wife.Id, 0, true, true)],
      now, Op("device.enroll", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(marc.Id, 0, true, false)],
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
      while (coordinator.CurrentForProfile(marc.Id).SelectedHeartRateEnrollmentId is null)
      {
        await Task.Delay(25, timeout.Token);
      }
      DeviceTelemetrySnapshot marcStatus = coordinator.CurrentForProfile(marc.Id);
      DeviceTelemetrySnapshot wifeStatus = coordinator.CurrentForProfile(wife.Id);
      Assert.Equal(2, marcStatus.HeartRateSources!.Count);
      Assert.Equal(polar.Id, marcStatus.SelectedHeartRateEnrollmentId);
      Assert.Equal(polar.Id, wifeStatus.SelectedHeartRateEnrollmentId);
      Assert.Equal(HeartRateDeviceFamily.Polar, marcStatus.SelectedHeartRateDeviceFamily);
      Assert.Contains(polar.DeviceId, transport.ConnectionDeviceIds);
      Assert.DoesNotContain(garmin.DeviceId, transport.ConnectionDeviceIds);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Activates_the_next_assigned_source_after_the_preferred_source_fails()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var runner = new UserProfile(Guid.NewGuid(), "Runner", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(runner, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR-FAIL", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN-FALLBACK", "Garmin fÄ“nix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(runner.Id, 5, true, true)],
      now, Op("device.enroll", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(runner.Id, 0, true, false)],
      now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport();
    transport.UnavailableDeviceIds.TryAdd(polar.DeviceId, 0);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(runner.Id, requiresHeartRate: true);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(7));
      while (coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId != garmin.Id)
        await Task.Delay(25, timeout.Token);

      Assert.Contains(polar.DeviceId, transport.ConnectionDeviceIds);
      Assert.Contains(garmin.DeviceId, transport.ConnectionDeviceIds);
      Assert.Equal((ushort)142, coordinator.CurrentForProfile(runner.Id).HeartRateBpm);

      Assert.True(transport.UnavailableDeviceIds.TryRemove(polar.DeviceId, out _));
      using var recoveryTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
      while (coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId != polar.Id)
      {
        await Task.Delay(25, recoveryTimeout.Token);
      }
      Assert.Equal(DeviceConnectionState.Ready, coordinator.CurrentForProfile(runner.Id).HeartRateSources?
        .Single(source => source.EnrollmentId == garmin.Id).State);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Warms_the_first_assigned_fallback_before_the_preferred_source_fails()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var runner = new UserProfile(Guid.NewGuid(), "Runner", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(runner, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR-WARM", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN-WARM", "Garmin fenix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(runner.Id, 0, true, true)],
      now, Op("device.enroll.polar", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(runner.Id, 1, true, false)],
      now, Op("device.enroll.garmin", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new FallbackHysteresisBleTransport(polar.DeviceId, garmin.DeviceId)
    {
      HoldPrimaryUnavailableDiscovery = true,
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(runner.Id, requiresHeartRate: true);
      await transport.PrimaryDiscoveryStarted.Task.WaitAsync(TimeSpan.FromSeconds(3));
      await transport.FallbackConnectionStarted.Task.WaitAsync(TimeSpan.FromSeconds(3));
      Assert.Equal(1, transport.FallbackConnectionCount);

      transport.ReleasePrimaryDiscovery.TrySetResult();
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(7));
      while (coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId != garmin.Id)
        await Task.Delay(25, timeout.Token);

      Assert.Equal(1, transport.FallbackConnectionCount);
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);
    }
    finally
    {
      transport.ReleasePrimaryDiscovery.TrySetResult();
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Keeps_connected_fallback_through_one_sample_primary_recovery_and_loss()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var runner = new UserProfile(Guid.NewGuid(), "Runner", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(runner, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR-HYSTERESIS", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN-HYSTERESIS", "Garmin fenix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(runner.Id, 0, true, true)],
      now, Op("device.enroll", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(runner.Id, 1, true, false)],
      now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new FallbackHysteresisBleTransport(polar.DeviceId, garmin.DeviceId);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(runner.Id, requiresHeartRate: true);
      using var fallbackTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
      while (coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId != garmin.Id)
        await Task.Delay(25, fallbackTimeout.Token);

      transport.PrimaryAvailable = true;
      await transport.PrimarySampleObserved.Task.WaitAsync(TimeSpan.FromSeconds(12));
      await coordinator.RefreshAsync();

      HeartRateSourceSnapshot recoveredPrimaryFallback = Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!, source => source.EnrollmentId == garmin.Id);
      Assert.Equal(DeviceConnectionState.Ready, recoveredPrimaryFallback.State);
      Assert.Equal(1, transport.FallbackConnectionCount);

      transport.ReleasePrimaryFailure.SetResult();
      using var lossTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.ActiveReliabilityFailureCount(polar.Id) == 0)
        await Task.Delay(25, lossTimeout.Token);
      await coordinator.RefreshAsync();

      Assert.Equal(garmin.Id, coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId);
      Assert.Equal(1, transport.FallbackConnectionCount);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Sparse_samples_keep_the_warm_fallback_connected_after_preferred_recovery_stabilizes()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var clock = new AdjustableTimeProvider(now);
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var runner = new UserProfile(Guid.NewGuid(), "Runner", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(runner, now, Op("profile.create", now));
    DeviceEnrollment polar = HeartRate("POLAR-STABLE-RECOVERY", "Polar H10");
    DeviceEnrollment garmin = HeartRate("GARMIN-STABLE-FALLBACK", "Garmin fenix 8");
    await store.EnrollWithAssignmentsAsync(polar,
      [new HeartRateAssignmentPreference(runner.Id, 0, true, true)],
      now, Op("device.enroll", now));
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(runner.Id, 1, true, false)],
      now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new FallbackHysteresisBleTransport(polar.DeviceId, garmin.DeviceId, clock)
    {
      CompleteStableRecovery = true,
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      clock, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(runner.Id, requiresHeartRate: true);
      using var fallbackTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
      while (coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId != garmin.Id)
        await Task.Delay(25, fallbackTimeout.Token);

      transport.PrimaryAvailable = true;
      await transport.PrimarySampleObserved.Task.WaitAsync(TimeSpan.FromSeconds(12));
      await coordinator.RefreshAsync();
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);

      transport.ReleaseStablePrimarySample.SetResult();
      await transport.SparsePrimarySampleObserved.Task.WaitAsync(TimeSpan.FromSeconds(2));
      await coordinator.RefreshAsync();
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);

      transport.ReleaseContinuousPrimarySamples.SetResult();
      await transport.PrimaryStableSampleObserved.Task.WaitAsync(TimeSpan.FromSeconds(2));
      await coordinator.RefreshAsync();

      Assert.Equal(polar.Id, coordinator.CurrentForProfile(runner.Id).SelectedHeartRateEnrollmentId);
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);
      Assert.Equal(1, transport.FallbackConnectionCount);

      clock.Set(clock.GetUtcNow().AddSeconds(6));
      await coordinator.RefreshAsync();
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);
      Assert.Equal(1, transport.FallbackConnectionCount);

      transport.ReleasePostStablePrimarySample.SetResult();
      await transport.PostStablePrimarySampleObserved.Task.WaitAsync(TimeSpan.FromSeconds(2));
      await coordinator.RefreshAsync();
      Assert.Equal(DeviceConnectionState.Ready, Assert.Single(
        coordinator.CurrentForProfile(runner.Id).HeartRateSources!,
        source => source.EnrollmentId == garmin.Id).State);
      Assert.Equal(1, transport.FallbackConnectionCount);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Does_not_auto_connect_a_disabled_non_polar_assignment_but_manual_retry_still_connects()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    var runner = new UserProfile(Guid.NewGuid(), "Runner", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(runner, now, Op("profile.create", now));
    DeviceEnrollment garmin = HeartRate("GARMIN-DISABLED", "Garmin fenix 8");
    await store.EnrollWithAssignmentsAsync(garmin,
      [new HeartRateAssignmentPreference(runner.Id, 0, false, true)],
      now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(runner.Id, requiresHeartRate: true);
      await coordinator.RefreshAsync();
      await Task.Delay(100);
      Assert.DoesNotContain(garmin.DeviceId, transport.ConnectionDeviceIds);

      Assert.True(await coordinator.RetryConnectionAsync(garmin.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (!transport.ConnectionDeviceIds.Contains(garmin.DeviceId))
        await Task.Delay(25, timeout.Token);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Optional_targeted_discovery_cannot_block_required_heart_rate_subscription()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("POLAR-OPTIONAL-DISCOVERY", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new HangingOptionalDiscoveryBleTransport();
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
      while (coordinator.Current.HeartRate.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);

      Assert.Equal((ushort)142, coordinator.Current.HeartRateBpm);
      Assert.True(transport.OptionalDiscoveryStarted.Task.IsCompleted);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Short_and_sparse_flaps_escalate_but_continuous_stability_resets_backoff_after_delayed_failure_detection()
  {
    DateTimeOffset began = DateTimeOffset.UtcNow;
    var clock = new AdjustableTimeProvider(began);
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill();
    await store.EnrollAsync(treadmill, began, Op("device.enroll", began));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new BackoffSequenceBleTransport(clock);
    string journalDirectory = Path.Combine(_directory, "backoff-journal");
    using var journal = new BleDiagnosticJournal(journalDirectory, NullLogger<BleDiagnosticJournal>.Instance);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      clock, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance, journal);

    await journal.StartAsync(CancellationToken.None);
    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(12));
      while (transport.ConnectionAttemptCount < 4)
        await Task.Delay(25, timeout.Token);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      await journal.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }

    string path = Path.Combine(journalDirectory, "bluetooth.jsonl");
    var retrySeconds = new List<double>();
    foreach (string line in await File.ReadAllLinesAsync(path))
    {
      using System.Text.Json.JsonDocument document = System.Text.Json.JsonDocument.Parse(line);
      System.Text.Json.JsonElement entry = document.RootElement.GetProperty("Event");
      if (entry.GetProperty("Phase").GetString() == "attempt-failed" && retrySeconds.Count < 3)
        retrySeconds.Add(entry.GetProperty("RetrySeconds").GetDouble());
    }
    var policy = new BleReconnectPolicy();
    Assert.Equal(3, retrySeconds.Count);
    Assert.Equal(policy.GetDelay(treadmill.Id, 1).TotalSeconds, retrySeconds[0], 3);
    Assert.Equal(policy.GetDelay(treadmill.Id, 2).TotalSeconds, retrySeconds[1], 3);
    Assert.Equal(policy.GetDelay(treadmill.Id, 1).TotalSeconds, retrySeconds[2], 3);
  }

  [Fact]
  public async Task Publishes_heart_rate_before_optional_device_information_reads_finish()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("POLAR-BLOCKED-INFO", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { BlockHeartRateDeviceInformationReads = true };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
      while (coordinator.Current.HeartRate.State != DeviceConnectionState.Ready ||
             !transport.DeviceInformationReadStarted.Task.IsCompleted)
        await Task.Delay(25, timeout.Token);

      Assert.Equal((ushort)142, coordinator.Current.HeartRateBpm);
      Assert.True(transport.DeviceInformationReadStarted.Task.IsCompleted);
      Assert.False(transport.DeviceInformationReadCompleted.Task.IsCompleted);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Resubscribes_after_native_disconnect_without_waiting_for_optional_reads()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("POLAR-RECONNECT-INFO", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      BlockHeartRateDeviceInformationReads = true,
      DisconnectFirstHeartRateSubscription = true,
    };
    string journalDirectory = Path.Combine(_directory, "diagnostics");
    using var journal = new BleDiagnosticJournal(journalDirectory, NullLogger<BleDiagnosticJournal>.Instance);
    await journal.StartAsync(CancellationToken.None);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance, journal);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(7));
      while (transport.HeartRateSubscriptionCount < 2 ||
             coordinator.Current.HeartRate.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);

      Assert.Equal((ushort)142, coordinator.Current.HeartRateBpm);
      Assert.True(transport.ConnectionDeviceIds.Count >= 2);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
      await journal.StopAsync(CancellationToken.None);
    }
    string evidence = await File.ReadAllTextAsync(Path.Combine(journalDirectory, "bluetooth.jsonl"));
    Assert.Contains("NativeDisconnected", evidence);
    Assert.Contains("first-valid-reading", evidence);
    Assert.Contains("rediscovery-window-ended", evidence);
    Assert.Contains(heartRate.Id.ToString(), evidence);
    Assert.DoesNotContain("POLAR-RECONNECT-INFO", evidence);
  }

  [Fact]
  public async Task Uses_cached_heart_rate_locator_first_but_scans_after_a_device_unavailable_failure()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("POLAR-CACHE-FIRST", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport();
    transport.UnavailableDeviceIds.TryAdd(heartRate.DeviceId, 0);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: true);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (transport.ActiveScanCount == 0)
        await Task.Delay(25, timeout.Token);

      string[] operations = transport.OperationEvents.ToArray();
      Assert.NotEmpty(operations);
      Assert.Equal($"connect:{heartRate.DeviceId}", operations[0]);
      Assert.Contains("scan", operations);
      Assert.Equal(heartRate.DeviceId, transport.ConnectionDeviceIds.First());
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Retries_a_durably_stable_heart_rate_locator_before_scanning()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("POLAR-STABLE-CACHED-RETRY", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      heartRate.DeviceId,
      currentDeviceId: heartRate.DeviceId,
      advertiseStoredAddressFirst: true,
      disconnectAfterStableCurrentConnection: true);
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
      while (transport.ConnectionDeviceIds.Count < 2)
        await Task.Delay(25, timeout.Token);

      Assert.Equal(2, transport.ConnectionDeviceIds.Count);
      Assert.All(transport.ConnectionDeviceIds, id => Assert.Equal(heartRate.DeviceId, id));
      Assert.Equal(0, transport.ActiveScanCount);
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
  public async Task Continues_treadmill_telemetry_when_optional_device_information_read_fails()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill();
    await store.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { BlockTreadmillDeviceInformationReads = true };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstTreadmillNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);

      Assert.True(transport.TreadmillDeviceInformationReadAttempted.Task.IsCompleted);
      Assert.False(transport.TreadmillDeviceInformationReadCompleted.Task.IsCompleted);
      Assert.Equal(6, coordinator.Current.TreadmillTelemetry?.SpeedKph);
      Assert.Equal(1, coordinator.Current.TreadmillTelemetry?.InclinePercent);
      Assert.Equal(treadmill.ModelNumber, coordinator.Current.Treadmill.ModelNumber);
      Assert.Equal(treadmill.FirmwareRevision, coordinator.Current.Treadmill.FirmwareRevision);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Continues_vendor_treadmill_telemetry_when_optional_device_information_read_fails()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(TreadmillTelemetryMode.Vendor);
    await store.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      UseVendorTreadmill = true,
      ThrowTreadmillDeviceInformationReads = true,
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.FirstVendorNotificationConsumed.Task.WaitAsync(TimeSpan.FromSeconds(5));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);

      Assert.True(transport.TreadmillDeviceInformationReadAttempted.Task.IsCompleted);
      Assert.InRange(coordinator.Current.TreadmillTelemetry?.SpeedKph ?? 0, 9.99, 10.00);
      Assert.Equal(5.3, coordinator.Current.TreadmillTelemetry?.InclinePercent);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Does_not_publish_hardware_verified_treadmill_ready_while_identity_reads_are_blocked()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(
      TreadmillTelemetryMode.Ftms,
      TreadmillCapabilityEvidence.HardwareVerified);
    await store.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { BlockTreadmillDeviceInformationReads = true };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await transport.TreadmillDeviceInformationReadAttempted.Task.WaitAsync(TimeSpan.FromSeconds(5));
      await Task.Delay(250);

      Assert.NotEqual(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
      Assert.Null(coordinator.Current.TreadmillTelemetry);
      Assert.False(transport.FirstTreadmillNotificationConsumed.Task.IsCompleted);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Theory]
  [InlineData(TreadmillTelemetryMode.Ftms, false)]
  [InlineData(TreadmillTelemetryMode.Vendor, true)]
  public async Task Downgrades_hardware_verified_treadmill_when_fresh_identity_changes(
    TreadmillTelemetryMode telemetryMode,
    bool vendor)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var enrollmentStore = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(
      telemetryMode,
      TreadmillCapabilityEvidence.HardwareVerified,
      modelNumber: "OMEGA Z",
      firmwareRevision: "V10.23.17");
    await enrollmentStore.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var trackingStore = new EvidenceTrackingStore(enrollmentStore);
    trackingStore.ReleaseEvidenceWrite = new(TaskCreationOptions.RunContinuationsAsynchronously);
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore>(_ => trackingStore);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      UseVendorTreadmill = vendor,
      TreadmillModelNumber = "OMEGA Z 2",
      TreadmillFirmwareRevision = "V10.23.18",
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await trackingStore.EvidenceWriteStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
      Assert.NotEqual(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
      trackingStore.ReleaseEvidenceWrite!.TrySetResult();
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      VersionedDeviceEnrollment? observed = null;
      while (observed?.Enrollment.Evidence != TreadmillCapabilityEvidence.PassivelyObserved)
      {
        observed = await enrollmentStore.FindActiveAsync(DeviceRole.Treadmill, timeout.Token);
        if (observed?.Enrollment.Evidence != TreadmillCapabilityEvidence.PassivelyObserved)
          await Task.Delay(25, timeout.Token);
      }

      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);
      Assert.Equal("OMEGA Z 2", observed.Enrollment.ModelNumber);
      Assert.Equal("V10.23.18", observed.Enrollment.FirmwareRevision);
      if (vendor)
      {
        Assert.Equal(new TreadmillCapabilities(), observed.Enrollment.Capabilities);
      }
      else
      {
        Assert.NotNull(observed.Enrollment.Capabilities?.SpeedRange);
        Assert.False(observed.Enrollment.Capabilities!.CanStartRemotely);
      }
      Assert.Equal(1, trackingStore.EvidenceWriteCount);
      Assert.Equal(TreadmillCapabilityEvidence.PassivelyObserved, coordinator.Current.Treadmill.Evidence);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Retains_hardware_verified_treadmill_when_fresh_identity_matches()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var enrollmentStore = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(
      TreadmillTelemetryMode.Ftms,
      TreadmillCapabilityEvidence.HardwareVerified,
      modelNumber: "OMEGA Z",
      firmwareRevision: "V10.23.17");
    VersionedDeviceEnrollment original = await enrollmentStore.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var trackingStore = new EvidenceTrackingStore(enrollmentStore);
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore>(_ => trackingStore);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      TreadmillModelNumber = "OMEGA Z",
      TreadmillFirmwareRevision = "V10.23.17",
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.Treadmill.State != DeviceConnectionState.Ready)
        await Task.Delay(25, timeout.Token);

      VersionedDeviceEnrollment? observed = null;
      while (observed is null)
      {
        observed = await enrollmentStore.FindActiveAsync(DeviceRole.Treadmill, timeout.Token);
        if (observed is null) await Task.Delay(25, timeout.Token);
      }

      Assert.Equal(TreadmillCapabilityEvidence.HardwareVerified, observed.Enrollment.Evidence);
      Assert.Equal(TreadmillCapabilityEvidence.HardwareVerified, coordinator.Current.Treadmill.Evidence);
      Assert.Equal(original.Version, observed.Version);
      Assert.Equal(0, trackingStore.EvidenceWriteCount);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Does_not_publish_ready_when_hardware_identity_downgrade_cannot_be_persisted()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var enrollmentStore = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(
      TreadmillTelemetryMode.Ftms,
      TreadmillCapabilityEvidence.HardwareVerified,
      modelNumber: "OMEGA Z",
      firmwareRevision: "V10.23.17");
    await enrollmentStore.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var trackingStore = new EvidenceTrackingStore(enrollmentStore) { ThrowEvidenceWrites = true };
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore>(_ => trackingStore);
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport
    {
      TreadmillModelNumber = "OMEGA Z 2",
      TreadmillFirmwareRevision = "V10.23.18",
    };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await trackingStore.EvidenceWriteStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
      await Task.Delay(100);

      Assert.NotEqual(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Does_not_publish_hardware_verified_treadmill_ready_when_identity_service_is_missing()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment treadmill = Treadmill(
      TreadmillTelemetryMode.Ftms,
      TreadmillCapabilityEvidence.HardwareVerified,
      modelNumber: "OMEGA Z",
      firmwareRevision: "V10.23.17");
    await store.EnrollAsync(treadmill, now, Op("device.enroll", now));
    var services = new ServiceCollection().AddSingleton(_factory).AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new ScriptedBleTransport { OmitTreadmillDeviceInformationService = true };
    var coordinator = new ReadOnlyDeviceCoordinator(
      provider.GetRequiredService<IServiceScopeFactory>(), transport,
      new BleAdvertisementBroker(transport, NullLogger<BleAdvertisementBroker>.Instance),
      TimeProvider.System, new ApplicationMaintenanceState(), NullLogger<ReadOnlyDeviceCoordinator>.Instance);

    await coordinator.StartAsync(CancellationToken.None);
    try
    {
      await coordinator.PrepareForRunAsync(Guid.NewGuid(), requiresHeartRate: false);
      await Task.Delay(250);

      Assert.NotEqual(DeviceConnectionState.Ready, coordinator.Current.Treadmill.State);
      Assert.Null(coordinator.Current.TreadmillTelemetry);
      Assert.False(transport.FirstTreadmillNotificationConsumed.Task.IsCompleted);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Actively_rediscovers_an_enrolled_device_before_retrying_a_windows_cache_miss()
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

      Assert.True(transport.ActiveScanCount >= 1);
      Assert.True(transport.ConnectionAttemptCount >= 2);
      Assert.NotNull(coordinator.Current.TreadmillTelemetry);

      long generation = coordinator.Current.Treadmill.ConnectionGeneration;
      int attempts = transport.ConnectionAttemptCount;
      int scans = transport.ActiveScanCount;
      Assert.True(await coordinator.RetryConnectionAsync(treadmill.Id));
      Assert.True(coordinator.HasActiveConnectionDemand(treadmill.Id, DateTimeOffset.UtcNow));
      Assert.True(coordinator.HasActiveConnectionDemand(treadmill.Id, DateTimeOffset.UtcNow.AddDays(1)));
      await coordinator.ReleaseRunConnectionsAsync();
      Assert.True(coordinator.HasActiveConnectionDemand(treadmill.Id, DateTimeOffset.UtcNow.AddDays(1)));
      Assert.True(transport.ActiveScanCount > scans);
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
  public async Task Rebinds_a_returning_heart_rate_source_to_its_new_ble_address()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("102030405060", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      heartRate.DeviceId,
      currentDeviceId: "AABBCCDDEEFF",
      advertiseStoredAddressFirst: false);
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
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
      while (coordinator.Current.HeartRate.State != DeviceConnectionState.Ready)
      {
        await Task.Delay(25, timeout.Token);
      }

      Assert.Contains("AABBCCDDEEFF", transport.ConnectionDeviceIds);
      Assert.Equal((ushort)142, coordinator.Current.HeartRateBpm);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Does_not_rebind_from_a_truncated_fresh_scan_even_after_a_unique_candidate_was_seen()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("102030405060", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      heartRate.DeviceId,
      currentDeviceId: "AABBCCDDEEFF",
      advertiseStoredAddressFirst: false,
      throwAfterAdvertisement: true);
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
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      // This is an identity-safety assertion, not a three-second scheduling
      // benchmark. Await the observed attempt instead of polling during scans.
      string firstAttempt = await transport.FirstConnectionAttempt.WaitAsync(TimeSpan.FromSeconds(15));
      Assert.Equal(heartRate.DeviceId, firstAttempt);
      Assert.Contains(heartRate.DeviceId, transport.ConnectionDeviceIds);
      Assert.DoesNotContain("AABBCCDDEEFF", transport.ConnectionDeviceIds);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Renaming_a_heart_rate_source_restarts_its_worker_and_applies_fresh_disambiguation()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment target = HeartRate("102030405060", "Household HR");
    DeviceEnrollment peer = HeartRate("112233445566", "Household HR");
    VersionedDeviceEnrollment storedTarget = await store.EnrollAsync(
      target,
      now,
      Op("device.enroll.target", now));
    await store.EnrollAsync(peer, now, Op("device.enroll.peer", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      target.DeviceId,
      currentDeviceId: "AABBCCDDEEFF",
      advertiseStoredAddressFirst: false);
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
      Assert.True(await coordinator.RetryConnectionAsync(target.Id));
      using var firstAttemptTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
      while (transport.ConnectionDeviceIds.Count == 0)
      {
        await Task.Delay(25, firstAttemptTimeout.Token);
      }

      Assert.DoesNotContain("AABBCCDDEEFF", transport.ConnectionDeviceIds);
      long generationBeforeRename = coordinator.Current.HeartRate.ConnectionGeneration;
      await store.RenameAsync(
        target.Id,
        "Marc Polar H10",
        storedTarget.Version,
        now.AddSeconds(1),
        Op("device.rename.target", now.AddSeconds(1)));
      await coordinator.RefreshAsync();

      using var reconnectTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.HeartRate.State != DeviceConnectionState.Ready)
      {
        await Task.Delay(25, reconnectTimeout.Token);
      }

      Assert.True(coordinator.Current.HeartRate.ConnectionGeneration > generationBeforeRename);
      Assert.Contains("AABBCCDDEEFF", transport.ConnectionDeviceIds);
      Assert.Equal((ushort)142, coordinator.Current.HeartRateBpm);
      HeartRateSourceSnapshot renamedSource = Assert.Single(
        coordinator.Current.HeartRateSources!,
        source => source.EnrollmentId == target.Id);
      Assert.Equal(HeartRateDeviceKind.ChestStrap, renamedSource.Kind);
      Assert.Equal(HeartRateDeviceFamily.Polar, renamedSource.Family);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Rediscovers_a_rotated_heart_rate_address_after_a_generic_gatt_failure()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("102030405060", "Garmin Forerunner 965");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      heartRate.DeviceId,
      currentDeviceId: "AABBCCDDEEFF",
      advertiseStoredAddressFirst: true);
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
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
      while (coordinator.Current.HeartRate.State != DeviceConnectionState.Ready)
      {
        await Task.Delay(25, timeout.Token);
      }

      Assert.Contains(heartRate.DeviceId, transport.ConnectionDeviceIds);
      Assert.Contains("AABBCCDDEEFF", transport.ConnectionDeviceIds);
      Assert.True(transport.ActiveScanCount >= 2);
      Assert.True(transport.TargetedDiscoveryCount >= 2);
    }
    finally
    {
      await coordinator.StopAsync(CancellationToken.None);
      coordinator.Dispose();
    }
  }

  [Fact]
  public async Task Immediately_retries_a_freshly_advertising_heart_rate_source_after_native_disconnect()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var store = new DeviceEnrollmentStore(_factory);
    DeviceEnrollment heartRate = HeartRate("102030405060", "Polar H10");
    await store.EnrollAsync(heartRate, now, Op("device.enroll", now));
    var services = new ServiceCollection();
    services.AddSingleton(_factory);
    services.AddScoped<IDeviceEnrollmentStore, DeviceEnrollmentStore>();
    await using ServiceProvider provider = services.BuildServiceProvider();
    var transport = new RotatingHeartRateBleTransport(
      heartRate.DeviceId,
      currentDeviceId: heartRate.DeviceId,
      advertiseStoredAddressFirst: true,
      keepScanOpenAfterAdvertisement: true,
      disconnectFirstCurrentConnection: true);
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
      Assert.True(await coordinator.RetryConnectionAsync(heartRate.Id));
      using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
      while (transport.ConnectionDeviceIds.Count < 2)
      {
        await Task.Delay(25, timeout.Token);
      }

      Assert.Equal(2, transport.ConnectionDeviceIds.Count(id =>
        string.Equals(id, heartRate.DeviceId, StringComparison.OrdinalIgnoreCase)));
      Assert.True(transport.ActiveScanCount >= 1);
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

  private static DeviceEnrollment Treadmill(
    TreadmillTelemetryMode telemetryMode = TreadmillTelemetryMode.Ftms,
    TreadmillCapabilityEvidence evidence = TreadmillCapabilityEvidence.Unknown,
    string? modelNumber = null,
    string? firmwareRevision = null) => new(
    Guid.NewGuid(), DeviceRole.Treadmill, "A1B2C3D4E5F6", "horizon-omega-z", new string('a', 64),
    "Horizon Omega Z", modelNumber, firmwareRevision, telemetryMode,
    new TreadmillCapabilities(), evidence, null);

  private static DeviceEnrollment HeartRate() => HeartRate("102030405060", "Polar H10");

  private static DeviceEnrollment HeartRate(string id, string name) => new(
    Guid.NewGuid(), DeviceRole.HeartRate, id, "bluetooth-heart-rate", new string('b', 64),
    name, null, null, null, null, TreadmillCapabilityEvidence.Unknown, null);

  private static PersistenceWriteOperation Op(string type, DateTimeOffset now) => new(
    Guid.NewGuid(), type, 200, "{}", now, new string('0', 64));

  private sealed class EvidenceTrackingStore(IDeviceEnrollmentStore inner) : IDeviceEnrollmentStore
  {
    private int _evidenceWriteCount;

    public int EvidenceWriteCount => Volatile.Read(ref _evidenceWriteCount);

    public bool ThrowEvidenceWrites { get; set; }

    public TaskCompletionSource EvidenceWriteStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    public TaskCompletionSource? ReleaseEvidenceWrite { get; set; }

    public Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(
      CancellationToken cancellationToken = default) =>
      inner.ListActiveAsync(cancellationToken);

    public Task<VersionedDeviceEnrollment?> FindActiveAsync(
      DeviceRole role,
      CancellationToken cancellationToken = default) =>
      inner.FindActiveAsync(role, cancellationToken);

    public Task<VersionedDeviceEnrollment> EnrollAsync(
      DeviceEnrollment enrollment,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) =>
      inner.EnrollAsync(enrollment, nowUtc, operation, cancellationToken);

    public Task<bool> ForgetAsync(
      DeviceRole role,
      int expectedVersion,
      DateTimeOffset nowUtc,
      PersistenceWriteOperation operation,
      CancellationToken cancellationToken = default) =>
      inner.ForgetAsync(role, expectedVersion, nowUtc, operation, cancellationToken);

    public async Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(
      Guid id,
      int expectedVersion,
      string? modelNumber,
      string? firmwareRevision,
      TreadmillCapabilities? capabilities,
      TreadmillCapabilityEvidence evidence,
      DateTimeOffset verifiedAtUtc,
      CancellationToken cancellationToken = default)
    {
      Interlocked.Increment(ref _evidenceWriteCount);
      EvidenceWriteStarted.TrySetResult();
      if (ReleaseEvidenceWrite is { } release)
        await release.Task.WaitAsync(cancellationToken);
      if (ThrowEvidenceWrites)
        throw new InvalidOperationException("The evidence store was intentionally failed by the test.");
      return await inner.UpdateEvidenceAsync(
        id,
        expectedVersion,
        modelNumber,
        firmwareRevision,
        capabilities,
        evidence,
        verifiedAtUtc,
        cancellationToken);
    }
  }

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
    private static readonly Guid VendorService = Expand(0xFFF0);
    private static readonly Guid VendorStatus = Expand(0xFFF4);

    public DateTimeOffset? FirstHeartRateNotificationAt { get; private set; }
    public DateTimeOffset? FirstBatterySubscriptionAt { get; private set; }
    public ConcurrentQueue<string> ConnectionDeviceIds { get; } = [];
    public ConcurrentQueue<string> OperationEvents { get; } = [];
    private int _scanCount;
    public int ActiveScanCount => Volatile.Read(ref _scanCount);
    public ConcurrentDictionary<string, byte> UnavailableDeviceIds { get; } = new(StringComparer.OrdinalIgnoreCase);
    public bool UseVendorTreadmill { get; set; }
    public bool BlockHeartRateDeviceInformationReads { get; set; }
    public bool BlockTreadmillDeviceInformationReads { get; set; }
    public bool ThrowTreadmillDeviceInformationReads { get; set; }
    public bool OmitTreadmillDeviceInformationService { get; set; }
    public string TreadmillModelNumber { get; set; } = "OMEGA Z";
    public string TreadmillFirmwareRevision { get; set; } = "V10.23.17";
    public bool DisconnectFirstHeartRateSubscription { get; set; }
    private int _heartRateSubscriptionCount;
    public int HeartRateSubscriptionCount => Volatile.Read(ref _heartRateSubscriptionCount);
    public TaskCompletionSource DeviceInformationReadStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource DeviceInformationReadCompleted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource TreadmillDeviceInformationReadAttempted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource TreadmillDeviceInformationReadCompleted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource FirstVendorNotificationConsumed { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
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
      Interlocked.Increment(ref _scanCount);
      OperationEvents.Enqueue("scan");
      await Task.Yield();
      yield break;
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default)
    {
      ConnectionDeviceIds.Enqueue(deviceId);
      OperationEvents.Enqueue($"connect:{deviceId}");
      return ValueTask.FromResult<IBleConnection>(new Connection(deviceId, this));
    }

    private sealed class Connection(string deviceId, ScriptedBleTransport owner) : IBleConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default)
      {
        if (owner.UnavailableDeviceIds.ContainsKey(DeviceId))
          return ValueTask.FromException<IReadOnlyList<BleService>>(new WindowsBleDeviceUnavailableException());
        IReadOnlyList<BleService> result = DeviceId.StartsWith('A')
          ? owner.UseVendorTreadmill
            ? [new BleService(VendorService,
              [new BleCharacteristic(VendorService, VendorStatus, false, false, true)]),
              new BleService(DeviceInformationService,
              [
                new BleCharacteristic(DeviceInformationService, ModelNumber, true, false, false),
                new BleCharacteristic(DeviceInformationService, FirmwareRevision, true, false, false),
              ])]
            : [new BleService(Ftms,
            [
              new BleCharacteristic(Ftms, Feature, true, false, false),
              new BleCharacteristic(Ftms, SpeedRange, true, false, false),
              new BleCharacteristic(Ftms, InclineRange, true, false, false),
              new BleCharacteristic(Ftms, ControlPoint, false, true, true),
              new BleCharacteristic(Ftms, TreadmillData, false, false, true),
            ]),
          ..(owner.OmitTreadmillDeviceInformationService
            ? Array.Empty<BleService>()
            : [new BleService(DeviceInformationService,
              [
                new BleCharacteristic(DeviceInformationService, ModelNumber, true, false, false),
                new BleCharacteristic(DeviceInformationService, FirmwareRevision, true, false, false),
              ])])]
          :
          [
            new BleService(HeartRateService,
              [new BleCharacteristic(HeartRateService, HeartRateMeasurement, false, false, true)]),
            new BleService(BatteryService,
              [new BleCharacteristic(BatteryService, BatteryLevel, true, false, true)]),
            ..(owner.BlockHeartRateDeviceInformationReads
              ? new BleService[]
              {
                new(DeviceInformationService,
                [
                  new BleCharacteristic(DeviceInformationService, ModelNumber, true, false, false),
                  new BleCharacteristic(DeviceInformationService, FirmwareRevision, true, false, false),
                ]),
              }
              : []),
          ];
        return ValueTask.FromResult(result);
      }

      public async ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default)
      {
        if (owner.BlockTreadmillDeviceInformationReads &&
            DeviceId.StartsWith('A') &&
            serviceUuid == DeviceInformationService)
        {
          owner.TreadmillDeviceInformationReadAttempted.TrySetResult();
          await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
          owner.TreadmillDeviceInformationReadCompleted.TrySetResult();
        }
        if (owner.ThrowTreadmillDeviceInformationReads &&
            DeviceId.StartsWith('A') &&
            serviceUuid == DeviceInformationService)
        {
          owner.TreadmillDeviceInformationReadAttempted.TrySetResult();
          throw new WindowsBleException("The treadmill device information characteristic is unavailable.");
        }
        if (owner.BlockHeartRateDeviceInformationReads && serviceUuid == DeviceInformationService)
        {
          owner.DeviceInformationReadStarted.TrySetResult();
          await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
          owner.DeviceInformationReadCompleted.TrySetResult();
        }
        byte[] value = characteristicUuid == BatteryLevel
          ? [87]
          : characteristicUuid == ModelNumber
            ? System.Text.Encoding.UTF8.GetBytes(owner.TreadmillModelNumber)
          : characteristicUuid == FirmwareRevision
            ? System.Text.Encoding.UTF8.GetBytes(owner.TreadmillFirmwareRevision)
          : characteristicUuid == Feature
          ? [0, 0, 0, 0, 3, 0, 0, 0]
          : characteristicUuid == SpeedRange
            ? [0, 0, 0xD0, 0x07, 10, 0]
            : [0, 0, 0xC8, 0, 1, 0];
        return value;
      }

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        await Task.Yield();
        if (characteristicUuid == HeartRateMeasurement)
        {
          owner.FirstHeartRateNotificationAt ??= DateTimeOffset.UtcNow;
          int subscription = Interlocked.Increment(ref owner._heartRateSubscriptionCount);
          yield return new BleNotification(
            serviceUuid,
            characteristicUuid,
            owner.HeartRateNotificationValue,
            owner.HeartRateObservedAt ?? DateTimeOffset.UtcNow);
          if (owner.DisconnectFirstHeartRateSubscription && subscription == 1)
            throw new WindowsBleDisconnectedException();
          await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
          yield break;
        }
        if (characteristicUuid == VendorStatus)
        {
          owner.FirstVendorNotificationConsumed.TrySetResult();
          yield return new BleNotification(
            serviceUuid,
            characteristicUuid,
            CreateVendorStatusFrame(),
            DateTimeOffset.UtcNow);
          await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
          yield break;
        }
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

        byte[] value = [86];
        DateTimeOffset observedAt = DateTimeOffset.UtcNow;
        yield return new BleNotification(serviceUuid, characteristicUuid, value, observedAt);
        if (characteristicUuid == TreadmillData && owner.DisconnectAfterFirstTreadmillNotification)
        {
          Volatile.Write(ref owner._treadmillFailureObserved, 1);
          throw new WindowsBleDisconnectedException();
        }
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }

      private static byte[] CreateVendorStatusFrame()
      {
        var frame = new byte[40];
        frame[0] = 0x55;
        frame[1] = 0xAA;
        frame[5] = 0x17;
        frame[6] = 30;
        frame[^2] = 0x0D;
        frame[^1] = 0x0A;
        frame[24] = 0x6D;
        frame[25] = 0x02;
        frame[30] = 53;
        return frame;
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class CacheDependentBleTransport(string deviceId) : IBleCentralTransport
  {
    private readonly ScriptedBleTransport _connected = new();
    private bool _observed;

    public int ActiveScanCount { get; private set; }

    public int ConnectionAttemptCount { get; private set; }

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      ActiveScanCount++;
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

  private sealed class RotatingHeartRateBleTransport(
    string storedDeviceId,
    string currentDeviceId,
    bool advertiseStoredAddressFirst,
    bool throwAfterAdvertisement = false,
    bool keepScanOpenAfterAdvertisement = false,
    bool disconnectFirstCurrentConnection = false,
    bool disconnectAfterStableCurrentConnection = false) : IBleCentralTransport
  {
    private static readonly Guid HeartRateService = Expand(0x180D);
    private static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    private readonly TaskCompletionSource<string> _firstConnectionAttempt = new(
      TaskCreationOptions.RunContinuationsAsynchronously);
    public Task<string> FirstConnectionAttempt => _firstConnectionAttempt.Task;
    private int _scanCount;
    private int _targetedDiscoveryCount;
    private int _currentConnectionCount;

    public int ActiveScanCount => Volatile.Read(ref _scanCount);

    public int TargetedDiscoveryCount => Volatile.Read(ref _targetedDiscoveryCount);

    public ConcurrentQueue<string> ConnectionDeviceIds { get; } = [];

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      int scan = Interlocked.Increment(ref _scanCount);
      await Task.Yield();
      cancellationToken.ThrowIfCancellationRequested();
      string advertisedId = advertiseStoredAddressFirst && scan == 1
        ? storedDeviceId
        : currentDeviceId;
      yield return new BleAdvertisement(
        advertisedId,
        advertiseStoredAddressFirst ? "Garmin Forerunner 965" : "Polar H10",
        -42,
        [HeartRateService]);
      if (throwAfterAdvertisement)
      {
        throw new InvalidOperationException("Simulated truncated active scan.");
      }
      if (keepScanOpenAfterAdvertisement)
      {
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default)
    {
      ConnectionDeviceIds.Enqueue(deviceId);
      _firstConnectionAttempt.TrySetResult(deviceId);
      int currentConnection = string.Equals(deviceId, currentDeviceId, StringComparison.OrdinalIgnoreCase)
        ? Interlocked.Increment(ref _currentConnectionCount)
        : 0;
      return ValueTask.FromResult<IBleConnection>(
        currentConnection > 0
          ? new WorkingHeartRateConnection(
            deviceId,
            this,
            disconnectFirstCurrentConnection && currentConnection == 1,
            disconnectAfterStableCurrentConnection && currentConnection == 1)
          : new GenericGattFailureConnection(deviceId, this));
    }

    private sealed class GenericGattFailureConnection(
      string deviceId,
      RotatingHeartRateBleTransport owner) :
      IBleConnection,
      IBleTargetedServiceDiscoveryConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) =>
        ValueTask.FromException<IReadOnlyList<BleService>>(
          new WindowsBleException("The cached GATT handle is stale."));

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesForUuidsAsync(
        IReadOnlyCollection<Guid> serviceUuids,
        CancellationToken cancellationToken = default)
      {
        Interlocked.Increment(ref owner._targetedDiscoveryCount);
        return ValueTask.FromResult<IReadOnlyList<BleService>>([]);
      }

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();
    }

    private sealed class WorkingHeartRateConnection(
      string deviceId,
      RotatingHeartRateBleTransport owner,
      bool disconnectAfterNotification,
      bool disconnectAfterStableConnection) :
      IBleConnection,
      IBleTargetedServiceDiscoveryConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) => throw new WindowsBleException(
          "Full discovery reached an unrelated protected Garmin/Polar service.");

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesForUuidsAsync(
        IReadOnlyCollection<Guid> serviceUuids,
        CancellationToken cancellationToken = default)
      {
        Interlocked.Increment(ref owner._targetedDiscoveryCount);
        if (!serviceUuids.Contains(HeartRateService))
          return ValueTask.FromResult<IReadOnlyList<BleService>>([]);
        return ValueTask.FromResult<IReadOnlyList<BleService>>(
        [
          new BleService(HeartRateService,
          [
            new BleCharacteristic(
              HeartRateService,
              HeartRateMeasurement,
              CanRead: false,
              CanWrite: false,
              CanNotify: true),
          ]),
        ]);
      }

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        DateTimeOffset firstAt = DateTimeOffset.UtcNow;
        yield return new BleNotification(
          serviceUuid,
          characteristicUuid,
          new byte[] { 0x00, 142 },
          firstAt);
        if (disconnectAfterNotification)
        {
          throw new WindowsBleDisconnectedException();
        }
        if (disconnectAfterStableConnection)
        {
          for (var second = 1; second <= (int)BleReconnectPolicy.StableConnectionThreshold.TotalSeconds / 5; second++)
          {
            yield return new BleNotification(
              serviceUuid,
              characteristicUuid,
              new byte[] { 0x00, 142 },
              firstAt.AddSeconds(second * 5));
          }
          throw new WindowsBleDisconnectedException();
        }
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class FallbackHysteresisBleTransport(
    string primaryDeviceId,
    string fallbackDeviceId,
    AdjustableTimeProvider? clock = null) : IBleCentralTransport
  {
    private static readonly Guid HeartRateService = Expand(0x180D);
    private static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    private readonly string _primaryDeviceId = primaryDeviceId;
    private readonly string _fallbackDeviceId = fallbackDeviceId;
    private readonly AdjustableTimeProvider? _clock = clock;
    private int _primaryAvailable;
    private int _fallbackConnectionCount;

    public bool PrimaryAvailable
    {
      get => Volatile.Read(ref _primaryAvailable) != 0;
      set => Volatile.Write(ref _primaryAvailable, value ? 1 : 0);
    }

    public int FallbackConnectionCount => Volatile.Read(ref _fallbackConnectionCount);
    public bool CompleteStableRecovery { get; init; }
    public bool HoldPrimaryUnavailableDiscovery { get; set; }
    public TaskCompletionSource PrimaryDiscoveryStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource ReleasePrimaryDiscovery { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource FallbackConnectionStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource PrimarySampleObserved { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource ReleasePrimaryFailure { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource ReleaseStablePrimarySample { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource SparsePrimarySampleObserved { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource ReleaseContinuousPrimarySamples { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource PrimaryStableSampleObserved { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource ReleasePostStablePrimarySample { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource PostStablePrimarySampleObserved { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.Yield();
      yield break;
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default)
    {
      if (string.Equals(deviceId, _fallbackDeviceId, StringComparison.OrdinalIgnoreCase))
      {
        Interlocked.Increment(ref _fallbackConnectionCount);
        FallbackConnectionStarted.TrySetResult();
      }
      return ValueTask.FromResult<IBleConnection>(new Connection(deviceId, this));
    }

    private sealed class Connection(string deviceId, FallbackHysteresisBleTransport owner) :
      IBleConnection, IBleTargetedServiceDiscoveryConnection
    {
      public string DeviceId { get; } = deviceId;

      public ValueTask DisposeAsync() => ValueTask.CompletedTask;

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) => RequiredServices(cancellationToken);

      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesForUuidsAsync(
        IReadOnlyCollection<Guid> serviceUuids,
        CancellationToken cancellationToken = default) =>
        serviceUuids.Contains(HeartRateService)
          ? RequiredServices(cancellationToken)
          : ValueTask.FromResult<IReadOnlyList<BleService>>([]);

      private async ValueTask<IReadOnlyList<BleService>> RequiredServices(CancellationToken cancellationToken)
      {
        if (string.Equals(DeviceId, owner._primaryDeviceId, StringComparison.OrdinalIgnoreCase) && !owner.PrimaryAvailable)
        {
          owner.PrimaryDiscoveryStarted.TrySetResult();
          if (owner.HoldPrimaryUnavailableDiscovery)
            await owner.ReleasePrimaryDiscovery.Task.WaitAsync(cancellationToken);
          throw new WindowsBleDeviceUnavailableException();
        }
        return [
          new BleService(HeartRateService,
            [new BleCharacteristic(HeartRateService, HeartRateMeasurement, false, false, true)]),
        ];
      }

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        DateTimeOffset firstAt = owner._clock?.GetUtcNow() ?? DateTimeOffset.UtcNow;
        yield return new BleNotification(serviceUuid, characteristicUuid, new byte[] { 0x00, 142 }, firstAt);
        if (string.Equals(DeviceId, owner._primaryDeviceId, StringComparison.OrdinalIgnoreCase))
        {
          owner.PrimarySampleObserved.TrySetResult();
          if (owner.CompleteStableRecovery)
          {
            await owner.ReleaseStablePrimarySample.Task.WaitAsync(cancellationToken);
            DateTimeOffset sparseAt = firstAt.Add(BleReconnectPolicy.StableConnectionThreshold);
            owner._clock!.Set(sparseAt);
            yield return new BleNotification(serviceUuid, characteristicUuid, new byte[] { 0x00, 142 }, sparseAt);
            owner.SparsePrimarySampleObserved.TrySetResult();
            await owner.ReleaseContinuousPrimarySamples.Task.WaitAsync(cancellationToken);
            for (var second = 1; second <= (int)BleReconnectPolicy.StableConnectionThreshold.TotalSeconds; second++)
            {
              DateTimeOffset stableAt = sparseAt.AddSeconds(second);
              owner._clock!.Set(stableAt);
              yield return new BleNotification(serviceUuid, characteristicUuid, new byte[] { 0x00, 142 }, stableAt);
            }
            owner.PrimaryStableSampleObserved.TrySetResult();
            await owner.ReleasePostStablePrimarySample.Task.WaitAsync(cancellationToken);
            DateTimeOffset postStableAt = owner._clock!.GetUtcNow();
            yield return new BleNotification(serviceUuid, characteristicUuid, new byte[] { 0x00, 142 }, postStableAt);
            owner.PostStablePrimarySampleObserved.TrySetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            yield break;
          }
          await owner.ReleasePrimaryFailure.Task.WaitAsync(cancellationToken);
          throw new WindowsBleDisconnectedException();
        }
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class HangingOptionalDiscoveryBleTransport : IBleCentralTransport
  {
    private static readonly Guid HeartRateService = Expand(0x180D);
    private static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    public TaskCompletionSource OptionalDiscoveryStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

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

    private sealed class Connection(string deviceId, HangingOptionalDiscoveryBleTransport owner) :
      IBleConnection, IBleTargetedServiceDiscoveryConnection
    {
      public string DeviceId { get; } = deviceId;
      public ValueTask DisposeAsync() => ValueTask.CompletedTask;
      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public async ValueTask<IReadOnlyList<BleService>> DiscoverServicesForUuidsAsync(
        IReadOnlyCollection<Guid> serviceUuids,
        CancellationToken cancellationToken = default)
      {
        if (serviceUuids.Count == 1 && serviceUuids.Contains(HeartRateService))
        {
          return [new BleService(HeartRateService,
            [new BleCharacteristic(HeartRateService, HeartRateMeasurement, false, false, true)])];
        }
        owner.OptionalDiscoveryStarted.TrySetResult();
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
        return [];
      }

      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        yield return new BleNotification(serviceUuid, characteristicUuid, new byte[] { 0x00, 142 }, DateTimeOffset.UtcNow);
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class BackoffSequenceBleTransport(AdjustableTimeProvider clock) : IBleCentralTransport
  {
    private static readonly Guid FtmsService = Expand(0x1826);
    private static readonly Guid TreadmillData = Expand(0x2ACD);
    private int _connectionAttemptCount;
    public int ConnectionAttemptCount => Volatile.Read(ref _connectionAttemptCount);

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.Yield();
      yield break;
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult<IBleConnection>(new Connection(
        deviceId,
        Interlocked.Increment(ref _connectionAttemptCount),
        clock));

    private sealed class Connection(
      string deviceId,
      int attempt,
      AdjustableTimeProvider clock) : IBleConnection
    {
      public string DeviceId { get; } = deviceId;
      public ValueTask DisposeAsync() => ValueTask.CompletedTask;
      public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(
        CancellationToken cancellationToken = default) => ValueTask.FromResult<IReadOnlyList<BleService>>([
          new BleService(FtmsService, [new BleCharacteristic(FtmsService, TreadmillData, false, false, true)]),
        ]);
      public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        CancellationToken cancellationToken = default) => throw new NotSupportedException();

      public async IAsyncEnumerable<BleNotification> SubscribeAsync(
        Guid serviceUuid,
        Guid characteristicUuid,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
      {
        byte[] sample = [0x08, 0x00, 0x58, 0x02, 0x0A, 0x00, 0x00, 0x00];
        DateTimeOffset firstAt = clock.GetUtcNow();
        yield return new BleNotification(serviceUuid, characteristicUuid, sample, firstAt);
        if (attempt == 1)
        {
          clock.Set(firstAt.AddSeconds(1));
          throw new WindowsBleDisconnectedException();
        }
        if (attempt == 2)
        {
          DateTimeOffset sparseAt = firstAt.Add(BleReconnectPolicy.StableConnectionThreshold);
          yield return new BleNotification(serviceUuid, characteristicUuid, sample, sparseAt);
          clock.Set(sparseAt.AddSeconds(1));
          throw new WindowsBleDisconnectedException();
        }
        if (attempt == 3)
        {
          DateTimeOffset lastAt = firstAt;
          for (var second = 1; second <= (int)BleReconnectPolicy.StableConnectionThreshold.TotalSeconds; second++)
          {
            lastAt = firstAt.AddSeconds(second);
            yield return new BleNotification(serviceUuid, characteristicUuid, sample, lastAt);
          }
          clock.Set(lastAt.AddSeconds(6));
          throw new WindowsBleDisconnectedException();
        }
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
    }

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class AdjustableTimeProvider(DateTimeOffset initial) : TimeProvider
  {
    private long _utcTicks = initial.UtcTicks;
    public override DateTimeOffset GetUtcNow() => new(Interlocked.Read(ref _utcTicks), TimeSpan.Zero);
    public void Set(DateTimeOffset value) => Interlocked.Exchange(ref _utcTicks, value.UtcTicks);
  }

  private sealed class NonCooperativeAsyncDisposable : IAsyncDisposable
  {
    private readonly TaskCompletionSource _neverCompletes = new(
      TaskCreationOptions.RunContinuationsAsynchronously);

    public bool DisposeCalled { get; private set; }

    public ValueTask DisposeAsync()
    {
      DisposeCalled = true;
      return new ValueTask(_neverCompletes.Task);
    }
  }
}
