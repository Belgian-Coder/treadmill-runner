using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Ftms;
using TreadmillRunner.Protocols.HeartRate;
using TreadmillRunner.Protocols.Omega;
using Microsoft.EntityFrameworkCore;
using System.Text;
using System.Runtime.CompilerServices;
using System.Threading.Channels;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Bluetooth;

namespace TreadmillRunner.Gateway.Devices;

public interface IReadOnlyDeviceCoordinator
{
  DeviceTelemetrySnapshot Current { get; }
  bool HasTreadmillEnrollment => false;
  DeviceTelemetrySnapshot CurrentForProfile(Guid? profileId) => Current;
  Task RefreshAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
}

public sealed class ReadOnlyDeviceCoordinator(
  IServiceScopeFactory scopeFactory,
  IBleCentralTransport transport,
  TimeProvider timeProvider,
  IApplicationMaintenanceState maintenanceState,
  ILogger<ReadOnlyDeviceCoordinator> logger) : BackgroundService, IReadOnlyDeviceCoordinator
{
  private static readonly TimeSpan EnrollmentRefreshInterval = TimeSpan.FromSeconds(2);
  private static readonly TimeSpan GattOperationTimeout = TimeSpan.FromSeconds(15);
  private static readonly TimeSpan InitialNotificationTimeout = TimeSpan.FromSeconds(15);
  private static readonly TimeSpan TelemetrySilenceTimeout = TimeSpan.FromSeconds(30);
  private static readonly TimeSpan ReliabilityRetention = TimeSpan.FromDays(90);
  private static readonly TimeSpan HeartRateFreshnessLimit = TimeSpan.FromSeconds(5);
  private const int MaximumHeartRateWorkers = 8;
  private readonly object _sync = new();
  private readonly SemaphoreSlim _reconcileGate = new(1, 1);
  private readonly Dictionary<Guid, DeviceWorker> _workers = [];
  private readonly Dictionary<Guid, HeartRateRuntime> _heartRateSources = [];
  private readonly Dictionary<Guid, SelectionRuntime> _profileSelections = [];
  private readonly Dictionary<Guid, ReliabilityIncidentRuntime> _reliabilityIncidents = [];
  private readonly BleReconnectPolicy _reconnectPolicy = new();
  private readonly Channel<ReliabilityWrite> _reliabilityWrites = Channel.CreateUnbounded<ReliabilityWrite>(
    new UnboundedChannelOptions
    {
      SingleReader = true,
      SingleWriter = false,
    });
  private IReadOnlyList<HeartRateDeviceAssignment> _assignments = [];
  private long _nextGeneration;
  private bool _hasTreadmillEnrollment;
  private DeviceTelemetrySnapshot _snapshot = EmptySnapshot(timeProvider.GetUtcNow());

  public DeviceTelemetrySnapshot Current
  {
    get
    {
      lock (_sync)
      {
        return BuildSnapshotLocked(profileId: null);
      }
    }
  }

  public bool HasTreadmillEnrollment
  {
    get
    {
      lock (_sync) return _hasTreadmillEnrollment;
    }
  }

  public DeviceTelemetrySnapshot CurrentForProfile(Guid? profileId)
  {
    lock (_sync)
    {
      return BuildSnapshotLocked(profileId);
    }
  }

  public Task RefreshAsync(CancellationToken cancellationToken = default) =>
    ReconcileWorkersAsync(cancellationToken);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    Task reliabilityWriter = RunReliabilityWriterAsync(CancellationToken.None);
    try
    {
      while (!stoppingToken.IsCancellationRequested)
      {
        await ReconcileWorkersAsync(stoppingToken);
        await Task.Delay(EnrollmentRefreshInterval, timeProvider, stoppingToken);
      }
    }
    finally
    {
      DeviceWorker[] workers;
      lock (_sync)
      {
        workers = _workers.Values.ToArray();
        _workers.Clear();
      }

      foreach (DeviceWorker worker in workers) worker.Cancellation.Cancel();
      await Task.WhenAll(workers.Select(static worker => worker.Task));
      foreach (DeviceWorker worker in workers) worker.Cancellation.Dispose();
      _reliabilityWrites.Writer.TryComplete();
      try
      {
        await reliabilityWriter;
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
      }
    }
  }

  private async Task ReconcileWorkersAsync(CancellationToken stoppingToken)
  {
    await _reconcileGate.WaitAsync(stoppingToken);
    try
    {
      IReadOnlyList<VersionedDeviceEnrollment> enrollments;
      IReadOnlyList<HeartRateDeviceAssignment> assignments;
      try
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        IDeviceEnrollmentStore store = scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>();
        enrollments = await store.ListActiveAsync(stoppingToken);
        assignments = await store.ListHeartRateAssignmentsAsync(stoppingToken);
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        logger.LogDebug(exception, "Device enrollment polling is waiting for database readiness.");
        return;
      }

      HashSet<Guid> autoConnectIds = assignments
        .Where(static assignment => assignment.AutoConnect)
        .Select(static assignment => assignment.DeviceEnrollmentId)
        .ToHashSet();
      foreach (VersionedDeviceEnrollment polar in enrollments.Where(static item =>
        item.Enrollment.Role == DeviceRole.HeartRate &&
        item.Enrollment.HeartRateDeviceFamily == HeartRateDeviceFamily.Polar))
      {
        autoConnectIds.Add(polar.Enrollment.Id);
      }
      HashSet<Guid> assignedIds = assignments
        .Select(static assignment => assignment.DeviceEnrollmentId)
        .ToHashSet();
      VersionedDeviceEnrollment[] desiredEnrollments = enrollments
        .Where(enrollment => enrollment.Enrollment.Role == DeviceRole.Treadmill ||
          !assignedIds.Contains(enrollment.Enrollment.Id) ||
          autoConnectIds.Contains(enrollment.Enrollment.Id))
        .Where((enrollment, _) => enrollment.Enrollment.Role == DeviceRole.Treadmill ||
          enrollments.Where(item => item.Enrollment.Role == DeviceRole.HeartRate)
            .Take(MaximumHeartRateWorkers)
            .Any(item => item.Enrollment.Id == enrollment.Enrollment.Id))
        .ToArray();
      var desired = desiredEnrollments.ToDictionary(static enrollment => enrollment.Enrollment.Id);
      List<DeviceWorker> removed = [];
      List<VersionedDeviceEnrollment> added = [];
      lock (_sync)
      {
        _assignments = assignments;
        _hasTreadmillEnrollment = enrollments.Any(static item => item.Enrollment.Role == DeviceRole.Treadmill);
        foreach ((Guid enrollmentId, DeviceWorker worker) in _workers.ToArray())
        {
          if (!desired.TryGetValue(enrollmentId, out VersionedDeviceEnrollment? enrollment) ||
              RequiresWorkerRestart(worker.Enrollment.Enrollment, enrollment.Enrollment))
          {
            _workers.Remove(enrollmentId);
            worker.Cancellation.Cancel();
            removed.Add(worker);
            if (!desired.ContainsKey(enrollmentId) && worker.Role == DeviceRole.HeartRate)
            {
              _heartRateSources.Remove(enrollmentId);
              _reliabilityIncidents.Remove(enrollmentId);
            }
            else
            {
              SetDisconnected(worker.EnrollmentId, worker.Role);
            }
          }
          else if (!Equals(worker.Enrollment, enrollment))
          {
            _workers[enrollmentId] = worker with { Enrollment = enrollment };
            RefreshEnrollmentMetadata(enrollment.Enrollment);
          }
        }

        foreach (VersionedDeviceEnrollment enrollment in desiredEnrollments)
        {
          if (_workers.ContainsKey(enrollment.Enrollment.Id)) continue;
          added.Add(enrollment);
        }
      }

      if (removed.Count > 0)
      {
        await Task.WhenAll(removed.Select(static worker => worker.Task));
        foreach (DeviceWorker worker in removed) worker.Cancellation.Dispose();
      }

      lock (_sync)
      {
        foreach (VersionedDeviceEnrollment enrollment in added)
        {
          if (_workers.ContainsKey(enrollment.Enrollment.Id)) continue;
          var cancellation = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
          Task task = Task.Run(
            () => RunEnrollmentAsync(enrollment, cancellation.Token),
            CancellationToken.None);
          _workers.Add(enrollment.Enrollment.Id,
            new DeviceWorker(enrollment, cancellation, task));
        }
      }
    }
    finally
    {
      _reconcileGate.Release();
    }
  }

  private async Task RunEnrollmentAsync(
    VersionedDeviceEnrollment stored,
    CancellationToken cancellationToken)
  {
    DeviceEnrollment enrollment = stored.Enrollment;
    var consecutiveFailureCount = 0;
    while (!cancellationToken.IsCancellationRequested)
    {
      long generation = Interlocked.Increment(ref _nextGeneration);
      var attempt = new ConnectionAttemptRuntime();
      try
      {
        UpdateConnection(enrollment, DeviceConnectionState.Connecting, generation, fault: null);
        await using IBleConnection connection = await transport.ConnectAsync(
          enrollment.DeviceId,
          cancellationToken);
        UpdateConnection(enrollment, DeviceConnectionState.DiscoveringServices, generation, fault: null);
        IReadOnlyList<BleService> services = await connection.DiscoverServicesAsync(cancellationToken)
          .AsTask()
          .WaitAsync(GattOperationTimeout, timeProvider, cancellationToken);
        UpdateConnection(enrollment, DeviceConnectionState.Subscribing, generation, fault: null);

        if (enrollment.Role == DeviceRole.Treadmill)
        {
          await RunTreadmillAsync(
            connection,
            enrollment,
            stored.Version,
            services,
            generation,
            observedAt => OnPrimaryTelemetry(enrollment, generation, observedAt, attempt),
            cancellationToken);
        }
        else
        {
          await RunHeartRateAsync(
            connection,
            enrollment,
            stored.Version,
            services,
            generation,
            observedAt => OnPrimaryTelemetry(enrollment, generation, observedAt, attempt),
            cancellationToken);
        }

        throw new IOException("The BLE notification stream ended unexpectedly.");
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        DateTimeOffset failedAt = timeProvider.GetUtcNow();
        if (attempt.IsStableAt(failedAt))
        {
          consecutiveFailureCount = 0;
        }
        consecutiveFailureCount++;
        TimeSpan reconnectDelay = _reconnectPolicy.GetDelay(enrollment.Id, consecutiveFailureCount);
        string sanitizedFault = SanitizeFault(exception);
        logger.LogWarning(
          exception,
          "Read-only {DeviceRole} connection failed; reconnecting without issuing a treadmill command.",
          enrollment.Role);
        UpdateConnection(
          enrollment,
          DeviceConnectionState.Faulted,
          generation,
          sanitizedFault);
        RecordReliabilityFailure(
          enrollment,
          generation,
          ClassifyFailure(exception),
          sanitizedFault,
          reconnectDelay,
          failedAt);
        UpdateConnection(enrollment, DeviceConnectionState.Reconnecting, generation, fault: null);
        try
        {
          await Task.Delay(reconnectDelay, timeProvider, cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
          break;
        }
      }
    }

    SetDisconnected(enrollment.Id, enrollment.Role);
  }

  private static bool RequiresWorkerRestart(DeviceEnrollment current, DeviceEnrollment desired) =>
    current.Role != desired.Role ||
    !string.Equals(current.DeviceId, desired.DeviceId, StringComparison.Ordinal) ||
    !string.Equals(current.ProtocolId, desired.ProtocolId, StringComparison.Ordinal) ||
    !string.Equals(current.IdentityFingerprint, desired.IdentityFingerprint, StringComparison.Ordinal) ||
    current.TelemetryMode != desired.TelemetryMode;

  private void RefreshEnrollmentMetadata(DeviceEnrollment enrollment)
  {
    if (enrollment.Role == DeviceRole.Treadmill)
    {
      DeviceConnectionSnapshot current = _snapshot.Treadmill;
      _snapshot = _snapshot with
      {
        Treadmill = current with
        {
          DisplayName = enrollment.DisplayName,
          ModelNumber = enrollment.ModelNumber,
          FirmwareRevision = enrollment.FirmwareRevision,
          Evidence = enrollment.Evidence,
          Capabilities = enrollment.Capabilities,
        },
        ReportedCapabilities = enrollment.Capabilities,
      };
    }
    else if (_heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime))
    {
      _heartRateSources[enrollment.Id] = runtime with { Enrollment = enrollment };
    }
  }

  private async Task RunTreadmillAsync(
    IBleConnection connection,
    DeviceEnrollment enrollment,
    int enrollmentVersion,
    IReadOnlyList<BleService> services,
    long generation,
    Action<DateTimeOffset> primaryTelemetryObserved,
    CancellationToken cancellationToken)
  {
    if (enrollment.TelemetryMode == TreadmillTelemetryMode.Ftms)
    {
      RequireCharacteristic(services, Uuids.FtmsService, Uuids.TreadmillData, requireNotify: true);
      TreadmillCapabilities reported = await ReadFtmsCapabilitiesAsync(connection, services, cancellationToken);
      (string? model, string? firmware) = await ReadDeviceInformationAsync(connection, services, cancellationToken);
      UpdateCapabilities(reported);
      UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
      var evidencePersisted = false;
      await foreach (BleNotification notification in SubscribeWithWatchdogAsync(
        connection,
        Uuids.FtmsService,
        Uuids.TreadmillData,
        cancellationToken))
      {
        if (!FtmsTreadmillDataParser.TryParse(notification.Value.Span, out FtmsTreadmillData? data) ||
            data is null)
        {
          throw new InvalidDataException("FTMS treadmill telemetry was invalid.");
        }

        UpdateTreadmillTelemetry(data, notification.ObservedAt, generation);
        primaryTelemetryObserved(notification.ObservedAt);
        if (!evidencePersisted)
        {
          evidencePersisted = true;
          await TryPersistEvidenceAsync(
            enrollment,
            enrollmentVersion,
            model,
            firmware,
            reported,
            notification.ObservedAt,
            cancellationToken);
        }
      }

      return;
    }

    RequireCharacteristic(services, Uuids.VendorService, Uuids.VendorStatus, requireNotify: true);
    var reassembler = new OmegaFrameReassembler();
    (string? vendorModel, string? vendorFirmware) = await ReadDeviceInformationAsync(
      connection,
      services,
      cancellationToken);
    var vendorEvidencePersisted = false;
    UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
    await foreach (BleNotification notification in SubscribeWithWatchdogAsync(
      connection,
      Uuids.VendorService,
      Uuids.VendorStatus,
      cancellationToken))
    {
      foreach (byte[] frame in reassembler.Append(notification.Value.Span))
      {
        if (OmegaStatusDecoder.TryDecode(frame, out OmegaStatus? status) && status is not null)
        {
          UpdateTreadmillTelemetry(
            new FtmsTreadmillData(0, status.SpeedKph, status.InclinePercent, null),
            notification.ObservedAt,
            generation);
          primaryTelemetryObserved(notification.ObservedAt);
          if (!vendorEvidencePersisted)
          {
            vendorEvidencePersisted = true;
            await TryPersistEvidenceAsync(
              enrollment,
              enrollmentVersion,
              vendorModel,
              vendorFirmware,
              enrollment.Capabilities,
              notification.ObservedAt,
              cancellationToken);
          }
        }
      }
    }
  }

  private async Task RunHeartRateAsync(
    IBleConnection connection,
    DeviceEnrollment enrollment,
    int enrollmentVersion,
    IReadOnlyList<BleService> services,
    long generation,
    Action<DateTimeOffset> primaryTelemetryObserved,
    CancellationToken cancellationToken)
  {
    RequireCharacteristic(services, Uuids.HeartRateService, Uuids.HeartRateMeasurement, requireNotify: true);
    UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
    (string? model, string? firmware) = await ReadDeviceInformationAsync(connection, services, cancellationToken);
    var evidencePersisted = false;
    using var batteryCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    Task batteryTask = RunOptionalBatteryAsync(
      connection,
      enrollment,
      services,
      generation,
      batteryCancellation.Token);
    try
    {
      await foreach (BleNotification notification in SubscribeWithWatchdogAsync(
        connection,
        Uuids.HeartRateService,
        Uuids.HeartRateMeasurement,
        cancellationToken))
      {
        HeartRateMeasurement measurement = HeartRateMeasurementParser.Parse(notification.Value.Span);
        lock (_sync)
        {
          if (!_heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime) ||
              runtime.Connection.ConnectionGeneration != generation) continue;
          _heartRateSources[enrollment.Id] = runtime with
          {
            BeatsPerMinute = measurement.BeatsPerMinute,
            ObservedAt = notification.ObservedAt,
            Connection = runtime.Connection with { LastObservedAt = notification.ObservedAt, Fault = null },
          };
        }
        primaryTelemetryObserved(notification.ObservedAt);
        if (!evidencePersisted)
        {
          evidencePersisted = true;
          await TryPersistEvidenceAsync(
            enrollment,
            enrollmentVersion,
            model,
            firmware,
            null,
            notification.ObservedAt,
            cancellationToken);
        }
      }
    }
    finally
    {
      batteryCancellation.Cancel();
      try
      {
        await batteryTask;
      }
      catch (OperationCanceledException) when (batteryCancellation.IsCancellationRequested)
      {
      }
    }
  }

  private async Task RunOptionalBatteryAsync(
    IBleConnection connection,
    DeviceEnrollment enrollment,
    IReadOnlyList<BleService> services,
    long generation,
    CancellationToken cancellationToken)
  {
    BleCharacteristic? battery = FindCharacteristic(services, Uuids.BatteryService, Uuids.BatteryLevel);
    if (battery is null || (!battery.CanRead && !battery.CanNotify)) return;

    if (battery.CanRead)
    {
      try
      {
        ReadOnlyMemory<byte> value = await ReadBoundedAsync(
          connection,
          Uuids.BatteryService,
          Uuids.BatteryLevel,
          cancellationToken);
        if (BatteryLevelParser.TryParse(value.Span, out byte percent))
        {
          UpdateHeartRateBattery(enrollment.Id, generation, percent, timeProvider.GetUtcNow());
        }
      }
      catch (Exception exception) when (exception is not OperationCanceledException)
      {
        logger.LogDebug(exception, "Optional heart-rate battery read was unavailable.");
      }
    }

    if (!battery.CanNotify || cancellationToken.IsCancellationRequested) return;
    try
    {
      await foreach (BleNotification notification in connection.SubscribeAsync(
        Uuids.BatteryService,
        Uuids.BatteryLevel,
        cancellationToken))
      {
        if (BatteryLevelParser.TryParse(notification.Value.Span, out byte percent))
        {
          UpdateHeartRateBattery(enrollment.Id, generation, percent, notification.ObservedAt);
        }
        else
        {
          logger.LogDebug("Optional heart-rate battery notification was malformed.");
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
    }
    catch (Exception exception)
    {
      logger.LogDebug(exception, "Optional heart-rate battery notifications ended.");
    }
  }

  private void UpdateHeartRateBattery(
    Guid enrollmentId,
    long generation,
    byte percent,
    DateTimeOffset observedAt)
  {
    lock (_sync)
    {
      if (!_heartRateSources.TryGetValue(enrollmentId, out HeartRateRuntime? runtime) ||
          runtime.Connection.ConnectionGeneration != generation) return;
      _heartRateSources[enrollmentId] = runtime with
      {
        BatteryPercent = percent,
        BatteryObservedAt = observedAt,
      };
    }
  }

  private async IAsyncEnumerable<BleNotification> SubscribeWithWatchdogAsync(
    IBleConnection connection,
    Guid serviceUuid,
    Guid characteristicUuid,
    [EnumeratorCancellation] CancellationToken cancellationToken)
  {
    using var subscriptionCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    IAsyncEnumerator<BleNotification> enumerator = connection.SubscribeAsync(
      serviceUuid,
      characteristicUuid,
      subscriptionCancellation.Token).GetAsyncEnumerator(subscriptionCancellation.Token);
    var first = true;
    try
    {
      while (true)
      {
        bool hasNotification;
        try
        {
          hasNotification = await enumerator.MoveNextAsync().AsTask().WaitAsync(
            first ? InitialNotificationTimeout : TelemetrySilenceTimeout,
            timeProvider,
            cancellationToken);
        }
        catch (TimeoutException exception)
        {
          subscriptionCancellation.Cancel();
          throw new BleTelemetrySilenceException(first, exception);
        }

        if (!hasNotification) yield break;
        first = false;
        yield return enumerator.Current;
      }
    }
    finally
    {
      subscriptionCancellation.Cancel();
      try
      {
        await enumerator.DisposeAsync();
      }
      catch (OperationCanceledException) when (subscriptionCancellation.IsCancellationRequested)
      {
      }
    }
  }

  private async Task<ReadOnlyMemory<byte>> ReadBoundedAsync(
    IBleConnection connection,
    Guid serviceUuid,
    Guid characteristicUuid,
    CancellationToken cancellationToken) => await connection.ReadAsync(
      serviceUuid,
      characteristicUuid,
      cancellationToken).AsTask().WaitAsync(GattOperationTimeout, timeProvider, cancellationToken);

  private async Task<(string? Model, string? Firmware)> ReadDeviceInformationAsync(
    IBleConnection connection,
    IReadOnlyList<BleService> services,
    CancellationToken cancellationToken)
  {
    string? model = await TryReadStringAsync(connection, services, Uuids.ModelNumber, cancellationToken);
    string? firmware = await TryReadStringAsync(connection, services, Uuids.FirmwareRevision, cancellationToken);
    return (model, firmware);
  }

  private async Task<string?> TryReadStringAsync(
    IBleConnection connection,
    IReadOnlyList<BleService> services,
    Guid characteristicUuid,
    CancellationToken cancellationToken)
  {
    BleCharacteristic? characteristic = FindCharacteristic(
      services,
      Uuids.DeviceInformationService,
      characteristicUuid);
    if (characteristic?.CanRead != true) return null;
    ReadOnlyMemory<byte> value = await ReadBoundedAsync(
      connection,
      Uuids.DeviceInformationService,
      characteristicUuid,
      cancellationToken);
    string text = Encoding.UTF8.GetString(value.Span).Trim('\0', ' ', '\t', '\r', '\n');
    return string.IsNullOrWhiteSpace(text) ? null : text[..Math.Min(text.Length, 100)];
  }

  private async Task TryPersistEvidenceAsync(
    DeviceEnrollment enrollment,
    int enrollmentVersion,
    string? model,
    string? firmware,
    TreadmillCapabilities? capabilities,
    DateTimeOffset observedAt,
    CancellationToken cancellationToken)
  {
    if (!maintenanceState.TryBeginMutation()) return;
    try
    {
      using IServiceScope scope = scopeFactory.CreateScope();
      bool preserveHardwareVerification = enrollment.Evidence == TreadmillCapabilityEvidence.HardwareVerified;
      await scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>().UpdateEvidenceAsync(
        enrollment.Id,
        enrollmentVersion,
        model,
        firmware,
        preserveHardwareVerification
          ? MergeVerifiedCapabilities(enrollment.Capabilities, capabilities)
          : capabilities,
        preserveHardwareVerification
          ? TreadmillCapabilityEvidence.HardwareVerified
          : TreadmillCapabilityEvidence.PassivelyObserved,
        observedAt,
        cancellationToken);
    }
    catch (DbUpdateConcurrencyException)
    {
      // Reconciliation has already replaced this enrollment generation.
    }
    finally
    {
      maintenanceState.EndMutation();
    }
  }

  private static TreadmillCapabilities? MergeVerifiedCapabilities(
    TreadmillCapabilities? verified,
    TreadmillCapabilities? reported)
  {
    if (verified is null) return reported;
    if (reported is null) return verified;
    return new TreadmillCapabilities(
      verified.CanSetSpeedRemotely,
      verified.CanSetInclineRemotely,
      verified.CanPauseRemotely,
      verified.CanStopRemotely,
      verified.CanStartRemotely,
      reported.ReportsSpeedTargetSupport,
      reported.ReportsInclineTargetSupport,
      reported.ReportsStandardStartResume,
      verified.SpeedRange ?? reported.SpeedRange,
      verified.InclineRange ?? reported.InclineRange);
  }

  private async Task<TreadmillCapabilities> ReadFtmsCapabilitiesAsync(
    IBleConnection connection,
    IReadOnlyList<BleService> services,
    CancellationToken cancellationToken)
  {
    FtmsReportedFeatures features = default;
    BleCharacteristic? feature = FindCharacteristic(services, Uuids.FtmsService, Uuids.FitnessMachineFeature);
    if (feature?.CanRead == true)
    {
      ReadOnlyMemory<byte> value = await ReadBoundedAsync(
        connection,
        Uuids.FtmsService,
        Uuids.FitnessMachineFeature,
        cancellationToken);
      if (!FtmsCapabilityParser.TryParseFeatures(value.Span, out features))
      {
        throw new InvalidDataException("FTMS feature data was invalid.");
      }
    }

    TreadmillOperatingRange? speedRange = await TryReadRangeAsync(
      connection,
      services,
      Uuids.SupportedSpeedRange,
      FtmsCapabilityParser.TryParseSupportedSpeedRange,
      cancellationToken);
    TreadmillOperatingRange? inclineRange = await TryReadRangeAsync(
      connection,
      services,
      Uuids.SupportedInclinationRange,
      FtmsCapabilityParser.TryParseSupportedInclinationRange,
      cancellationToken);
    bool hasControlPoint = FindCharacteristic(
      services,
      Uuids.FtmsService,
      Uuids.FitnessMachineControlPoint) is not null;
    return features.ToUnverifiedCapabilities(hasControlPoint, speedRange, inclineRange);
  }

  private async Task<TreadmillOperatingRange?> TryReadRangeAsync(
    IBleConnection connection,
    IReadOnlyList<BleService> services,
    Guid characteristicUuid,
    RangeParser parser,
    CancellationToken cancellationToken)
  {
    BleCharacteristic? characteristic = FindCharacteristic(services, Uuids.FtmsService, characteristicUuid);
    if (characteristic?.CanRead != true) return null;
    ReadOnlyMemory<byte> value = await ReadBoundedAsync(
      connection,
      Uuids.FtmsService,
      characteristicUuid,
      cancellationToken);
    if (!parser(value.Span, out TreadmillOperatingRange range))
    {
      throw new InvalidDataException($"FTMS range {characteristicUuid:D} was invalid.");
    }

    return range;
  }

  private void UpdateTreadmillTelemetry(
    FtmsTreadmillData data,
    DateTimeOffset observedAt,
    long generation)
  {
    lock (_sync)
    {
      if (_snapshot.Treadmill.ConnectionGeneration != generation) return;
      double speed = data.InstantaneousSpeedKph ?? _snapshot.TreadmillTelemetry?.SpeedKph ?? 0;
      double incline = data.InclinationPercent ?? _snapshot.TreadmillTelemetry?.InclinePercent ?? 0;
      _snapshot = _snapshot with
      {
        CapturedAt = timeProvider.GetUtcNow(),
        TreadmillTelemetry = new TreadmillTelemetry(observedAt, speed, incline),
        Treadmill = _snapshot.Treadmill with { LastObservedAt = observedAt, Fault = null },
      };
    }
  }

  private void UpdateCapabilities(TreadmillCapabilities capabilities)
  {
    lock (_sync)
    {
      _snapshot = _snapshot with { ReportedCapabilities = capabilities };
    }
  }

  private void OnPrimaryTelemetry(
    DeviceEnrollment enrollment,
    long generation,
    DateTimeOffset observedAt,
    ConnectionAttemptRuntime attempt)
  {
    attempt.ObserveTelemetry(observedAt);
    ReliabilityIncidentRuntime? incident;
    lock (_sync)
    {
      if (!_reliabilityIncidents.Remove(enrollment.Id, out incident)) return;
    }

    EnqueueReliabilityWrite(new ResolveReliabilityWrite(
      enrollment.Id,
      generation,
      0,
      incident.MaximumReconnectDelay,
      observedAt));
  }

  private void RecordReliabilityFailure(
    DeviceEnrollment enrollment,
    long generation,
    BleReliabilityFailureKind failureKind,
    string sanitizedFault,
    TimeSpan reconnectDelay,
    DateTimeOffset occurredAtUtc)
  {
    lock (_sync)
    {
      if (_reliabilityIncidents.TryGetValue(enrollment.Id, out ReliabilityIncidentRuntime? incident))
      {
        _reliabilityIncidents[enrollment.Id] = incident with
        {
          FailedAttemptCount = incident.FailedAttemptCount + 1,
          MaximumReconnectDelay = reconnectDelay > incident.MaximumReconnectDelay
            ? reconnectDelay
            : incident.MaximumReconnectDelay,
        };
      }
      else
      {
        _reliabilityIncidents[enrollment.Id] = new ReliabilityIncidentRuntime(1, reconnectDelay);
      }
    }

    EnqueueReliabilityWrite(new BeginReliabilityWrite(
      enrollment.Id,
      enrollment.Role,
      enrollment.DisplayName,
      generation,
      failureKind,
      sanitizedFault,
      reconnectDelay,
      occurredAtUtc));
  }

  private void EnqueueReliabilityWrite(ReliabilityWrite write)
  {
    if (!_reliabilityWrites.Writer.TryWrite(write))
    {
      logger.LogWarning("The bounded BLE reliability recorder dropped an event while persistence was unavailable.");
    }
  }

  private async Task RunReliabilityWriterAsync(CancellationToken cancellationToken)
  {
    await foreach (ReliabilityWrite write in _reliabilityWrites.Reader.ReadAllAsync(cancellationToken))
    {
      for (var attempt = 1; attempt <= 3; attempt++)
      {
        try
        {
          while (!maintenanceState.TryBeginMutation())
            await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken);
          try
          {
            using IServiceScope scope = scopeFactory.CreateScope();
            IBleReliabilityStore store = scope.ServiceProvider.GetRequiredService<IBleReliabilityStore>();
            DateTimeOffset referenceTime;
            if (write is BeginReliabilityWrite begin)
            {
              await store.BeginOrContinueIncidentAsync(
                begin.EnrollmentId,
                begin.Role,
                begin.DisplayName,
                begin.ConnectionGeneration,
                begin.FailureKind,
                begin.SanitizedFault,
                begin.ReconnectDelay,
                begin.OccurredAtUtc,
                cancellationToken);
              referenceTime = begin.OccurredAtUtc;
            }
            else if (write is ResolveReliabilityWrite resolve)
            {
              await store.ResolveIncidentAsync(
                resolve.EnrollmentId,
                resolve.ConnectionGeneration,
                resolve.AdditionalFailedAttempts,
                resolve.MaximumReconnectDelay,
                resolve.RecoveredAtUtc,
                cancellationToken);
              referenceTime = resolve.RecoveredAtUtc;
            }
            else continue;
            await store.PruneRecoveredBeforeAsync(referenceTime - ReliabilityRetention, cancellationToken);
          }
          finally
          {
            maintenanceState.EndMutation();
          }
          break;
        }
        catch (Exception exception) when (attempt < 3)
        {
          logger.LogWarning(exception, "A sanitized BLE reliability write failed; retrying bounded persistence attempt {Attempt}.", attempt);
          await Task.Delay(TimeSpan.FromMilliseconds(100 * attempt), cancellationToken);
        }
        catch (Exception exception)
        {
          logger.LogWarning(exception, "A sanitized BLE reliability incident could not be persisted after bounded retries.");
        }
      }
    }
  }

  private void UpdateConnection(
    DeviceEnrollment enrollment,
    DeviceConnectionState state,
    long generation,
    string? fault)
  {
    var connection = new DeviceConnectionSnapshot(
      enrollment.Role,
      state,
      generation,
      enrollment.DisplayName,
      enrollment.ProtocolId,
      enrollment.TelemetryMode?.ToString(),
      LastObserved(enrollment.Id, enrollment.Role),
      fault,
      enrollment.ModelNumber,
      enrollment.FirmwareRevision,
      enrollment.Evidence,
      enrollment.Capabilities);
    lock (_sync)
    {
      if (enrollment.Role == DeviceRole.Treadmill)
      {
        _snapshot = _snapshot with { CapturedAt = timeProvider.GetUtcNow(), Treadmill = connection };
      }
      else
      {
        _heartRateSources[enrollment.Id] = _heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime)
          ? runtime with { Enrollment = enrollment, Connection = connection }
          : new HeartRateRuntime(enrollment, connection, null, null);
      }
    }
  }

  private DateTimeOffset? LastObserved(Guid enrollmentId, DeviceRole role)
  {
    lock (_sync)
    {
      return role == DeviceRole.Treadmill
        ? _snapshot.Treadmill.LastObservedAt
        : _heartRateSources.GetValueOrDefault(enrollmentId)?.Connection.LastObservedAt;
    }
  }

  private void SetDisconnected(Guid enrollmentId, DeviceRole role)
  {
    lock (_sync)
    {
      DeviceConnectionSnapshot disconnected = EmptyConnection(role) with
      {
        ConnectionGeneration = role == DeviceRole.Treadmill
          ? _snapshot.Treadmill.ConnectionGeneration
          : _heartRateSources.GetValueOrDefault(enrollmentId)?.Connection.ConnectionGeneration ?? 0,
      };
      if (role == DeviceRole.Treadmill)
      {
        _snapshot = _snapshot with { CapturedAt = timeProvider.GetUtcNow(), Treadmill = disconnected, TreadmillTelemetry = null, ReportedCapabilities = null };
      }
      else if (_heartRateSources.TryGetValue(enrollmentId, out HeartRateRuntime? runtime))
      {
        _heartRateSources[enrollmentId] = runtime with
        {
          Connection = disconnected with { DisplayName = runtime.Enrollment.DisplayName, ProtocolId = runtime.Enrollment.ProtocolId },
          BeatsPerMinute = null,
          ObservedAt = null,
        };
      }
    }
  }

  private DeviceTelemetrySnapshot BuildSnapshotLocked(Guid? profileId)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    HeartRateSourceSnapshot[] sources = _heartRateSources.Values
      .Select(runtime => new HeartRateSourceSnapshot(
        runtime.Enrollment.Id,
        runtime.Enrollment.DisplayName,
        runtime.Enrollment.HeartRateDeviceKind ?? HeartRateDeviceKind.Sensor,
        runtime.Enrollment.HeartRateDeviceFamily ?? HeartRateDeviceFamily.Other,
        runtime.Connection.State,
        runtime.Connection.ConnectionGeneration,
        runtime.BeatsPerMinute,
        runtime.ObservedAt,
        runtime.Connection.Fault,
        runtime.BatteryPercent,
        runtime.BatteryObservedAt))
      .OrderBy(source => source.DisplayName, StringComparer.OrdinalIgnoreCase)
      .ToArray();
    HeartRateSourceSnapshot? selected = HeartRateSourceSelector.Select(
      sources,
      _assignments,
      profileId,
      now,
      HeartRateFreshnessLimit);
    HeartRateSourceSnapshot? displayed = selected ?? SelectDisplayedSource(sources, profileId);
    Guid selectionKey = profileId ?? Guid.Empty;
    Guid? selectedId = selected?.EnrollmentId;
    SelectionRuntime selection = _profileSelections.GetValueOrDefault(selectionKey) ?? new SelectionRuntime(null, 0);
    if (selection.EnrollmentId != selectedId)
    {
      selection = new SelectionRuntime(selectedId, selection.Generation + 1);
      _profileSelections[selectionKey] = selection;
    }
    DeviceConnectionSnapshot connection = displayed is null
      ? EmptyConnection(DeviceRole.HeartRate)
      : new DeviceConnectionSnapshot(
        DeviceRole.HeartRate,
        displayed.State,
        displayed.ConnectionGeneration,
        displayed.DisplayName,
        "bluetooth-heart-rate",
        displayed.Kind.ToString(),
        displayed.ObservedAt,
        displayed.Fault);
    return _snapshot with
    {
      CapturedAt = now,
      HeartRate = connection,
      HeartRateBpm = displayed?.BeatsPerMinute,
      HeartRateObservedAt = displayed?.ObservedAt,
      HeartRateSources = sources,
      SelectedHeartRateEnrollmentId = selectedId,
      SelectedHeartRateDeviceKind = displayed?.Kind,
      SelectedHeartRateDeviceFamily = displayed?.Family,
      HeartRateSelectionGeneration = selection.Generation,
      HeartRateSelectionReason = selected is not null
        ? "Selected the highest-priority fresh sensor assigned to this runner."
        : displayed is not null
          ? "Sensor is enrolled but a fresh pulse sample is not available yet."
          : "No automatic heart-rate sensor is assigned to this runner.",
      SelectedHeartRateBatteryPercent = displayed?.BatteryPercent,
      SelectedHeartRateBatteryObservedAt = displayed?.BatteryObservedAt,
    };
  }

  private HeartRateSourceSnapshot? SelectDisplayedSource(
    IReadOnlyList<HeartRateSourceSnapshot> sources,
    Guid? profileId)
  {
    IEnumerable<HeartRateSourceSnapshot> eligible = sources;
    if (profileId is not null)
    {
      HashSet<Guid> ids = _assignments
        .Where(item => item.UserProfileId == profileId && item.AutoConnect)
        .Select(item => item.DeviceEnrollmentId)
        .ToHashSet();
      eligible = ids.Count > 0
        ? eligible.Where(source => ids.Contains(source.EnrollmentId))
        : eligible.Where(source => _assignments.All(item => item.DeviceEnrollmentId != source.EnrollmentId));
    }
    return eligible
      .OrderBy(source => source.State == DeviceConnectionState.Ready ? 0 : 1)
      .ThenBy(source => source.Family == HeartRateDeviceFamily.Polar ? 0 : source.Kind == HeartRateDeviceKind.ChestStrap ? 1 : 2)
      .ThenBy(source => source.EnrollmentId)
      .FirstOrDefault();
  }

  private static void RequireCharacteristic(
    IReadOnlyList<BleService> services,
    Guid serviceUuid,
    Guid characteristicUuid,
    bool requireNotify)
  {
    BleCharacteristic characteristic = FindCharacteristic(services, serviceUuid, characteristicUuid)
      ?? throw new InvalidDataException($"Required BLE characteristic {characteristicUuid:D} is missing.");
    if (requireNotify && !characteristic.CanNotify)
    {
      throw new InvalidDataException($"BLE characteristic {characteristicUuid:D} cannot notify.");
    }
  }

  private static BleCharacteristic? FindCharacteristic(
    IReadOnlyList<BleService> services,
    Guid serviceUuid,
    Guid characteristicUuid) => services
    .FirstOrDefault(service => service.Uuid == serviceUuid)?
    .Characteristics.FirstOrDefault(characteristic => characteristic.CharacteristicUuid == characteristicUuid);

  private static string SanitizeFault(Exception exception) => exception switch
  {
    WindowsBleDisconnectedException => "The BLE device disconnected.",
    BleTelemetrySilenceException => "BLE telemetry stopped arriving.",
    InvalidDataException => exception.Message,
    FormatException => "The device sent invalid telemetry.",
    TimeoutException => "The BLE operation timed out.",
    _ => "The BLE device is unavailable.",
  };

  private static BleReliabilityFailureKind ClassifyFailure(Exception exception) => exception switch
  {
    WindowsBleDisconnectedException => BleReliabilityFailureKind.NativeDisconnected,
    BleTelemetrySilenceException => BleReliabilityFailureKind.TelemetrySilent,
    TimeoutException => BleReliabilityFailureKind.GattTimeout,
    InvalidDataException invalid when invalid.Message.StartsWith("Required BLE characteristic", StringComparison.Ordinal) =>
      BleReliabilityFailureKind.RequiredCharacteristicMissing,
    InvalidDataException or FormatException => BleReliabilityFailureKind.InvalidTelemetry,
    IOException => BleReliabilityFailureKind.NotificationEnded,
    _ => BleReliabilityFailureKind.AdapterUnavailable,
  };

  private static DeviceTelemetrySnapshot EmptySnapshot(DateTimeOffset now) => new(
    now,
    EmptyConnection(DeviceRole.Treadmill),
    EmptyConnection(DeviceRole.HeartRate),
    null,
    null,
    null,
    null);

  private static DeviceConnectionSnapshot EmptyConnection(DeviceRole role) => new(
    role,
    DeviceConnectionState.Disconnected,
    0,
    null,
    null,
    null,
    null,
    null);

  private sealed record DeviceWorker(
    VersionedDeviceEnrollment Enrollment,
    CancellationTokenSource Cancellation,
    Task Task)
  {
    public Guid EnrollmentId => Enrollment.Enrollment.Id;
    public DeviceRole Role => Enrollment.Enrollment.Role;
  }

  private sealed record HeartRateRuntime(
    DeviceEnrollment Enrollment,
    DeviceConnectionSnapshot Connection,
    ushort? BeatsPerMinute,
    DateTimeOffset? ObservedAt,
    byte? BatteryPercent = null,
    DateTimeOffset? BatteryObservedAt = null);

  private sealed class ConnectionAttemptRuntime
  {
    public DateTimeOffset? FirstTelemetryAtUtc { get; private set; }
    public DateTimeOffset? LastTelemetryAtUtc { get; private set; }
    public int TelemetrySampleCount { get; private set; }

    public void ObserveTelemetry(DateTimeOffset observedAt)
    {
      FirstTelemetryAtUtc ??= observedAt;
      LastTelemetryAtUtc = observedAt;
      TelemetrySampleCount++;
    }

    public bool IsStableAt(DateTimeOffset now) =>
      FirstTelemetryAtUtc is { } first &&
      LastTelemetryAtUtc is { } last &&
      TelemetrySampleCount >= 2 &&
      now - first >= BleReconnectPolicy.StableConnectionThreshold &&
      now - last <= HeartRateFreshnessLimit;
  }

  private sealed record ReliabilityIncidentRuntime(
    int FailedAttemptCount,
    TimeSpan MaximumReconnectDelay);

  private abstract record ReliabilityWrite;

  private sealed record BeginReliabilityWrite(
    Guid EnrollmentId,
    DeviceRole Role,
    string DisplayName,
    long ConnectionGeneration,
    BleReliabilityFailureKind FailureKind,
    string SanitizedFault,
    TimeSpan ReconnectDelay,
    DateTimeOffset OccurredAtUtc) : ReliabilityWrite;

  private sealed record ResolveReliabilityWrite(
    Guid EnrollmentId,
    long ConnectionGeneration,
    int AdditionalFailedAttempts,
    TimeSpan MaximumReconnectDelay,
    DateTimeOffset RecoveredAtUtc) : ReliabilityWrite;

  private sealed record SelectionRuntime(Guid? EnrollmentId, long Generation);

  private delegate bool RangeParser(ReadOnlySpan<byte> payload, out TreadmillOperatingRange range);

  private static class Uuids
  {
    public static readonly Guid FtmsService = Expand(0x1826);
    public static readonly Guid FitnessMachineFeature = Expand(0x2ACC);
    public static readonly Guid TreadmillData = Expand(0x2ACD);
    public static readonly Guid SupportedSpeedRange = Expand(0x2AD4);
    public static readonly Guid SupportedInclinationRange = Expand(0x2AD5);
    public static readonly Guid FitnessMachineControlPoint = Expand(0x2AD9);
    public static readonly Guid HeartRateService = Expand(0x180D);
    public static readonly Guid HeartRateMeasurement = Expand(0x2A37);
    public static readonly Guid BatteryService = Expand(0x180F);
    public static readonly Guid BatteryLevel = Expand(0x2A19);
    public static readonly Guid DeviceInformationService = Expand(0x180A);
    public static readonly Guid ModelNumber = Expand(0x2A24);
    public static readonly Guid FirmwareRevision = Expand(0x2A26);
    public static readonly Guid VendorService = Expand(0xFFF0);
    public static readonly Guid VendorStatus = Expand(0xFFF4);

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }

  private sealed class BleTelemetrySilenceException(bool initial, Exception innerException)
    : TimeoutException(
      initial
        ? "The BLE subscription did not publish its initial telemetry in time."
        : "The BLE telemetry stream became silent.",
      innerException);
}
