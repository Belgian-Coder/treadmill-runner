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
  int ActiveReliabilityFailureCount(Guid enrollmentId) => 0;
  DeviceTelemetrySnapshot CurrentForProfile(Guid? profileId) => Current;
  Task RefreshAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
  Task PrepareForRunAsync(
    Guid profileId,
    bool requiresHeartRate,
    CancellationToken cancellationToken = default) => Task.CompletedTask;
  Task HoldRunConnectionsAsync(
    Guid profileId,
    bool requiresHeartRate,
    CancellationToken cancellationToken = default) => Task.CompletedTask;
  Task ReleaseRunConnectionsAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
  Task<bool> RetryConnectionAsync(Guid enrollmentId, CancellationToken cancellationToken = default) =>
    Task.FromResult(false);
  Task<bool> DisconnectAsync(Guid enrollmentId, CancellationToken cancellationToken = default) =>
    Task.FromResult(false);
}

public sealed class ReadOnlyDeviceCoordinator(
  IServiceScopeFactory scopeFactory,
  IBleCentralTransport transport,
  IBleAdvertisementBroker advertisementBroker,
  TimeProvider timeProvider,
  IApplicationMaintenanceState maintenanceState,
  ILogger<ReadOnlyDeviceCoordinator> logger) : BackgroundService, IReadOnlyDeviceCoordinator
{
  private static readonly TimeSpan EnrollmentRefreshInterval = TimeSpan.FromSeconds(2);
  private static readonly TimeSpan GattOperationTimeout = TimeSpan.FromSeconds(15);
  private static readonly TimeSpan InitialNotificationTimeout = TimeSpan.FromSeconds(15);
  private static readonly TimeSpan TelemetrySilenceTimeout = TimeSpan.FromSeconds(30);
  private static readonly TimeSpan ReconnectDiscoveryTimeout = TimeSpan.FromSeconds(5);
  private static readonly TimeSpan ReliabilityRetention = TimeSpan.FromDays(90);
  private static readonly TimeSpan ReliabilityPruneInterval = TimeSpan.FromHours(1);
  private static readonly TimeSpan ReliabilityLagThreshold = TimeSpan.FromSeconds(1);
  private static readonly TimeSpan ReliabilityLogThrottle = TimeSpan.FromMinutes(1);
  private static readonly TimeSpan HeartRateFreshnessLimit = TimeSpan.FromSeconds(5);
  private static readonly TimeSpan PreparationDemandDuration = TimeSpan.FromMinutes(2);
  private static readonly TimeSpan ManualConnectionDemandDuration = TimeSpan.FromMinutes(2);
  private const int MaximumHeartRateWorkers = 8;
  private readonly object _sync = new();
  private readonly SemaphoreSlim _reconcileGate = new(1, 1);
  private readonly Dictionary<Guid, DeviceWorker> _workers = [];
  private readonly Dictionary<Guid, HeartRateRuntime> _heartRateSources = [];
  private readonly Dictionary<Guid, SelectionRuntime> _profileSelections = [];
  private readonly Dictionary<Guid, ReliabilityIncidentRuntime> _reliabilityIncidents = [];
  private readonly Dictionary<Guid, DateTimeOffset> _manualConnectionDemandExpirations = [];
  private readonly HashSet<Guid> _explicitlyDisconnectedEnrollmentIds = [];
  private readonly BleReconnectPolicy _reconnectPolicy = new();
  private readonly Channel<ReliabilityWriteEnvelope> _reliabilityWrites = Channel.CreateUnbounded<ReliabilityWriteEnvelope>(
    new UnboundedChannelOptions
    {
      SingleReader = true,
      SingleWriter = false,
    });
  private readonly Channel<EvidenceKey> _evidenceSignals = Channel.CreateUnbounded<EvidenceKey>(
    new UnboundedChannelOptions
    {
      SingleReader = true,
      SingleWriter = false,
    });
  private readonly object _evidenceSync = new();
  private readonly Dictionary<EvidenceKey, EvidenceWrite> _pendingEvidenceWrites = [];
  private readonly HashSet<EvidenceKey> _queuedEvidenceKeys = [];
  private IReadOnlyList<HeartRateDeviceAssignment> _assignments = [];
  private long _nextGeneration;
  private bool _hasTreadmillEnrollment;
  private RunConnectionDemand? _runConnectionDemand;
  private DateTimeOffset? _lastReliabilityPruneAtUtc;
  private DateTimeOffset? _lastReliabilityPressureLogAtUtc;
  private long _reliabilityDroppedCount;
  private long _reliabilityLaggedCount;
  private DeviceTelemetrySnapshot _snapshot = EmptySnapshot(timeProvider.GetUtcNow());

  internal ReliabilityWriterMetrics ReliabilityMetrics => new(
    Interlocked.Read(ref _reliabilityDroppedCount),
    Interlocked.Read(ref _reliabilityLaggedCount),
    0);

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

  public int ActiveReliabilityFailureCount(Guid enrollmentId)
  {
    lock (_sync)
    {
      return _reliabilityIncidents.TryGetValue(enrollmentId, out ReliabilityIncidentRuntime? incident)
        ? incident.FailedAttemptCount
        : 0;
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

  public async Task PrepareForRunAsync(
    Guid profileId,
    bool requiresHeartRate,
    CancellationToken cancellationToken = default)
  {
    if (profileId == Guid.Empty) throw new ArgumentException("A runner profile is required.", nameof(profileId));
    cancellationToken.ThrowIfCancellationRequested();
    lock (_sync)
    {
      _runConnectionDemand = new RunConnectionDemand(
        profileId,
        requiresHeartRate,
        timeProvider.GetUtcNow() + PreparationDemandDuration);
      _explicitlyDisconnectedEnrollmentIds.Clear();
    }
    await ReconcileWorkersAsync(cancellationToken);
  }

  public async Task HoldRunConnectionsAsync(
    Guid profileId,
    bool requiresHeartRate,
    CancellationToken cancellationToken = default)
  {
    if (profileId == Guid.Empty) throw new ArgumentException("A runner profile is required.", nameof(profileId));
    cancellationToken.ThrowIfCancellationRequested();
    lock (_sync)
    {
      _runConnectionDemand = new RunConnectionDemand(profileId, requiresHeartRate, ExpiresAtUtc: null);
      _explicitlyDisconnectedEnrollmentIds.Clear();
    }
    await ReconcileWorkersAsync(cancellationToken);
  }

  public async Task ReleaseRunConnectionsAsync(CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    lock (_sync)
    {
      _runConnectionDemand = null;
      _manualConnectionDemandExpirations.Clear();
      _explicitlyDisconnectedEnrollmentIds.Clear();
    }
    await ReconcileWorkersAsync(cancellationToken);
  }

  public async Task<bool> RetryConnectionAsync(
    Guid enrollmentId,
    CancellationToken cancellationToken = default)
  {
    if (enrollmentId == Guid.Empty) return false;
    cancellationToken.ThrowIfCancellationRequested();
    VersionedDeviceEnrollment? enrollment;
    using (IServiceScope scope = scopeFactory.CreateScope())
    {
      enrollment = (await scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>()
          .ListActiveAsync(cancellationToken))
        .SingleOrDefault(item => item.Enrollment.Id == enrollmentId);
    }
    if (enrollment is null) return false;
    DeviceWorker? worker = null;
    await _reconcileGate.WaitAsync(cancellationToken);
    try
    {
      lock (_sync)
      {
        _manualConnectionDemandExpirations[enrollmentId] =
          timeProvider.GetUtcNow() + ManualConnectionDemandDuration;
        _explicitlyDisconnectedEnrollmentIds.Remove(enrollmentId);
        if (_workers.Remove(enrollmentId, out DeviceWorker? existing))
        {
          worker = existing;
          existing.Cancellation.Cancel();
        }
      }

      if (worker is not null)
      {
        await worker.Task;
        worker.Cancellation.Dispose();
      }
    }
    finally
    {
      _reconcileGate.Release();
    }

    // An explicit Connect also performs the same bounded passive discovery as
    // Auto scan. This refreshes the Windows BLE cache before the fresh GATT
    // worker starts, so an awake Polar/Garmin does not require a separate scan.
    await PassivelyRediscoverAsync(enrollment.Enrollment, cancellationToken);
    await ReconcileWorkersAsync(cancellationToken);
    return true;
  }

  public async Task<bool> DisconnectAsync(
    Guid enrollmentId,
    CancellationToken cancellationToken = default)
  {
    if (enrollmentId == Guid.Empty) return false;
    cancellationToken.ThrowIfCancellationRequested();
    if (!await EnrollmentExistsAsync(enrollmentId, cancellationToken)) return false;
    DeviceWorker? worker = null;
    await _reconcileGate.WaitAsync(cancellationToken);
    try
    {
      lock (_sync)
      {
        _manualConnectionDemandExpirations.Remove(enrollmentId);
        _explicitlyDisconnectedEnrollmentIds.Add(enrollmentId);
        if (_workers.Remove(enrollmentId, out DeviceWorker? existing))
        {
          worker = existing;
          existing.Cancellation.Cancel();
        }
      }

      if (worker is not null)
      {
        await worker.Task;
        worker.Cancellation.Dispose();
      }
    }
    finally
    {
      _reconcileGate.Release();
    }

    return true;
  }

  private async Task<bool> EnrollmentExistsAsync(Guid enrollmentId, CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    IReadOnlyList<VersionedDeviceEnrollment> enrollments = await scope.ServiceProvider
      .GetRequiredService<IDeviceEnrollmentStore>()
      .ListActiveAsync(cancellationToken);
    return enrollments.Any(enrollment => enrollment.Enrollment.Id == enrollmentId);
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    Task reliabilityWriter = RunReliabilityWriterAsync(stoppingToken);
    Task evidenceWriter = RunEvidenceWriterAsync(stoppingToken);
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
      _evidenceSignals.Writer.TryComplete();
      try
      {
        await reliabilityWriter;
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
      }
      try
      {
        await evidenceWriter;
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

      DateTimeOffset now = timeProvider.GetUtcNow();
      RunConnectionDemand? runDemand;
      HashSet<Guid> manuallyDemandedIds;
      HashSet<Guid> explicitlyDisconnectedIds;
      lock (_sync)
      {
        if (_runConnectionDemand?.ExpiresAtUtc is { } expiresAt && expiresAt <= now)
          _runConnectionDemand = null;
        foreach (Guid expired in _manualConnectionDemandExpirations
          .Where(item => item.Value <= now)
          .Select(static item => item.Key)
          .ToArray())
        {
          _manualConnectionDemandExpirations.Remove(expired);
        }
        runDemand = _runConnectionDemand;
        manuallyDemandedIds = _manualConnectionDemandExpirations.Keys.ToHashSet();
        explicitlyDisconnectedIds = _explicitlyDisconnectedEnrollmentIds.ToHashSet();
      }

      var desiredIds = new HashSet<Guid>(manuallyDemandedIds);
      if (runDemand is not null)
      {
        desiredIds.UnionWith(enrollments
          .Where(static enrollment => enrollment.Enrollment.Role == DeviceRole.Treadmill)
          .Select(static enrollment => enrollment.Enrollment.Id));
        HeartRateDeviceAssignment[] profileAssignments = assignments
          .Where(assignment => assignment.UserProfileId == runDemand.ProfileId)
          .ToArray();
        Guid[] automaticHeartRateIds = profileAssignments
          // Keep every sensor assigned to this runner available during a run.
          // The old HR-target/AutoConnect filter dropped even the preferred
          // Polar on ordinary workouts and removed Garmin failover.
          .OrderBy(static assignment => assignment.Priority)
          .Select(static assignment => assignment.DeviceEnrollmentId)
          .Distinct()
          .Take(MaximumHeartRateWorkers)
          .ToArray();
        if (automaticHeartRateIds.Length > 0)
        {
          desiredIds.UnionWith(automaticHeartRateIds);
        }
        else if (profileAssignments.Length == 0)
        {
          HashSet<Guid> assignedIds = assignments.Select(static assignment => assignment.DeviceEnrollmentId).ToHashSet();
          desiredIds.UnionWith(enrollments
            .Where(enrollment => enrollment.Enrollment.Role == DeviceRole.HeartRate &&
              !assignedIds.Contains(enrollment.Enrollment.Id))
            .Take(MaximumHeartRateWorkers)
            .Select(static enrollment => enrollment.Enrollment.Id));
        }
      }

      desiredIds.ExceptWith(explicitlyDisconnectedIds);
      VersionedDeviceEnrollment[] desiredEnrollments = enrollments
        .Where(enrollment => desiredIds.Contains(enrollment.Enrollment.Id))
        .ToArray();
      var desired = desiredEnrollments.ToDictionary(static enrollment => enrollment.Enrollment.Id);
      List<DeviceWorker> removed = [];
      List<VersionedDeviceEnrollment> added = [];
      lock (_sync)
      {
        _assignments = assignments;
        _hasTreadmillEnrollment = enrollments.Any(static item => item.Enrollment.Role == DeviceRole.Treadmill);
        HashSet<Guid> activeEnrollmentIds = enrollments
          .Select(static item => item.Enrollment.Id)
          .ToHashSet();
        foreach (Guid staleHeartRateId in _heartRateSources.Keys
          .Where(id => !activeEnrollmentIds.Contains(id))
          .ToArray())
        {
          _heartRateSources.Remove(staleHeartRateId);
        }
        foreach (VersionedDeviceEnrollment enrollment in enrollments)
          RefreshEnrollmentMetadata(enrollment.Enrollment);
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
        bool activeDemand = HasActiveRunDemand(failedAt);
        TimeSpan reconnectDelay = _reconnectPolicy.GetDelay(
          enrollment.Id,
          consecutiveFailureCount,
          active: activeDemand);
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
        if (exception is WindowsBleDeviceUnavailableException &&
            await PassivelyRediscoverAsync(enrollment, cancellationToken))
        {
          continue;
        }
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

  private async Task<bool> PassivelyRediscoverAsync(
    DeviceEnrollment enrollment,
    CancellationToken cancellationToken)
  {
    using var discovery = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    discovery.CancelAfter(ReconnectDiscoveryTimeout);
    try
    {
      await foreach (BleAdvertisement advertisement in advertisementBroker
        .ScanAsync(discovery.Token)
        .WithCancellation(discovery.Token))
      {
        if (string.Equals(
          advertisement.DeviceId,
          enrollment.DeviceId,
          StringComparison.OrdinalIgnoreCase))
        {
          logger.LogInformation(
            "Passively rediscovered the enrolled {DeviceRole}; retrying its read-only connection.",
            enrollment.Role);
          return true;
        }
      }
    }
    catch (OperationCanceledException) when (
      !cancellationToken.IsCancellationRequested && discovery.IsCancellationRequested)
    {
      // The peripheral is still absent. The normal reconnect delay remains authoritative.
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogWarning(
        exception,
        "Passive {DeviceRole} rediscovery was unavailable; retaining bounded reconnect backoff.",
        enrollment.Role);
    }

    return false;
  }

  private static bool RequiresWorkerRestart(DeviceEnrollment current, DeviceEnrollment desired) =>
    current.Role != desired.Role ||
    !string.Equals(current.DeviceId, desired.DeviceId, StringComparison.Ordinal) ||
    !string.Equals(current.ProtocolId, desired.ProtocolId, StringComparison.Ordinal) ||
    !string.Equals(current.IdentityFingerprint, desired.IdentityFingerprint, StringComparison.Ordinal) ||
    current.TelemetryMode != desired.TelemetryMode;

  private bool HasActiveRunDemand(DateTimeOffset now)
  {
    lock (_sync)
    {
      return _runConnectionDemand is { } demand &&
        (demand.ExpiresAtUtc is null || demand.ExpiresAtUtc > now);
    }
  }

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
          ProtocolId = enrollment.ProtocolId,
          TelemetryMode = enrollment.TelemetryMode?.ToString(),
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
    else
    {
      _heartRateSources[enrollment.Id] = new HeartRateRuntime(
        enrollment,
        EmptyConnection(DeviceRole.HeartRate) with
        {
          DisplayName = enrollment.DisplayName,
          ProtocolId = enrollment.ProtocolId,
        },
        null,
        null);
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
      var evidencePersisted = false;
      var readyPublished = false;
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

        TreadmillTelemetryUpdateResult update = UpdateTreadmillTelemetry(data, notification.ObservedAt, generation);
        if (update == TreadmillTelemetryUpdateResult.Ignored)
        {
          continue;
        }
        if (!readyPublished)
        {
          UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
          readyPublished = true;
        }
        if (update == TreadmillTelemetryUpdateResult.Primary)
          primaryTelemetryObserved(notification.ObservedAt);
        if (!evidencePersisted && TryEnqueueEvidencePersistence(
          enrollment,
          enrollmentVersion,
          model,
          firmware,
          reported,
          generation,
          notification.ObservedAt))
        {
          evidencePersisted = true;
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
    var vendorReadyPublished = false;
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
          TreadmillTelemetryUpdateResult update = UpdateTreadmillTelemetry(
            new FtmsTreadmillData(0, status.SpeedKph, status.InclinePercent, null),
            notification.ObservedAt,
            generation);
          if (update == TreadmillTelemetryUpdateResult.Ignored)
          {
            continue;
          }
          if (!vendorReadyPublished)
          {
            UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
            vendorReadyPublished = true;
          }
          primaryTelemetryObserved(notification.ObservedAt);
          if (!vendorEvidencePersisted && TryEnqueueEvidencePersistence(
            enrollment,
            enrollmentVersion,
            vendorModel,
            vendorFirmware,
            enrollment.Capabilities,
            generation,
            notification.ObservedAt))
          {
            vendorEvidencePersisted = true;
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
    (string? model, string? firmware) = await ReadDeviceInformationAsync(connection, services, cancellationToken);
    var evidencePersisted = false;
    var readyPublished = false;
    using var batteryCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    Task? batteryTask = null;
    try
    {
      await foreach (BleNotification notification in SubscribeWithWatchdogAsync(
        connection,
        Uuids.HeartRateService,
        Uuids.HeartRateMeasurement,
        cancellationToken))
      {
        HeartRateMeasurement measurement = HeartRateMeasurementParser.Parse(notification.Value.Span);
        HeartRateSignalQuality quality = ClassifyHeartRateSignal(measurement);
        ushort? usableBeatsPerMinute = quality == HeartRateSignalQuality.Valid
          ? measurement.BeatsPerMinute
          : null;
        lock (_sync)
        {
          if (!_heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime) ||
              runtime.Connection.ConnectionGeneration != generation) continue;
          _heartRateSources[enrollment.Id] = runtime with
          {
            BeatsPerMinute = usableBeatsPerMinute,
            ObservedAt = notification.ObservedAt,
            Quality = quality,
            ContactState = MapContactState(measurement.ContactStatus),
            Connection = runtime.Connection with
            {
              LastObservedAt = notification.ObservedAt,
              Fault = HeartRateFault(quality),
            },
          };
        }
        if (quality == HeartRateSignalQuality.Valid && !readyPublished)
        {
          UpdateConnection(enrollment, DeviceConnectionState.Ready, generation, fault: null);
          readyPublished = true;
        }
        if (quality != HeartRateSignalQuality.Valid) continue;
        primaryTelemetryObserved(notification.ObservedAt);
        if (!evidencePersisted && TryEnqueueEvidencePersistence(
          enrollment,
          enrollmentVersion,
          model,
          firmware,
          capabilities: null,
          generation,
          notification.ObservedAt))
        {
          evidencePersisted = true;
        }
        if (batteryTask is null)
        {
          batteryTask = RunOptionalBatteryAsync(
            connection,
            enrollment,
            services,
            generation,
            batteryCancellation.Token);
        }
      }
    }
    finally
    {
      batteryCancellation.Cancel();
      if (batteryTask is not null)
      {
        try
        {
          await batteryTask;
        }
        catch (OperationCanceledException) when (batteryCancellation.IsCancellationRequested)
        {
        }
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

  private static HeartRateSignalQuality ClassifyHeartRateSignal(HeartRateMeasurement measurement) =>
    measurement.ContactStatus == HeartRateContactStatus.NotDetected
      ? HeartRateSignalQuality.ContactLost
      : measurement.BeatsPerMinute is >= 30 and <= 250
        ? HeartRateSignalQuality.Valid
        : HeartRateSignalQuality.Invalid;

  private static string? HeartRateFault(HeartRateSignalQuality quality) => quality switch
  {
    HeartRateSignalQuality.ContactLost => "Heart-rate contact is not detected.",
    HeartRateSignalQuality.Invalid => "The heart-rate sensor sent an unusable pulse value.",
    _ => null,
  };

  private static HeartRateContactState MapContactState(HeartRateContactStatus status) => status switch
  {
    HeartRateContactStatus.NotSupported => HeartRateContactState.NotSupported,
    HeartRateContactStatus.Detected => HeartRateContactState.Detected,
    HeartRateContactStatus.NotDetected => HeartRateContactState.NotDetected,
    _ => HeartRateContactState.Unknown,
  };

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

  private bool TryEnqueueEvidencePersistence(
    DeviceEnrollment enrollment,
    int enrollmentVersion,
    string? model,
    string? firmware,
    TreadmillCapabilities? capabilities,
    long generation,
    DateTimeOffset observedAt)
  {
    // A telemetry callback may outlive the worker generation that produced it.
    // Drop stale evidence rather than allowing an old device session to mutate
    // the current enrollment record.
    if (!IsCurrentGeneration(enrollment, generation)) return true;

    EvidenceWrite write = new(
      enrollment,
      enrollmentVersion,
      model,
      firmware,
      capabilities,
      generation,
      observedAt);
    EvidenceKey key = new(enrollment.Id, generation);
    bool signal;
    lock (_evidenceSync)
    {
      // Device metadata is disposable churn: keep only the newest observation
      // for a connection generation, while never dropping the critical key.
      _pendingEvidenceWrites[key] = write;
      signal = _queuedEvidenceKeys.Add(key);
    }

    if (!signal) return true;
    if (_evidenceSignals.Writer.TryWrite(key)) return true;

    lock (_evidenceSync)
    {
      _queuedEvidenceKeys.Remove(key);
      _pendingEvidenceWrites.Remove(key);
    }
    return false;
  }

  private async Task RunEvidenceWriterAsync(CancellationToken cancellationToken)
  {
    await foreach (EvidenceKey key in _evidenceSignals.Reader.ReadAllAsync(cancellationToken))
    {
      EvidenceWrite write;
      lock (_evidenceSync)
      {
        if (!_pendingEvidenceWrites.TryGetValue(key, out EvidenceWrite? pending) || pending is null)
        {
          _queuedEvidenceKeys.Remove(key);
          continue;
        }
        _pendingEvidenceWrites.Remove(key);
        write = pending;
        _queuedEvidenceKeys.Remove(key);
      }

      if (!IsCurrentGeneration(write.Enrollment, write.ConnectionGeneration)) continue;
      var persisted = false;
      for (var attempt = 1; attempt <= 3; attempt++)
      {
        if (!IsCurrentGeneration(write.Enrollment, write.ConnectionGeneration)) break;
        try
        {
          await TryPersistEvidenceAsync(
            write.Enrollment,
            write.EnrollmentVersion,
            write.Model,
            write.Firmware,
            write.Capabilities,
            write.ObservedAt,
            cancellationToken);
          persisted = true;
          break;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
          return;
        }
        catch (Exception exception) when (attempt < 3)
        {
          logger.LogWarning(
            exception,
            "A BLE evidence write failed; retrying bounded persistence attempt {Attempt}.",
            attempt);
          await Task.Delay(TimeSpan.FromMilliseconds(250 * attempt), timeProvider, cancellationToken);
        }
        catch (Exception exception)
        {
          logger.LogWarning(
            exception,
            "A BLE evidence write failed after bounded retries without affecting telemetry.");
        }
      }

      if (!persisted && IsCurrentGeneration(write.Enrollment, write.ConnectionGeneration))
      {
        // Persistence failures must not turn into silent evidence loss. Keep
        // one latest write per generation and retry after a bounded pause;
        // disposable duplicate observations remain coalesced.
        await Task.Delay(TimeSpan.FromSeconds(1), timeProvider, cancellationToken);
        EvidenceKey retryKey = new(write.Enrollment.Id, write.ConnectionGeneration);
        lock (_evidenceSync)
        {
          // A newer observation may already be pending for this generation;
          // never overwrite it with the failed, older write.
          _pendingEvidenceWrites.TryAdd(retryKey, write);
          if (!_queuedEvidenceKeys.Add(retryKey)) continue;
        }
        if (!_evidenceSignals.Writer.TryWrite(retryKey))
        {
          lock (_evidenceSync)
          {
            _queuedEvidenceKeys.Remove(retryKey);
            _pendingEvidenceWrites.Remove(retryKey);
          }
        }
      }
    }
  }

  private bool IsCurrentGeneration(DeviceEnrollment enrollment, long generation)
  {
    lock (_sync)
    {
      return enrollment.Role == DeviceRole.Treadmill
        ? _snapshot.Treadmill.ConnectionGeneration == generation
        : _heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime) &&
          runtime.Connection.ConnectionGeneration == generation;
    }
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
    while (!maintenanceState.TryBeginMutation())
      await Task.Delay(TimeSpan.FromMilliseconds(250), timeProvider, cancellationToken);
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

  private TreadmillTelemetryUpdateResult UpdateTreadmillTelemetry(
    FtmsTreadmillData data,
    DateTimeOffset observedAt,
    long generation)
  {
    lock (_sync)
    {
      if (_snapshot.Treadmill.ConnectionGeneration != generation) return TreadmillTelemetryUpdateResult.Ignored;
      if (!IsPlausibleTelemetry(data, _snapshot.ReportedCapabilities))
      {
        _snapshot = _snapshot with
        {
          CapturedAt = timeProvider.GetUtcNow(),
          TreadmillTelemetry = null,
          Treadmill = _snapshot.Treadmill with
          {
            State = DeviceConnectionState.Faulted,
            Fault = "The treadmill sent implausible telemetry.",
          },
        };
        return TreadmillTelemetryUpdateResult.Ignored;
      }

      bool hasAnyField = data.InstantaneousSpeedKph is not null ||
        data.InclinationPercent is not null || data.RampAngleDegrees is not null ||
        data.AverageSpeedKph is not null || data.TotalDistanceMeters is not null ||
        data.PositiveElevationGainMeters is not null || data.NegativeElevationGainMeters is not null ||
        data.InstantaneousPaceSecondsPer500Meters is not null || data.AveragePaceSecondsPer500Meters is not null ||
        data.TotalEnergyKilocalories is not null || data.EnergyPerHourKilocalories is not null ||
        data.EnergyPerMinuteKilocalories is not null || data.HeartRateBpm is not null ||
        data.MetabolicEquivalent is not null || data.ElapsedTime is not null || data.RemainingTime is not null ||
        data.ForceNewtons is not null || data.PowerWatts is not null;
      if (!hasAnyField) return TreadmillTelemetryUpdateResult.Ignored;

      bool hasPrimaryField = data.InstantaneousSpeedKph is not null || data.InclinationPercent is not null;

      TreadmillTelemetry? previous = _snapshot.TreadmillTelemetry;
      double speed = data.InstantaneousSpeedKph ?? previous?.SpeedKph ?? 0;
      double incline = data.InclinationPercent ?? previous?.InclinePercent ?? 0;
      _snapshot = _snapshot with
      {
        CapturedAt = timeProvider.GetUtcNow(),
        TreadmillTelemetry = new TreadmillTelemetry(
          observedAt,
          speed,
          incline,
          data.InstantaneousSpeedKph is not null ? observedAt : previous?.SpeedObservedAt,
          data.InclinationPercent is not null ? observedAt : previous?.InclineObservedAt,
          data.AverageSpeedKph,
          data.TotalDistanceMeters,
          data.PositiveElevationGainMeters,
          data.NegativeElevationGainMeters,
          data.InstantaneousPaceSecondsPer500Meters,
          data.AveragePaceSecondsPer500Meters,
          data.TotalEnergyKilocalories,
          data.EnergyPerHourKilocalories,
          data.EnergyPerMinuteKilocalories,
          data.HeartRateBpm,
          data.MetabolicEquivalent,
          data.ElapsedTime,
          data.RemainingTime,
          data.ForceNewtons,
          data.PowerWatts),
        Treadmill = _snapshot.Treadmill with
        {
          State = DeviceConnectionState.Ready,
          LastObservedAt = observedAt,
          Fault = null,
        },
      };
      return hasPrimaryField ? TreadmillTelemetryUpdateResult.Primary : TreadmillTelemetryUpdateResult.Auxiliary;
    }
  }

  private enum TreadmillTelemetryUpdateResult
  {
    Ignored,
    Auxiliary,
    Primary,
  }

  private static bool IsPlausibleTelemetry(
    FtmsTreadmillData data,
    TreadmillCapabilities? capabilities)
  {
    if (data.InstantaneousSpeedKph is { } speed &&
        (!double.IsFinite(speed) || speed < 0 || speed > (double)(capabilities?.SpeedRange?.Maximum ?? 100m)))
      return false;
    if (data.InclinationPercent is { } incline)
    {
      if (!double.IsFinite(incline)) return false;
      if (capabilities?.InclineRange is { } range)
        return incline >= (double)range.Minimum && incline <= (double)range.Maximum;
      if (Math.Abs(incline) > 90) return false;
    }
    return true;
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

    var resolve = new ResolveReliabilityWrite(
      enrollment.Id,
      generation,
      Math.Max(0, incident.FailedAttemptCount - 1),
      incident.MaximumReconnectDelay,
      observedAt);
    if (!EnqueueReliabilityWrite(resolve))
    {
      // Keep the episode open so the next fresh telemetry sample can retry the
      // resolve after bounded queue pressure.
      lock (_sync)
      {
        if (!_reliabilityIncidents.ContainsKey(enrollment.Id))
          _reliabilityIncidents[enrollment.Id] = incident with { BeginQueued = false };
      }
    }
  }

  private void RecordReliabilityFailure(
    DeviceEnrollment enrollment,
    long generation,
    BleReliabilityFailureKind failureKind,
    string sanitizedFault,
    TimeSpan reconnectDelay,
    DateTimeOffset occurredAtUtc)
  {
    bool enqueueBegin;
    lock (_sync)
    {
      if (_reliabilityIncidents.TryGetValue(enrollment.Id, out ReliabilityIncidentRuntime? incident))
      {
        enqueueBegin = !incident.BeginQueued;
        _reliabilityIncidents[enrollment.Id] = incident with
        {
          FailedAttemptCount = incident.FailedAttemptCount + 1,
          MaximumReconnectDelay = reconnectDelay > incident.MaximumReconnectDelay
            ? reconnectDelay
            : incident.MaximumReconnectDelay,
          BeginQueued = true,
        };
      }
      else
      {
        enqueueBegin = true;
        _reliabilityIncidents[enrollment.Id] = new ReliabilityIncidentRuntime(1, reconnectDelay, true);
      }
    }

    if (enqueueBegin && !EnqueueReliabilityWrite(new BeginReliabilityWrite(
        enrollment.Id,
        enrollment.Role,
        enrollment.DisplayName,
        generation,
        failureKind,
        sanitizedFault,
        reconnectDelay,
        occurredAtUtc)))
    {
      // Permit the next failure to retry the first event after queue pressure.
      lock (_sync)
      {
        if (_reliabilityIncidents.TryGetValue(enrollment.Id, out ReliabilityIncidentRuntime? incident))
          _reliabilityIncidents[enrollment.Id] = incident with { BeginQueued = false };
      }
    }
  }

  private bool EnqueueReliabilityWrite(ReliabilityWrite write)
  {
    if (_reliabilityWrites.Writer.TryWrite(new ReliabilityWriteEnvelope(write, timeProvider.GetUtcNow())))
      return true;

    Interlocked.Increment(ref _reliabilityDroppedCount);
    DateTimeOffset now = timeProvider.GetUtcNow();
    bool logPressure;
    lock (_sync)
    {
      logPressure = _lastReliabilityPressureLogAtUtc is not { } last ||
        now - last >= ReliabilityLogThrottle;
      if (logPressure) _lastReliabilityPressureLogAtUtc = now;
    }
    if (logPressure)
      logger.LogWarning(
        "The BLE reliability recorder could not accept an event because shutdown had started. Dropped={DroppedCount}.",
        Interlocked.Read(ref _reliabilityDroppedCount));
    return false;
  }

  private async Task RunReliabilityWriterAsync(CancellationToken cancellationToken)
  {
    await foreach (ReliabilityWriteEnvelope envelope in _reliabilityWrites.Reader.ReadAllAsync(cancellationToken))
    {
      if (timeProvider.GetUtcNow() - envelope.EnqueuedAtUtc > ReliabilityLagThreshold)
      {
        Interlocked.Increment(ref _reliabilityLaggedCount);
        DateTimeOffset now = timeProvider.GetUtcNow();
        bool logLag;
        lock (_sync)
        {
          logLag = _lastReliabilityPressureLogAtUtc is not { } last ||
            now - last >= ReliabilityLogThrottle;
          if (logLag) _lastReliabilityPressureLogAtUtc = now;
        }
        if (logLag)
          logger.LogWarning(
            "The BLE reliability recorder is lagging while persistence is unavailable. LaggedCount={LaggedCount}.",
            Interlocked.Read(ref _reliabilityLaggedCount));
      }

      ReliabilityWrite write = envelope.Write;
      var persisted = false;
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
            }
            else continue;
            persisted = true;
          }
          finally
          {
            maintenanceState.EndMutation();
          }
          break;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
          return;
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

      if (!persisted)
      {
        lock (_sync)
        {
          if (write is BeginReliabilityWrite begin &&
              _reliabilityIncidents.TryGetValue(begin.EnrollmentId, out ReliabilityIncidentRuntime? incident))
          {
            _reliabilityIncidents[begin.EnrollmentId] = incident with { BeginQueued = false };
          }
          else if (write is ResolveReliabilityWrite resolve &&
                   !_reliabilityIncidents.ContainsKey(resolve.EnrollmentId))
          {
            _reliabilityIncidents[resolve.EnrollmentId] = new ReliabilityIncidentRuntime(
              Math.Max(1, resolve.AdditionalFailedAttempts + 1),
              resolve.MaximumReconnectDelay,
              false);
          }
        }

        // Reliability incidents are critical evidence. Keep the envelope in
        // the unbounded writer queue until persistence succeeds; later device
        // churn must not erase a failure episode.
        await Task.Delay(TimeSpan.FromSeconds(1), timeProvider, cancellationToken);
        _reliabilityWrites.Writer.TryWrite(envelope);
      }

      if (persisted)
        await TryPruneReliabilityAsync(cancellationToken);
    }
  }

  private async Task TryPruneReliabilityAsync(CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    lock (_sync)
    {
      if (_lastReliabilityPruneAtUtc is { } last && now - last < ReliabilityPruneInterval) return;
      _lastReliabilityPruneAtUtc = now;
    }

    try
    {
      while (!maintenanceState.TryBeginMutation())
        await Task.Delay(TimeSpan.FromMilliseconds(250), timeProvider, cancellationToken);
      try
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        await scope.ServiceProvider.GetRequiredService<IBleReliabilityStore>()
          .PruneRecoveredBeforeAsync(now - ReliabilityRetention, cancellationToken);
      }
      finally
      {
        maintenanceState.EndMutation();
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      return;
    }
    catch (Exception exception)
    {
      logger.LogWarning(exception, "A throttled BLE reliability retention prune failed.");
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
        if (_heartRateSources.TryGetValue(enrollment.Id, out HeartRateRuntime? runtime))
        {
          bool generationChanged = runtime.Connection.ConnectionGeneration != generation;
          _heartRateSources[enrollment.Id] = runtime with
          {
            Enrollment = enrollment,
            Connection = generationChanged ? connection with { LastObservedAt = null } : connection,
            BeatsPerMinute = generationChanged ? null : runtime.BeatsPerMinute,
            ObservedAt = generationChanged ? null : runtime.ObservedAt,
            BatteryPercent = generationChanged ? null : runtime.BatteryPercent,
            BatteryObservedAt = generationChanged ? null : runtime.BatteryObservedAt,
            Quality = generationChanged ? HeartRateSignalQuality.Unavailable : runtime.Quality,
          };
        }
        else
        {
          _heartRateSources[enrollment.Id] = new HeartRateRuntime(enrollment, connection, null, null);
        }
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
      if (role == DeviceRole.Treadmill)
      {
        DeviceConnectionSnapshot disconnected = _snapshot.Treadmill with
        {
          State = DeviceConnectionState.Disconnected,
          Fault = null,
        };
        _snapshot = _snapshot with { CapturedAt = timeProvider.GetUtcNow(), Treadmill = disconnected, TreadmillTelemetry = null, ReportedCapabilities = null };
      }
      else if (_heartRateSources.TryGetValue(enrollmentId, out HeartRateRuntime? runtime))
      {
        _heartRateSources[enrollmentId] = runtime with
        {
          Connection = runtime.Connection with
          {
            State = DeviceConnectionState.Disconnected,
            DisplayName = runtime.Enrollment.DisplayName,
            ProtocolId = runtime.Enrollment.ProtocolId,
            Fault = null,
          },
          BeatsPerMinute = null,
          ObservedAt = null,
          BatteryPercent = null,
          BatteryObservedAt = null,
          Quality = HeartRateSignalQuality.Unavailable,
          ContactState = HeartRateContactState.Unknown,
        };
      }
    }
  }

  private DeviceTelemetrySnapshot BuildSnapshotLocked(Guid? profileId)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    HeartRateSourceSnapshot[] sources = _heartRateSources.Values
      .Select(runtime =>
      {
        bool fresh = runtime.Connection.State == DeviceConnectionState.Ready &&
          runtime.Quality == HeartRateSignalQuality.Valid &&
          runtime.BeatsPerMinute is >= 30 and <= 250 &&
          runtime.ObservedAt is { } observedAt &&
          now - observedAt >= TimeSpan.Zero &&
          now - observedAt <= HeartRateFreshnessLimit;
        return new HeartRateSourceSnapshot(
          runtime.Enrollment.Id,
          runtime.Enrollment.DisplayName,
          runtime.Enrollment.HeartRateDeviceKind ?? HeartRateDeviceKind.Sensor,
          runtime.Enrollment.HeartRateDeviceFamily ?? HeartRateDeviceFamily.Other,
          runtime.Connection.State,
          runtime.Connection.ConnectionGeneration,
          fresh ? runtime.BeatsPerMinute : null,
          runtime.ObservedAt,
          runtime.Connection.Fault,
          runtime.BatteryPercent,
          runtime.BatteryObservedAt,
          runtime.Quality,
          runtime.ContactState);
      })
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
      HeartRateBpm = selected?.BeatsPerMinute,
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
      SelectedHeartRateQuality = displayed?.Quality ?? HeartRateSignalQuality.Unavailable,
      SelectedHeartRateContactState = displayed?.ContactState ?? HeartRateContactState.Unknown,
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
    DateTimeOffset? BatteryObservedAt = null,
    HeartRateSignalQuality Quality = HeartRateSignalQuality.Unavailable,
    HeartRateContactState ContactState = HeartRateContactState.Unknown);

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

  internal sealed record ReliabilityWriterMetrics(
    long DroppedCount,
    long LaggedCount,
    long EvidenceDroppedCount);

  private sealed record ReliabilityIncidentRuntime(
    int FailedAttemptCount,
    TimeSpan MaximumReconnectDelay,
    bool BeginQueued);

  private abstract record ReliabilityWrite;

  private sealed record ReliabilityWriteEnvelope(
    ReliabilityWrite Write,
    DateTimeOffset EnqueuedAtUtc);

  private sealed record EvidenceWrite(
    DeviceEnrollment Enrollment,
    int EnrollmentVersion,
    string? Model,
    string? Firmware,
    TreadmillCapabilities? Capabilities,
    long ConnectionGeneration,
    DateTimeOffset ObservedAt);

  private readonly record struct EvidenceKey(Guid EnrollmentId, long ConnectionGeneration);

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

  private sealed record RunConnectionDemand(
    Guid ProfileId,
    bool RequiresHeartRate,
    DateTimeOffset? ExpiresAtUtc);

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
