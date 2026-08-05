using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Ftms;
using TreadmillRunner.Protocols.HeartRate;
using TreadmillRunner.Protocols.Omega;
using Microsoft.EntityFrameworkCore;
using System.Text;
using TreadmillRunner.Gateway.Operations;

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
  private static readonly TimeSpan MaximumReconnectDelay = TimeSpan.FromSeconds(30);
  private static readonly TimeSpan HeartRateFreshnessLimit = TimeSpan.FromSeconds(5);
  private const int MaximumHeartRateWorkers = 8;
  private readonly object _sync = new();
  private readonly SemaphoreSlim _reconcileGate = new(1, 1);
  private readonly Dictionary<Guid, DeviceWorker> _workers = [];
  private readonly Dictionary<Guid, HeartRateRuntime> _heartRateSources = [];
  private readonly Dictionary<Guid, SelectionRuntime> _profileSelections = [];
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
            SetDisconnected(worker.EnrollmentId, worker.Role);
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
    var reconnectDelay = TimeSpan.FromSeconds(1);
    while (!cancellationToken.IsCancellationRequested)
    {
      long generation = Interlocked.Increment(ref _nextGeneration);
      try
      {
        UpdateConnection(enrollment, DeviceConnectionState.Connecting, generation, fault: null);
        await using IBleConnection connection = await transport.ConnectAsync(
          enrollment.DeviceId,
          cancellationToken);
        UpdateConnection(enrollment, DeviceConnectionState.DiscoveringServices, generation, fault: null);
        IReadOnlyList<BleService> services = await connection.DiscoverServicesAsync(cancellationToken);
        UpdateConnection(enrollment, DeviceConnectionState.Subscribing, generation, fault: null);

        if (enrollment.Role == DeviceRole.Treadmill)
        {
          await RunTreadmillAsync(connection, enrollment, stored.Version, services, generation, cancellationToken);
        }
        else
        {
          await RunHeartRateAsync(connection, enrollment, stored.Version, services, generation, cancellationToken);
        }

        throw new IOException("The BLE notification stream ended unexpectedly.");
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        logger.LogWarning(
          exception,
          "Read-only {DeviceRole} connection failed; reconnecting without issuing a treadmill command.",
          enrollment.Role);
        UpdateConnection(
          enrollment,
          DeviceConnectionState.Faulted,
          generation,
          SanitizeFault(exception));
      }

      try
      {
        UpdateConnection(enrollment, DeviceConnectionState.Reconnecting, generation, fault: null);
        await Task.Delay(reconnectDelay, timeProvider, cancellationToken);
        reconnectDelay = TimeSpan.FromSeconds(Math.Min(
          MaximumReconnectDelay.TotalSeconds,
          reconnectDelay.TotalSeconds * 2));
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        break;
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
      await foreach (BleNotification notification in connection.SubscribeAsync(
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
    await foreach (BleNotification notification in connection.SubscribeAsync(
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
    CancellationToken cancellationToken)
  {
    RequireCharacteristic(services, Uuids.HeartRateService, Uuids.HeartRateMeasurement, requireNotify: true);
    UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
    (string? model, string? firmware) = await ReadDeviceInformationAsync(connection, services, cancellationToken);
    var evidencePersisted = false;
    await foreach (BleNotification notification in connection.SubscribeAsync(
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

  private async Task<(string? Model, string? Firmware)> ReadDeviceInformationAsync(
    IBleConnection connection,
    IReadOnlyList<BleService> services,
    CancellationToken cancellationToken)
  {
    string? model = await TryReadStringAsync(connection, services, Uuids.ModelNumber, cancellationToken);
    string? firmware = await TryReadStringAsync(connection, services, Uuids.FirmwareRevision, cancellationToken);
    return (model, firmware);
  }

  private static async Task<string?> TryReadStringAsync(
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
    ReadOnlyMemory<byte> value = await connection.ReadAsync(
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
      ReadOnlyMemory<byte> value = await connection.ReadAsync(
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
    ReadOnlyMemory<byte> value = await connection.ReadAsync(
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
        runtime.Connection.Fault))
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
    InvalidDataException => exception.Message,
    FormatException => "The device sent invalid telemetry.",
    TimeoutException => "The BLE operation timed out.",
    _ => "The BLE device is unavailable.",
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
    DateTimeOffset? ObservedAt);

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
    public static readonly Guid DeviceInformationService = Expand(0x180A);
    public static readonly Guid ModelNumber = Expand(0x2A24);
    public static readonly Guid FirmwareRevision = Expand(0x2A26);
    public static readonly Guid VendorService = Expand(0xFFF0);
    public static readonly Guid VendorStatus = Expand(0xFFF4);

    private static Guid Expand(ushort value) =>
      Guid.Parse($"0000{value:x4}-0000-1000-8000-00805f9b34fb");
  }
}
