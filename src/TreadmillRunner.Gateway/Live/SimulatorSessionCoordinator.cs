using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.SignalR;
using Microsoft.Data.Sqlite;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Hubs;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Gateway.Polar;

namespace TreadmillRunner.Gateway.Live;

public interface ILiveSessionCoordinator : ITreadmillCommandContextValidator
{
  ActiveSessionSnapshot? CurrentSession { get; }
  bool IsHardwareSession { get; }

  Task<PreflightSnapshot> GetPreflightAsync(
    Guid profileId,
    Guid workoutRevisionId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> ArmAsync(
    Guid profileId,
    Guid workoutRevisionId,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default,
    WorkoutSessionSelection? selection = null);

  Task SetPhysicalMotionAsync(
    bool isMoving,
    double measuredSpeedKph,
    double measuredInclinePercent,
    CancellationToken cancellationToken = default);

  Task SetSimulatedHeartRateAsync(
    ushort? beatsPerMinute,
    CancellationToken cancellationToken = default);

  Task CompletePhysicalSessionAsync(CancellationToken cancellationToken = default);

  Task<TreadmillCommandResult> StopSimulatorAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> EndSessionAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> ResetWorkoutProgressAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> AdjustRequestedSpeedAsync(
    Guid operationId,
    double adjustmentKph,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> AdjustRequestedInclineAsync(
    Guid operationId,
    double targetPercent,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<TreadmillCommandIntent> PrepareCommandAsync(
    Guid operationId,
    TreadmillCommandKind kind,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    double? requestedValue = null,
    TreadmillCommandOrigin origin = TreadmillCommandOrigin.Manual,
    CancellationToken cancellationToken = default);

  Task RecordCommandResultAsync(
    TreadmillCommandIntent intent,
    TreadmillCommandResult result,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> SetHeartRateAutomationAsync(
    Guid operationId,
    HeartRateAutomationMode mode,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<ActiveSessionSnapshot> ResumePlannedControlsAsync(
    Guid operationId,
    long expectedSessionVersion,
    long connectionGeneration,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default);

  Task<bool> ResetAsync(
    CancellationToken cancellationToken = default,
    bool abandonStartupRecovery = false,
    bool reconcileRestoredDatabase = false);

  Task RestartStartupRecoveryAsync(CancellationToken cancellationToken = default);

  Task<bool> TryBeginMaintenanceAsync(CancellationToken cancellationToken = default);

  Task CancelMaintenanceAsync(CancellationToken cancellationToken = default);
}

public sealed class LiveSessionCoordinator(
    TimeProvider timeProvider,
    IServiceScopeFactory scopeFactory,
    IControlLeaseCoordinator leaseCoordinator,
    IReadOnlyDeviceCoordinator deviceCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    IHubContext<LiveHub> hubContext,
    IHostEnvironment hostEnvironment,
    IApplicationMaintenanceState applicationMaintenance,
    ILogger<LiveSessionCoordinator> logger,
    BleDiagnosticJournal? diagnosticJournal = null) :
  BackgroundService,
  ILiveSessionCoordinator,
  ILiveSnapshotSource
{
  private static readonly TimeSpan UpdateInterval = TimeSpan.FromMilliseconds(250);
  private static readonly TimeSpan PersistenceInterval = TimeSpan.FromSeconds(1);
  private static readonly TimeSpan TerminalPersistenceClientWaitTimeout = TimeSpan.FromSeconds(5);
  private static readonly TimeSpan ShutdownFinalPersistenceTimeout = TimeSpan.FromSeconds(5);
  private static readonly TimeSpan FreshTelemetryLimit = TimeSpan.FromSeconds(5);
  private readonly SemaphoreSlim _gate = new(1, 1);
  private readonly SemaphoreSlim _startupRecoveryGate = new(1, 1);
  private readonly SemaphoreSlim _armAdmission = new(1, 1);
  private readonly SemaphoreSlim _effectsGate = new(1, 1);
  private readonly CancellationTokenSource _effectsStop = new();
  private readonly CancellationTokenSource _shutdownStop = new();
  private readonly object _effectsQueueGate = new();
  private Task _effectsTail = Task.CompletedTask;
  private Task _terminalPersistenceTail = Task.CompletedTask;
  private Task _resetTail = Task.CompletedTask;
  private bool _resetPersistencePending;
  private bool _unfinishedSweepPending;
  private ActiveRun? _active;
  private LiveSnapshot _current = DisconnectedIdleSnapshot(timeProvider.GetUtcNow());
  private bool _startupRecoveryComplete;
  private bool _startupRecoveryWaitLogged;
  private int _startupRecoveryAttempt;
  private DateTimeOffset _nextStartupRecoveryAttemptUtc;
  private bool _maintenanceActive;
  private volatile bool _shutdownStarted;
  private readonly Guid _serviceInstanceId = Guid.NewGuid();
  private SessionTelemetryWriter? _telemetryWriter;
  private readonly LatestLiveSnapshotBroadcaster _snapshotBroadcaster = new(hubContext, logger, timeProvider);

  private SessionTelemetryWriter TelemetryWriter
  {
    get
    {
      SessionTelemetryWriter? existing = Volatile.Read(ref _telemetryWriter);
      if (existing is not null) return existing;
      var created = new SessionTelemetryWriter(
        scopeFactory,
        logger,
        timeProvider,
        IsCurrentTelemetryAsync,
        diagnosticJournal);
      return Interlocked.CompareExchange(ref _telemetryWriter, created, null) ?? created;
    }
  }

  public LiveSnapshot Current => Volatile.Read(ref _current);

  public ActiveSessionSnapshot? CurrentSession => _active?.Snapshot;
  public bool IsHardwareSession => _active?.HardwareMode == true;
  private bool SimulatorAvailable => hostEnvironment.IsDevelopment() && !deviceCoordinator.HasTreadmillEnrollment;

  public async Task<PreflightSnapshot> GetPreflightAsync(
    Guid profileId,
    Guid workoutRevisionId,
    CancellationToken cancellationToken = default)
  {
    (VersionedUserProfile profile, StoredWorkoutRevision revision, WorkoutDefinition workout) =
      await LoadPlanAsync(profileId, workoutRevisionId, cancellationToken);
    bool requiresHeartRate = ContainsHeartRateTarget(workout.Blocks);
    await deviceCoordinator.PrepareForRunAsync(profileId, requiresHeartRate, cancellationToken);
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(profileId);
    bool hardwareMode = !SimulatorAvailable;
    bool treadmillFresh = hardwareMode &&
      devices.Treadmill.State == DeviceConnectionState.Ready &&
      devices.TreadmillSpeedAge <= FreshTelemetryLimit;
    bool heartRateFresh = devices.SelectedHeartRateEnrollmentId is not null &&
      devices.HeartRate.State == DeviceConnectionState.Ready &&
      devices.HeartRateBpm is >= 30 and <= 250 &&
      devices.SelectedHeartRateQuality == HeartRateSignalQuality.Valid &&
      devices.SelectedHeartRateContactState != HeartRateContactState.NotDetected &&
      devices.HeartRateAge <= FreshTelemetryLimit;
    HeartRateSource heartRateSource = hardwareMode
      ? MapHeartRateSource(devices)
      : HeartRateSource.Simulated;
    TreadmillControlAvailability control = await LoadControlAvailabilityAsync(cancellationToken);
    if (!hardwareMode) control = TreadmillControlAvailability.Simulated;
    WorkoutCapabilityResult capabilityResult = WorkoutCapabilityPolicy.Evaluate(
      workout,
      control.SpeedRange,
      control.InclineRange,
      profile.Profile.MaximumSpeedKph);
    IReadOnlyList<PreflightCheck> profileDeviceChecks = await LoadProfileHeartRateDeviceChecksAsync(
      profileId,
      devices,
      cancellationToken);
    var checks = new List<PreflightCheck>
    {
      new("gateway", "Gateway", PreflightCheckStatus.Ready, "Gateway ready"),
      new("database", "Database", PreflightCheckStatus.Ready, "Database ready"),
      new(
        "workout-targets",
        "Workout targets",
        capabilityResult.IsValid ? PreflightCheckStatus.Ready : PreflightCheckStatus.Blocked,
        capabilityResult.IsValid
          ? capabilityResult.Targets.Any(static target => target.Disposition == WorkoutTargetDisposition.Normalized)
            ? "Targets are valid; safer treadmill-increment alignment will be applied."
            : "Targets fit the selected profile and verified treadmill limits."
          : string.Join(" ", capabilityResult.Rejected.Take(3).Select(static target => $"{target.Path}: {target.Reason}"))),
      new(
        "treadmill",
        devices.Treadmill.DisplayName ?? "Treadmill",
        hardwareMode
          ? treadmillFresh ? PreflightCheckStatus.Ready : PreflightCheckStatus.Waiting
          : PreflightCheckStatus.Ready,
        hardwareMode
          ? treadmillFresh
            ? $"Treadmill ready via {devices.Treadmill.TelemetryMode}"
            : devices.Treadmill.Fault ?? (devices.Treadmill.DisplayName is null
              ? "The enrolled treadmill is disconnected"
              : $"Treadmill is {devices.Treadmill.State}")
          : "Treadmill connected (simulator)"),
      new(
        "heart-rate",
        "Heart-rate signal",
        requiresHeartRate
          ? hardwareMode
            ? heartRateFresh ? PreflightCheckStatus.Ready : PreflightCheckStatus.Waiting
            : PreflightCheckStatus.Ready
          : PreflightCheckStatus.NotRequired,
        requiresHeartRate
          ? hardwareMode
            ? heartRateFresh
              ? $"{devices.HeartRate.DisplayName} is supplying fresh readings"
              : devices.HeartRate.Fault ?? "The preferred or active fallback sensor is not providing fresh telemetry"
            : "Heart rate connected (simulator)"
          : "Not required for this workout"),
    };
    checks.AddRange(profileDeviceChecks);
    return new PreflightSnapshot(
      timeProvider.GetUtcNow(),
      profile.Profile.Id,
      profile.Profile.DisplayName,
      revision.Id,
      workout.Title,
      workout.KnownDuration,
      requiresHeartRate ? "Heart-rate guided" : "Planned pace",
      requiresHeartRate,
      heartRateSource,
      checks,
      control.CanStart,
      control.CanStop,
      control.MinimumStartSpeedKph,
      control.CanSetSpeed,
      control.CanSetIncline,
      control.CanPause,
      control.SpeedRange,
      control.InclineRange,
      targetEvaluations: capabilityResult.Targets);
  }

  private async Task<IReadOnlyList<PreflightCheck>> LoadProfileHeartRateDeviceChecksAsync(
    Guid profileId,
    DeviceTelemetrySnapshot devices,
    CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    IDeviceEnrollmentStore store = scope.ServiceProvider.GetRequiredService<IDeviceEnrollmentStore>();
    IReadOnlyList<VersionedDeviceEnrollment> active = await store.ListActiveAsync(cancellationToken);
    IReadOnlyList<HeartRateDeviceAssignment> assignments = await store.ListHeartRateAssignmentsAsync(cancellationToken);
    Dictionary<Guid, DeviceEnrollment> enrollments = active
      .Select(static item => item.Enrollment)
      .Where(static enrollment => enrollment.Role == DeviceRole.HeartRate)
      .ToDictionary(static enrollment => enrollment.Id);
    Dictionary<Guid, HeartRateSourceSnapshot> sources = (devices.HeartRateSources ?? [])
      .ToDictionary(static source => source.EnrollmentId);
    HeartRateDeviceAssignment[] profileAssignments = assignments
      .Where(assignment => assignment.UserProfileId == profileId && enrollments.ContainsKey(assignment.DeviceEnrollmentId))
      .OrderByDescending(static assignment => assignment.IsPreferred)
      .ThenBy(static assignment => assignment.Priority)
      .ThenBy(static assignment => assignment.DeviceEnrollmentId)
      .ToArray();
    var checks = new List<PreflightCheck>(profileAssignments.Length);
    var fallbackIndex = 0;
    foreach (HeartRateDeviceAssignment assignment in profileAssignments)
    {
      DeviceEnrollment enrollment = enrollments[assignment.DeviceEnrollmentId];
      sources.TryGetValue(enrollment.Id, out HeartRateSourceSnapshot? source);
      bool selected = devices.SelectedHeartRateEnrollmentId == enrollment.Id;
      string role = assignment.IsPreferred ? "Preferred" : $"Fallback {++fallbackIndex}";
      string detail = selected
        ? $"{role} sensor selected for this run; {DescribeConnection(source)}."
        : source?.State == DeviceConnectionState.Ready
          ? $"{role} sensor connected and ready."
          : assignment.IsPreferred
            ? $"{role} sensor; {DescribeConnection(source)}."
            : $"{role} sensor on standby; it connects automatically if an earlier source fails.";
      checks.Add(new PreflightCheck(
        $"heart-rate-device-{enrollment.Id:N}",
        enrollment.DisplayName,
        source?.State == DeviceConnectionState.Ready ? PreflightCheckStatus.Ready : PreflightCheckStatus.NotRequired,
        detail));
    }

    return checks;
  }

  private static string DescribeConnection(HeartRateSourceSnapshot? source) => source?.State switch
  {
    DeviceConnectionState.Ready => "connected and ready",
    DeviceConnectionState.DiscoveringServices => "discovering services",
    DeviceConnectionState.Subscribing => "subscribing to heart-rate readings",
    DeviceConnectionState.Connecting => "connecting",
    DeviceConnectionState.Reconnecting => "reconnecting",
    _ when !string.IsNullOrWhiteSpace(source?.Fault) => source.Fault!,
    _ => "waiting to connect",
  };

  public async Task<ActiveSessionSnapshot> ArmAsync(
    Guid profileId,
    Guid workoutRevisionId,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default,
    WorkoutSessionSelection? selection = null)
  {
    using var armCancellation = CancellationTokenSource.CreateLinkedTokenSource(
      cancellationToken,
      _shutdownStop.Token);
    CancellationToken armToken = armCancellation.Token;
    await _armAdmission.WaitAsync(armToken);
    try
    {
      await WaitForTerminalPersistenceBarrierAsync(
        Volatile.Read(ref _resetTail),
        TerminalPersistenceClientWaitTimeout,
        armToken);
      var terminalSessionWasActive = false;
      LiveEffectMetadata? terminalRetryMetadata = null;
      Func<CancellationToken, Task>? terminalPersistenceRetry = null;
      ActiveSessionSnapshot? armedSnapshot = null;
      if (leaseCoordinator.Current is not ControlLease initialLease ||
          initialLease.Id != leaseId ||
          !string.Equals(initialLease.HolderId, holderId, StringComparison.Ordinal))
      {
        throw new InvalidOperationException("A current controller lease is required to arm a workout.");
      }

      await _gate.WaitAsync(armToken);
      try
      {
        ThrowIfShutdownStarted();
        if (!_startupRecoveryComplete)
          throw new InvalidOperationException("Gateway startup recovery is still in progress; try arming again shortly.");
        if (_resetPersistencePending)
          throw new InvalidOperationException("A previous reset did not complete; retry reset before arming another workout.");
        if (_unfinishedSweepPending)
          throw new InvalidOperationException("Restored session reconciliation is still incomplete; retry recovery before arming another workout.");
        if (_maintenanceActive)
          throw new InvalidOperationException("A software update is being activated; new sessions are temporarily unavailable.");
        if (_active is { Machine.State: not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted) })
          throw new InvalidOperationException("Another workout session is already active.");
        terminalSessionWasActive = _active is { Machine.State: SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted };
        if (terminalSessionWasActive && _active is { TerminalPersistenceEffect: not null } retainedTerminal)
        {
          terminalRetryMetadata = CaptureEffectMetadata(retainedTerminal);
          terminalPersistenceRetry = retainedTerminal.TerminalPersistenceEffect;
        }
      }
      finally
      {
        _gate.Release();
      }

      (VersionedUserProfile profile, StoredWorkoutRevision revision, WorkoutDefinition workout) =
        await LoadPlanAsync(profileId, workoutRevisionId, armToken);
      PreflightSnapshot preflight = await GetPreflightAsync(profileId, workoutRevisionId, armToken);
      if (!preflight.IsReady)
        throw new InvalidOperationException("Preflight is not ready; fresh required device telemetry is missing.");

      // Capability/enrollment reads are deliberately outside _gate. The arm
      // admission semaphore serializes competing arms while the authoritative
      // session lock remains reserved for fast state transitions.
      DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(profileId);
      bool hardwareMode = !SimulatorAvailable;
      bool requiresHeartRate = ContainsHeartRateTarget(workout.Blocks);
      TreadmillControlAvailability control = await LoadControlAvailabilityAsync(armToken);
      if (!hardwareMode) control = TreadmillControlAvailability.Simulated;
      WorkoutCapabilityResult capabilityResult = WorkoutCapabilityPolicy.Evaluate(
        workout,
        control.SpeedRange,
        control.InclineRange,
        profile.Profile.MaximumSpeedKph);
      if (!capabilityResult.IsValid)
        throw new InvalidOperationException("Workout targets exceed the selected profile or verified treadmill capabilities.");
      workout = capabilityResult.Definition;

      DateTimeOffset now = timeProvider.GetUtcNow();
      SessionTreadmillSnapshot? treadmillSnapshot = hardwareMode
        ? new SessionTreadmillSnapshot(
          devices.Treadmill.DisplayName ?? "Treadmill",
          devices.Treadmill.ProtocolId ?? "unknown",
          devices.Treadmill.TelemetryMode ?? "unknown",
          devices.Treadmill.ModelNumber,
          devices.Treadmill.FirmwareRevision,
          devices.Treadmill.Evidence,
          devices.Treadmill.Capabilities ?? control.ToCapabilities(),
          devices.Treadmill.ConnectionGeneration,
          control.EnrollmentId,
          control.IdentityFingerprint)
        : null;
      var definition = new NewWorkoutSession(
        Guid.NewGuid(),
        profile.Profile.Id,
        profile.Profile.DisplayName,
        revision.Id,
        workout.Title,
        now,
        JsonSerializer.Serialize(
          new SessionExecutionConfiguration(
            hardwareMode
              ? $"hardware:{devices.Treadmill.ProtocolId}:{devices.Treadmill.TelemetryMode}"
              : "simulator",
            requiresHeartRate ? "shadow" : "disabled",
            SessionProfileSnapshot.FromProfile(profile.Profile),
            devices.HeartRate.DisplayName,
            devices.SelectedHeartRateDeviceKind?.ToString(),
            devices.SelectedHeartRateDeviceFamily?.ToString(),
            treadmillSnapshot)),
        SessionMetricAlgorithms.EstimatedCaloriesV2,
        selection,
        hardwareMode ? SessionOrigin.Hardware : SessionOrigin.Simulator);
      var machine = new SessionStateMachine(timeProvider);
      machine.Arm();
      var active = new ActiveRun(
        definition,
        workout,
        machine,
        new WorkoutProgression(workout),
        profile.Profile.MaximumSpeedKph ?? 20,
        profile.Profile.WeightKilograms,
        now,
        hardwareMode,
        requiresHeartRate,
        hardwareMode ? MapHeartRateSource(devices) : HeartRateSource.Simulated,
        preflight.CanStartRemotely,
        preflight.CanStopRemotely,
        preflight.MinimumStartSpeedKph,
        control.CanSetSpeed,
        control.CanSetIncline,
        control.CanPause,
        control.SpeedRange,
        control.InclineRange,
        profile.Profile.HeartRateController,
        requiresHeartRate ? HeartRateAutomationMode.Shadow : HeartRateAutomationMode.Disabled,
        hardwareMode ? devices.Treadmill.ConnectionGeneration : 1,
        devices.SelectedHeartRateEnrollmentId,
        devices.HeartRateSelectionGeneration);

      using IServiceScope scope = scopeFactory.CreateScope();
      ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
      if (terminalSessionWasActive)
      {
        Task pendingTerminalEffects;
        await _gate.WaitAsync(armToken);
        try
        {
          ThrowIfShutdownStarted();
          lock (_effectsQueueGate) pendingTerminalEffects = _terminalPersistenceTail;
          if ((pendingTerminalEffects.IsFaulted || pendingTerminalEffects.IsCanceled) &&
              terminalRetryMetadata is { } retryMetadata &&
              terminalPersistenceRetry is not null)
          {
            var retryEffects = new LiveEffectBatch();
            retryEffects.Add(retryMetadata, terminalPersistenceRetry, terminal: true);
            pendingTerminalEffects = QueueEffects(
              retryEffects,
              _effectsStop.Token,
              deferExecution: true,
              propagateFailure: true);
          }
        }
        finally
        {
          _gate.Release();
        }
        await WaitForTerminalPersistenceBarrierAsync(
          pendingTerminalEffects,
          TerminalPersistenceClientWaitTimeout,
          armToken);
      }
      bool persisted = false;
      bool polarMemoryPrepared = false;
      try
      {
        await store.CreateAsync(definition, armToken);
        persisted = true;
        await deviceCoordinator.HoldRunConnectionsAsync(profileId, requiresHeartRate, armToken);
        if (hardwareMode && definition.Selection.RecordPolarH10Memory)
        {
          Guid h10Id = active.HeartRateEnrollmentId
            ?? throw new InvalidOperationException("The per-run memory option requires the exact selected Polar H10.");
          await scope.ServiceProvider.GetRequiredService<PolarH10AutomaticPreparationService>()
            .PrepareAsync(
              definition.SessionId,
              definition.UserProfileId,
              h10Id,
              armToken,
              definition.Selection.ReplaceExistingPolarH10Recording,
              definition.Selection.ReplacePolarH10ExerciseId);
          polarMemoryPrepared = true;
        }
      }
      catch (Exception exception)
      {
        if (persisted)
        {
          try
          {
            await store.InterruptUnfinishedAsync(
              timeProvider.GetUtcNow(),
              $"Session arm failed during device or H10 preparation: {exception.GetBaseException().Message}",
              CancellationToken.None);
          }
          catch (Exception cleanupException)
          {
            logger.LogWarning(cleanupException, "The newly created session could not be reconciled after arm setup failed.");
          }
        }
        try
        {
          await deviceCoordinator.ReleaseRunConnectionsAsync(CancellationToken.None);
        }
        catch (Exception cleanupException)
        {
          logger.LogWarning(cleanupException, "Run device connections could not be released after arm setup failed.");
        }
        throw;
      }

      string? admissionFailure = null;
      DateTimeOffset? leaseExpiry = null;
      await _gate.WaitAsync(polarMemoryPrepared ? CancellationToken.None : armToken);
      try
      {
        if (_shutdownStarted)
          admissionFailure = "The live-session coordinator is stopping.";
        else if (!_startupRecoveryComplete)
          admissionFailure = "Gateway startup recovery is still in progress; try arming again shortly.";
        else if (_maintenanceActive)
          admissionFailure = "A software update is being activated; new sessions are temporarily unavailable.";
        else if (_active is { Machine.State: not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted) })
          admissionFailure = "Another workout session is already active.";
        else if (leaseCoordinator.Current is not ControlLease finalLease ||
                 finalLease.Id != leaseId ||
                 !string.Equals(finalLease.HolderId, holderId, StringComparison.Ordinal))
          admissionFailure = "The controller lease changed while the workout was being prepared.";
        else
        {
          _active = active;
          leaseExpiry = finalLease.ExpiresAt;
          PublishSnapshot(active, now, SessionControlAccess.Controller, leaseExpiry);
          armedSnapshot = active.Snapshot;
        }
      }
      finally
      {
        _gate.Release();
      }

      if (armedSnapshot is not null)
      {
        _snapshotBroadcaster.Publish(null, armedSnapshot);
        return armedSnapshot;
      }

      if (polarMemoryPrepared)
      {
        try
        {
          IPolarH10RecordingStore memory = scope.ServiceProvider.GetRequiredService<IPolarH10RecordingStore>();
          if (await memory.QueueDiscardCleanupAsync(definition.SessionId, timeProvider.GetUtcNow(), CancellationToken.None))
            scope.ServiceProvider.GetRequiredService<PolarH10MemoryWorker>().Wake();
        }
        catch (Exception cleanupException)
        {
          logger.LogWarning(cleanupException, "The invalidated prepared H10 memory recording could not be queued for cleanup.");
        }
      }
      try
      {
        await store.InterruptUnfinishedAsync(
          timeProvider.GetUtcNow(),
          "Session arm was invalidated before it became the active in-memory session.",
          CancellationToken.None);
      }
      catch (Exception cleanupException)
      {
        logger.LogWarning(cleanupException, "The invalidated armed session could not be reconciled immediately.");
      }
      try
      {
        await deviceCoordinator.ReleaseRunConnectionsAsync(CancellationToken.None);
      }
      catch (Exception cleanupException)
      {
        logger.LogWarning(cleanupException, "Run device connections could not be released after arm admission was invalidated.");
      }
      throw new InvalidOperationException(admissionFailure ?? "The workout could not be armed.");
    }
    finally
    {
      _armAdmission.Release();
    }
  }

  public async Task SetPhysicalMotionAsync(
    bool isMoving,
    double measuredSpeedKph,
    double measuredInclinePercent,
    CancellationToken cancellationToken = default)
  {
    if (!SimulatorAvailable)
    {
      throw new InvalidOperationException("Simulator motion is disabled while a real treadmill is enrolled.");
    }

    if (!double.IsFinite(measuredSpeedKph) || measuredSpeedKph < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(measuredSpeedKph));
    }

    if (!double.IsFinite(measuredInclinePercent))
    {
      throw new ArgumentOutOfRangeException(nameof(measuredInclinePercent));
    }

    LiveEffectBatch effects = new();
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive)
        throw new InvalidOperationException("A software update is being activated; treadmill commands are unavailable.");
      ActiveRun active = RequireActive();
      SessionState previous = active.Machine.State;
      active.IsMoving = isMoving;
      active.MeasuredSpeedKph = isMoving ? measuredSpeedKph : 0;
      active.MeasuredInclinePercent = measuredInclinePercent;
      if (isMoving)
      {
        for (var index = 0; index < SessionStateMachine.RequiredPhysicalStartSamples; index++)
        {
          active.Machine.ObserveTelemetry(active.MeasuredSpeedKph);
        }
      }

      var now = timeProvider.GetUtcNow();
      MarkRunningIfTransitioned(active, previous, now, effects);

      PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }
    if (queuedEffects is not null)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
  }

  public async Task SetSimulatedHeartRateAsync(
    ushort? beatsPerMinute,
    CancellationToken cancellationToken = default)
  {
    if (beatsPerMinute is < 30 or > 250)
      throw new ArgumentOutOfRangeException(nameof(beatsPerMinute), "Heart rate must be between 30 and 250 bpm, or null to simulate stale telemetry.");
    ActiveSessionSnapshot? snapshotToPublish = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.HardwareMode)
        throw new InvalidOperationException("Simulated heart rate is unavailable during a hardware session.");
      active.HeartRateBpm = beatsPerMinute;
      DateTimeOffset now = timeProvider.GetUtcNow();
      active.HeartRateObservedAt = beatsPerMinute is null ? null : now;
      active.HeartRateAge = beatsPerMinute is null ? TimeSpan.FromSeconds(6) : TimeSpan.Zero;
      PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      snapshotToPublish = active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
  }

  public async Task CompletePhysicalSessionAsync(CancellationToken cancellationToken = default)
  {
    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    Task? terminalEffects = null;
    var releaseConnections = false;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.Machine.State is not (SessionState.Running or SessionState.PausedWaitingForPhysicalResume))
      {
        throw new InvalidOperationException("Physical completion requires a running or paused session.");
      }

      var now = timeProvider.GetUtcNow();
      UpdateMotion(active, now);
      DateTimeOffset completedAt = GetTerminalTimestamp(active, now);
      SessionSummary summary = CreateSummary(active, SessionState.Completed, completedAt);
      active.Machine.Complete();
      active.IsMoving = false;
      active.MeasuredSpeedKph = 0;
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      SessionCompletedEvent completed = new(completedAt);
      active.TerminalPersistenceEffect = CreateTerminalPersistenceEffect(metadata.SessionId, completed, summary);
      effects.Add(metadata, active.TerminalPersistenceEffect, terminal: true);
      active.DeviceConnectionsReleased = true;
      PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      releaseConnections = true;
      if (!effects.IsEmpty)
        terminalEffects = QueueEffects(
          effects,
          _effectsStop.Token,
          deferExecution: true,
          propagateFailure: true);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (releaseConnections)
      await ReleaseDeviceConnectionsAsync(cancellationToken);
    if (terminalEffects is not null)
      _ = await AwaitTerminalPersistenceForClientAsync(terminalEffects, cancellationToken);
  }

  public async Task<TreadmillCommandResult> StopSimulatorAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("An operation ID is required.", nameof(operationId));
    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("A current controller lease is required to stop the simulator session.");
    }

    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    TreadmillCommandResult? resultToReturn = null;
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.HardwareMode)
      {
        throw new InvalidOperationException("The simulator stop path cannot control real treadmill hardware.");
      }
      if (active.Machine.Version != expectedSessionVersion)
      {
        throw new InvalidOperationException(
          $"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      }
      if (active.Machine.State is not (
        SessionState.ArmedWaitingForPhysicalStart or
        SessionState.Running or
        SessionState.PausedWaitingForPhysicalResume))
      {
        throw new InvalidOperationException("Simulator Stop requires an active session.");
      }

      DateTimeOffset issuedAt = timeProvider.GetUtcNow();
      UpdateMotion(active, issuedAt);
      active.Machine.StopWaitingForPhysicalResume();
      active.IsMoving = false;
      active.MeasuredSpeedKph = 0;
      SuspendAutomation(active, "The treadmill is stopped; press Start when you are ready to resume.");
      active.ProcessedOperationIds.Add(operationId);
      var result = new TreadmillCommandResult(
        operationId,
        TreadmillCommandKind.Stop,
        TreadmillCommandDisposition.Confirmed,
        null,
        null,
        0,
        "Simulator Stop confirmed; the session is paused and can be resumed.",
        1,
        issuedAt,
        timeProvider.GetUtcNow());
      active.LastCommandResult = result;

      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      SessionPausedEvent stopped = new(SessionPauseReason.TreadmillStopped, result.CompletedAt);
      SessionRecoveryCheckpoint checkpoint = CreateRecoveryCheckpoint(active, result.CompletedAt);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        await store.AppendEventAsync(metadata.SessionId, stopped, token);
        await store.SaveRecoveryCheckpointAsync(checkpoint, token);
      });
      PublishSnapshot(active, result.CompletedAt, SessionControlAccess.Controller, lease.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      resultToReturn = result;
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (queuedEffects is not null)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
    return resultToReturn!;
  }

  public async Task<ActiveSessionSnapshot> EndSessionAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    ValidateSessionAction(operationId, leaseId, holderId);
    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    Task? terminalEffects = null;
    var releaseConnections = false;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.ProcessedOperationIds.Contains(operationId)) return active.Snapshot;
      if (active.Machine.Version != expectedSessionVersion)
        throw new InvalidOperationException($"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      if (active.Machine.State != SessionState.PausedWaitingForPhysicalResume ||
          active.IsMoving || active.MeasuredSpeedKph > 0.05)
        throw new InvalidOperationException("End session requires a confirmed stopped treadmill and a paused session.");

      DateTimeOffset now = timeProvider.GetUtcNow();
      DateTimeOffset stoppedAt = GetTerminalTimestamp(active, now);
      SessionSummary summary = CreateSummary(active, SessionState.Stopped, stoppedAt);
      active.Machine.Stop();
      active.ProcessedOperationIds.Add(operationId);
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      SessionStoppedEvent stopped = new(stoppedAt);
      active.TerminalPersistenceEffect = CreateTerminalPersistenceEffect(metadata.SessionId, stopped, summary);
      effects.Add(metadata, active.TerminalPersistenceEffect, terminal: true);
      active.DeviceConnectionsReleased = true;
      PublishSnapshot(active, now, SessionControlAccess.Controller, leaseCoordinator.Current?.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      releaseConnections = true;
      if (!effects.IsEmpty)
        terminalEffects = QueueEffects(
          effects,
          _effectsStop.Token,
          deferExecution: true,
          propagateFailure: true);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (releaseConnections)
      await ReleaseDeviceConnectionsAsync(cancellationToken);
    if (terminalEffects is not null)
      _ = await AwaitTerminalPersistenceForClientAsync(terminalEffects, cancellationToken);
    return snapshotToPublish!;
  }

  public async Task<ActiveSessionSnapshot> ResetWorkoutProgressAsync(
    Guid operationId,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    ValidateSessionAction(operationId, leaseId, holderId);
    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.ProcessedOperationIds.Contains(operationId)) return active.Snapshot;
      if (active.Machine.Version != expectedSessionVersion)
        throw new InvalidOperationException($"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      if (active.Machine.State != SessionState.PausedWaitingForPhysicalResume ||
          active.IsMoving || active.MeasuredSpeedKph > 0.05)
        throw new InvalidOperationException("Reset progress requires a confirmed stopped treadmill and a paused session.");

      DateTimeOffset now = timeProvider.GetUtcNow();
      int previousStepIndex = active.Progression.CurrentStepIndex;
      TimeSpan previousWorkoutElapsed = active.Progression.ElapsedSinceRestart;
      active.Progression.Restart(active.Elapsed, active.DistanceKilometers);
      active.SpeedOverrideKph = null;
      active.InclineOverridePercent = null;
      active.AppliedSpeedPlanStepIndex = null;
      active.AppliedInclinePlanStepIndex = null;
      active.HeartRateController.ResetDwell();
      active.DesiredHeartRateAutomationMode = HeartRateAutomationMode.Disabled;
      active.HeartRateAutomationMode = HeartRateAutomationMode.Disabled;
      active.HeartRateAutomationReason = "Workout progress was reset. Press Start when you are ready; step-zero speed and incline will be reconciled after fresh motion.";
      // While stopped, no planned command can run. Re-arm reconciliation now
      // so the explicit next Start plus fresh motion applies step-zero targets.
      active.CommandsSuspended = false;
      active.CommandsSuspendedReason = null;
      active.CanResumePlannedControls = false;
      active.Machine.MarkConfigurationChanged();
      active.ProcessedOperationIds.Add(operationId);

      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      WorkoutProgressResetEvent resetEvent = new(previousStepIndex, previousWorkoutElapsed, now);
      SessionRecoveryCheckpoint checkpoint = CreateRecoveryCheckpoint(active, now);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        await store.AppendEventAsync(metadata.SessionId, resetEvent, token);
        await store.SaveRecoveryCheckpointAsync(checkpoint, token);
      });
      PublishSnapshot(active, now, SessionControlAccess.Controller, leaseCoordinator.Current?.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (queuedEffects is not null)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
    return snapshotToPublish!;
  }

  private void ValidateSessionAction(Guid operationId, Guid leaseId, string holderId)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("An operation ID is required.", nameof(operationId));
    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
      throw new InvalidOperationException("A current controller lease is required for this session action.");
  }

  public async Task<ActiveSessionSnapshot> AdjustRequestedSpeedAsync(
    Guid operationId,
    double adjustmentKph,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("An operation ID is required.", nameof(operationId));
    }

    if (!double.IsFinite(adjustmentKph) || Math.Abs(adjustmentKph) > 30 || adjustmentKph == 0)
    {
      throw new ArgumentOutOfRangeException(nameof(adjustmentKph), "A finite adjustment between -30 and 30 km/h is required.");
    }

    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("A current controller lease is required to override speed.");
    }

    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.HardwareMode)
      {
        throw new InvalidOperationException(
          "Remote speed control remains disabled until its TR-006 hardware gate is approved and verified.");
      }

      if (active.ProcessedOperationIds.Contains(operationId))
      {
        return active.Snapshot;
      }

      if (active.Machine.Version != expectedSessionVersion)
      {
        throw new InvalidOperationException(
          $"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      }

      double previous = active.RequestedSpeedKph;
      double requested = Math.Round(
        Math.Clamp(previous + adjustmentKph, 0, active.MaximumSpeedKph),
        1,
        MidpointRounding.AwayFromZero);
      active.Machine.RecordManualSpeedOverride(previous, requested);
      var speedOverride = (ManualSpeedOverrideEvent)active.Machine.Events[^1];
      active.SpeedOverrideKph = requested;
      active.MeasuredSpeedKph = requested;
      active.IsMoving = requested > SessionStateMachine.PhysicalStartThresholdKph;
      active.TelemetryAge = TimeSpan.Zero;
      active.ProcessedOperationIds.Add(operationId);
      if (active.RequiresHeartRate)
      {
        active.HeartRateController.ResetDwell();
        active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedManualOverride;
        active.HeartRateAutomationReason =
          "A manual speed override suspends heart-rate automation until explicitly re-enabled.";
      }
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
          metadata.SessionId,
          speedOverride,
          token);
      });
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (queuedEffects is not null)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
    return snapshotToPublish!;
  }

  public async Task<ActiveSessionSnapshot> AdjustRequestedInclineAsync(
    Guid operationId,
    double targetPercent,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("An operation ID is required.", nameof(operationId));
    }

    if (!double.IsFinite(targetPercent) || targetPercent is < 0 or > 15)
    {
      throw new ArgumentOutOfRangeException(
        nameof(targetPercent),
        "A finite simulator incline target between 0 and 15 percent is required.");
    }

    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("A current controller lease is required to override incline.");
    }

    LiveEffectBatch effects = new();
    ActiveSessionSnapshot? snapshotToPublish = null;
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.HardwareMode)
      {
        throw new InvalidOperationException(
          "Simulator incline control is unavailable while a hardware session is active.");
      }

      if (active.ProcessedOperationIds.Contains(operationId))
      {
        return active.Snapshot;
      }

      if (active.Machine.State is not (SessionState.Running or SessionState.PausedWaitingForPhysicalResume))
      {
        throw new InvalidOperationException("Incline overrides require a running or paused session.");
      }

      if (active.Machine.Version != expectedSessionVersion)
      {
        throw new InvalidOperationException(
          $"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      }

      double previous = active.RequestedInclinePercent;
      double requested = Math.Round(targetPercent, 1, MidpointRounding.AwayFromZero);
      var inclineOverride = new ManualInclineOverrideEvent(
        previous,
        requested,
        timeProvider.GetUtcNow());
      active.InclineOverridePercent = requested;
      active.MeasuredInclinePercent = requested;
      active.TelemetryAge = TimeSpan.Zero;
      active.Machine.MarkConfigurationChanged();
      active.ProcessedOperationIds.Add(operationId);
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
          metadata.SessionId,
          inclineOverride,
          token);
      });
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (queuedEffects is not null)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
    return snapshotToPublish!;
  }

  public async Task<TreadmillCommandIntent> PrepareCommandAsync(
    Guid operationId,
    TreadmillCommandKind kind,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    double? requestedValue = null,
    TreadmillCommandOrigin origin = TreadmillCommandOrigin.Manual,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("An operation ID is required.", nameof(operationId));
    }

    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive)
        throw new InvalidOperationException("A software update is being activated; treadmill commands are unavailable.");
      ActiveRun active = RequireActive();

      bool gatewayOwned = origin is TreadmillCommandOrigin.PlannedTransition or
        TreadmillCommandOrigin.HeartRateAutomation or
        TreadmillCommandOrigin.WorkoutCompletion;
      if (gatewayOwned)
      {
        if (leaseId != active.AutomationAuthorityId ||
            !string.Equals(holderId, active.AutomationAuthorityHolder, StringComparison.Ordinal))
          throw new InvalidOperationException("The automatic command authority is not current for this session.");
      }
      else if (leaseCoordinator.Current is not ControlLease lease ||
               lease.Id != leaseId ||
               !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
      {
        throw new InvalidOperationException("A current controller lease is required for treadmill commands.");
      }

      if (active.Machine.Version != expectedSessionVersion)
      {
        throw new InvalidOperationException(
          $"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      }

      if (kind == TreadmillCommandKind.Start)
      {
        if (!active.CanStartRemotely || active.MinimumStartSpeedKph is null)
        {
          throw new InvalidOperationException("Remote Start is not hardware verified for this treadmill model and firmware.");
        }

        if (active.Machine.State is not (SessionState.ArmedWaitingForPhysicalStart or SessionState.PausedWaitingForPhysicalResume))
        {
          throw new InvalidOperationException("Remote Start/Resume requires an armed or physically paused session.");
        }

        requestedValue = active.MinimumStartSpeedKph;
      }
      else if (kind == TreadmillCommandKind.SetSpeed)
      {
        if (!active.CanSetSpeedRemotely || active.SpeedRange is null)
        {
          throw new InvalidOperationException("Remote speed control is not hardware verified for this treadmill model and firmware.");
        }

        if (active.Machine.State != SessionState.Running || active.MeasuredSpeedKph <= 0.05)
        {
          throw new InvalidOperationException("Remote speed changes require a running session and a moving belt.");
        }

        if (requestedValue is null || !double.IsFinite(requestedValue.Value))
        {
          throw new ArgumentOutOfRangeException(nameof(requestedValue));
        }

        requestedValue = Math.Clamp(requestedValue.Value, (double)active.SpeedRange.Minimum,
          Math.Min(active.MaximumSpeedKph, (double)active.SpeedRange.Maximum));
      }
      else if (kind == TreadmillCommandKind.SetIncline)
      {
        if (!active.CanSetInclineRemotely || active.InclineRange is null)
        {
          throw new InvalidOperationException("Remote incline control is not hardware verified for this treadmill model and firmware.");
        }

        if (active.Machine.State != SessionState.Running || requestedValue is null || !double.IsFinite(requestedValue.Value))
        {
          throw new InvalidOperationException("Remote incline changes require a running session and a finite target.");
        }

        requestedValue = Math.Clamp(requestedValue.Value, (double)active.InclineRange.Minimum,
          (double)active.InclineRange.Maximum);
      }
      else if (kind == TreadmillCommandKind.Pause)
      {
        if (!active.CanPauseRemotely)
        {
          throw new InvalidOperationException("Remote Pause is not hardware verified for this treadmill model and firmware.");
        }

        if (active.Machine.State != SessionState.Running)
        {
          throw new InvalidOperationException("Remote Pause requires a running session.");
        }

        requestedValue = null;
      }
      else if (kind == TreadmillCommandKind.Stop)
      {
        if (!active.CanStopRemotely)
        {
          throw new InvalidOperationException("Remote Stop is not hardware verified for this treadmill model and firmware.");
        }

        if (active.Machine.State is not (
          SessionState.ArmedWaitingForPhysicalStart or
          SessionState.Running or
          SessionState.PausedWaitingForPhysicalResume))
        {
          throw new InvalidOperationException("Remote Stop requires an active session.");
        }

        requestedValue = null;
      }
      else
      {
        throw new ArgumentOutOfRangeException(nameof(kind));
      }

      DeviceConnectionSnapshot treadmill = deviceCoordinator.Current.Treadmill;
      var issuedAt = timeProvider.GetUtcNow();
      return new TreadmillCommandIntent(
        operationId,
        active.Definition.SessionId,
        kind,
        issuedAt,
        issuedAt.AddSeconds(4),
        active.Machine.Version,
        active.Machine.State,
        leaseId,
        holderId,
        active.HardwareMode ? treadmill.ConnectionGeneration : active.ConnectionGeneration,
        requestedValue,
        origin);
    }
    finally
    {
      _gate.Release();
    }
  }

  public bool IsCurrent(TreadmillCommandIntent intent)
  {
    ArgumentNullException.ThrowIfNull(intent);
    ActiveSessionSnapshot? snapshot = CurrentSession;
    if (snapshot is null ||
        snapshot.SessionId != intent.SessionId ||
        snapshot.Version != intent.ExpectedSessionVersion ||
        snapshot.Live.SessionState != intent.ExpectedSessionState)
      return false;

    if (intent.Origin is TreadmillCommandOrigin.PlannedTransition or
        TreadmillCommandOrigin.HeartRateAutomation or
        TreadmillCommandOrigin.WorkoutCompletion)
      return _active is { } active &&
        intent.LeaseId == active.AutomationAuthorityId &&
        string.Equals(intent.HolderId, active.AutomationAuthorityHolder, StringComparison.Ordinal);

    ControlLease? lease = leaseCoordinator.Current;
    return
      lease is not null &&
      lease.Id == intent.LeaseId &&
      string.Equals(lease.HolderId, intent.HolderId, StringComparison.Ordinal);
  }

  public async Task RecordCommandResultAsync(
    TreadmillCommandIntent intent,
    TreadmillCommandResult result,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(intent);
    ArgumentNullException.ThrowIfNull(result);
    if (intent.OperationId != result.OperationId || intent.Kind != result.Kind)
    {
      throw new ArgumentException("The command result does not match its intent.", nameof(result));
    }

    ActiveSessionSnapshot? snapshotToPublish = null;
    var effects = new LiveEffectBatch();
    Task? queuedEffects = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ThrowIfShutdownStarted();
      if (_active is not { } active || active.Definition.SessionId != intent.SessionId)
      {
        return;
      }

      active.LastCommandResult = result;
      if (result.Disposition == TreadmillCommandDisposition.Unknown)
      {
        active.HeartRateController.ResetDwell();
        AddWarningOnce(active, "A treadmill command has an unknown physical outcome; automation is suspended.");
        active.CommandsSuspended = true;
        active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
        active.HeartRateAutomationReason = result.Reason;
        if (intent.Origin == TreadmillCommandOrigin.WorkoutCompletion)
        {
          AddWarningOnce(active,
            "Workout completion could not confirm that the treadmill stopped. Use the physical Stop control; this workout will remain open until stopped telemetry is confirmed.");
        }
      }
      else if (result.Disposition == TreadmillCommandDisposition.Rejected &&
               intent.Origin is TreadmillCommandOrigin.PlannedTransition or
                 TreadmillCommandOrigin.HeartRateAutomation or
                 TreadmillCommandOrigin.WorkoutCompletion)
      {
        active.HeartRateController.ResetDwell();
        active.CommandsSuspended = true;
        active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
        active.HeartRateAutomationReason = result.Reason;
        AddWarningOnce(active, "Automatic treadmill commands are suspended after a rejected command.");
        if (intent.Origin == TreadmillCommandOrigin.WorkoutCompletion)
        {
          AddWarningOnce(active,
            "The treadmill rejected the workout-completion Stop. Use the physical Stop control; this workout will remain open until stopped telemetry is confirmed.");
        }
      }
      else if (result.Disposition == TreadmillCommandDisposition.Confirmed)
      {
        active.Warnings.Remove("A treadmill command has an unknown physical outcome; automation is suspended.");
        if (result.Kind == TreadmillCommandKind.SetSpeed &&
            intent.Origin == TreadmillCommandOrigin.HeartRateAutomation &&
            result.AcceptedValue is { } automaticSpeed)
        {
          // The automation target becomes the requested value shown to the runner.
          // Keeping the prior manual override here would make the UI claim that an
          // obsolete target is still active after telemetry confirms a new speed.
          active.SpeedOverrideKph = automaticSpeed;
          active.ProcessedOperationIds.Add(intent.OperationId);
        }
        else if (result.Kind == TreadmillCommandKind.SetSpeed &&
            intent.Origin == TreadmillCommandOrigin.Manual &&
            result.AcceptedValue is { } acceptedSpeed)
        {
          double previous = active.RequestedSpeedKph;
          active.Machine.RecordManualSpeedOverride(previous, acceptedSpeed);
          var speedOverride = (ManualSpeedOverrideEvent)active.Machine.Events[^1];
          active.SpeedOverrideKph = acceptedSpeed;
          active.ProcessedOperationIds.Add(intent.OperationId);
          active.HeartRateController.ResetDwell();
          active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedManualOverride;
          active.HeartRateAutomationReason = "A manual speed override suspends heart-rate automation until explicitly re-enabled.";
          LiveEffectMetadata metadata = CaptureEffectMetadata(active);
          effects.Add(metadata, async token =>
          {
            using IServiceScope speedScope = scopeFactory.CreateScope();
            await speedScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
              metadata.SessionId,
              speedOverride,
              token);
          });
        }
        else if (result.Kind == TreadmillCommandKind.SetIncline &&
                 intent.Origin == TreadmillCommandOrigin.Manual &&
                 result.AcceptedValue is { } acceptedIncline)
        {
          double previous = active.RequestedInclinePercent;
          active.InclineOverridePercent = acceptedIncline;
          active.ProcessedOperationIds.Add(intent.OperationId);
          ManualInclineOverrideEvent inclineOverride = new(previous, acceptedIncline, timeProvider.GetUtcNow());
          LiveEffectMetadata metadata = CaptureEffectMetadata(active);
          effects.Add(metadata, async token =>
          {
            using IServiceScope inclineScope = scopeFactory.CreateScope();
            await inclineScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
              metadata.SessionId,
              inclineOverride,
              token);
          });
        }
        else if (intent.Origin == TreadmillCommandOrigin.PlannedTransition &&
                 result.Kind == TreadmillCommandKind.SetSpeed)
        {
          active.AppliedSpeedPlanStepIndex = active.Progression.CurrentStepIndex;
        }
        else if (intent.Origin == TreadmillCommandOrigin.PlannedTransition &&
                 result.Kind == TreadmillCommandKind.SetIncline)
        {
          active.AppliedInclinePlanStepIndex = active.Progression.CurrentStepIndex;
        }
        else if (result.Kind == TreadmillCommandKind.Pause && active.Machine.State == SessionState.Running)
        {
          active.HeartRateController.ResetDwell();
          active.Machine.PauseWaitingForPhysicalResume();
          active.CommandsSuspended = true;
          active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
          active.HeartRateAutomationReason = "Pause suspends automation until explicitly re-enabled after physical resume.";
          SessionPausedEvent paused = new(SessionPauseReason.WebControl, timeProvider.GetUtcNow());
          LiveEffectMetadata metadata = CaptureEffectMetadata(active);
          effects.Add(metadata, async token =>
          {
            using IServiceScope pauseScope = scopeFactory.CreateScope();
            await pauseScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
              metadata.SessionId,
              paused,
              token);
          });
        }
        else if (result.Kind == TreadmillCommandKind.Stop &&
            intent.Origin == TreadmillCommandOrigin.WorkoutCompletion &&
            active.Machine.State == SessionState.Running &&
            active.Progression.IsComplete &&
            result.MeasuredValue is <= 0.05)
        {
          var now = timeProvider.GetUtcNow();
          UpdateMotion(active, now);
          active.IsMoving = false;
          active.MeasuredSpeedKph = result.MeasuredValue ?? 0;
          active.ProcessedOperationIds.Add(intent.OperationId);
          FinalizeCompletedSession(active, now, effects);
        }
        else if (result.Kind == TreadmillCommandKind.Stop &&
            intent.Origin == TreadmillCommandOrigin.WorkoutCompletion)
        {
          SuspendAutomation(active,
            "Workout completion did not receive stopped telemetry. Use the physical Stop control; the workout remains open.");
          AddWarningOnce(active,
            "Workout completion did not receive stopped telemetry. Use the physical Stop control; the workout remains open.");
        }
        else if (result.Kind == TreadmillCommandKind.Stop &&
            active.Machine.State is SessionState.ArmedWaitingForPhysicalStart or SessionState.Running or SessionState.PausedWaitingForPhysicalResume)
        {
          var now = timeProvider.GetUtcNow();
          UpdateMotion(active, now);
          active.Machine.StopWaitingForPhysicalResume();
          active.IsMoving = false;
          active.MeasuredSpeedKph = result.MeasuredValue ?? 0;
          SuspendAutomation(active, "The treadmill is stopped; press Start when you are ready to resume.");
          active.ProcessedOperationIds.Add(intent.OperationId);
          SessionPausedEvent stopped = new(SessionPauseReason.TreadmillStopped, now);
          SessionRecoveryCheckpoint checkpoint = CreateRecoveryCheckpoint(active, now);
          LiveEffectMetadata metadata = CaptureEffectMetadata(active);
          effects.Add(metadata, async token =>
          {
            using IServiceScope scope = scopeFactory.CreateScope();
            ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
            await store.AppendEventAsync(metadata.SessionId, stopped, token);
            await store.SaveRecoveryCheckpointAsync(checkpoint, token);
          });
        }
      }

      var capturedAt = timeProvider.GetUtcNow();
      PublishSnapshot(active, capturedAt, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      snapshotToPublish = active.Snapshot;
      if (!effects.IsEmpty)
        queuedEffects = QueueEffects(effects, _effectsStop.Token);
    }
    finally
    {
      _gate.Release();
    }

    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    if (queuedEffects is not null && intent.Origin == TreadmillCommandOrigin.Manual)
      await AwaitQueuedEffectsForClientAsync(queuedEffects, cancellationToken);
  }

  public async Task<ActiveSessionSnapshot> SetHeartRateAutomationAsync(
    Guid operationId,
    HeartRateAutomationMode mode,
    long expectedSessionVersion,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("An operation ID is required.", nameof(operationId));
    if (mode is HeartRateAutomationMode.SuspendedManualOverride or HeartRateAutomationMode.SuspendedSafety)
      throw new ArgumentException("Suspended modes are system states, not selectable modes.", nameof(mode));
    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
      throw new InvalidOperationException("A current controller lease is required to change heart-rate automation.");

    ActiveSessionSnapshot? snapshotToPublish = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.ProcessedOperationIds.Contains(operationId)) return active.Snapshot;
      if (active.Machine.Version != expectedSessionVersion)
        throw new InvalidOperationException(
          $"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      if (!active.RequiresHeartRate && mode != HeartRateAutomationMode.Disabled)
        throw new InvalidOperationException("This workout has no heart-rate speed target.");
      if (active.HardwareMode &&
          mode is (HeartRateAutomationMode.DecreaseOnly or HeartRateAutomationMode.Full) &&
          !active.CanSetSpeedRemotely)
        throw new InvalidOperationException("Remote speed control is not hardware verified.");

      active.HeartRateAutomationMode = mode;
      active.DesiredHeartRateAutomationMode = mode;
      active.HeartRateController.ResetDwell();
      active.Machine.MarkConfigurationChanged();
      active.HeartRateAutomationReason = mode switch
      {
        HeartRateAutomationMode.Disabled => "Heart-rate automation is disabled.",
        HeartRateAutomationMode.Shadow => "Shadow mode records decisions without sending speed commands.",
        HeartRateAutomationMode.DecreaseOnly => "Only above-target speed decreases are enabled.",
        HeartRateAutomationMode.Full => "Below-target increases and above-target decreases are enabled.",
        _ => null,
      };
      active.CommandsSuspended = false;
      active.CommandsSuspendedReason = null;
      active.ProcessedOperationIds.Add(operationId);
      active.Warnings.Remove("Automatic treadmill commands are suspended after a rejected command.");
      active.Warnings.Remove("A treadmill command has an unknown physical outcome; automation is suspended.");
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      snapshotToPublish = active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    return snapshotToPublish!;
  }

  public async Task<ActiveSessionSnapshot> ResumePlannedControlsAsync(
    Guid operationId,
    long expectedSessionVersion,
    long connectionGeneration,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("An operation ID is required.", nameof(operationId));
    if (leaseCoordinator.Current is not ControlLease lease || lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
      throw new InvalidOperationException("A current controller lease is required to resume planned controls.");

    ActiveSessionSnapshot? snapshotToPublish = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun active = RequireActive();
      if (active.ProcessedOperationIds.Contains(operationId)) return active.Snapshot;
      if (active.Machine.Version != expectedSessionVersion)
        throw new InvalidOperationException($"Expected session version {expectedSessionVersion}, but current version is {active.Machine.Version}.");
      if (active.Machine.State != SessionState.Running || !active.IsMoving || active.TelemetryAge > FreshTelemetryLimit)
        throw new InvalidOperationException("Fresh moving treadmill telemetry is required before planned controls can resume.");
      if (active.LastCommandResult?.Disposition == TreadmillCommandDisposition.Unknown)
        throw new InvalidOperationException("An unknown treadmill command outcome must be resolved physically before controls can resume.");
      long currentGeneration = active.HardwareMode
        ? deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId).Treadmill.ConnectionGeneration
        : active.ConnectionGeneration;
      if (connectionGeneration != currentGeneration)
        throw new InvalidOperationException("The treadmill connection changed; refresh before resuming controls.");

      active.ConnectionGeneration = currentGeneration;
      active.CommandsSuspended = false;
      active.CommandsSuspendedReason = null;
      active.CanResumePlannedControls = false;
      active.RecoveryState = SessionRecoveryState.Recovered;
      active.ConnectionPhase = SessionConnectionPhase.Recovered;
      active.LastReconciledAtUtc = timeProvider.GetUtcNow();
      active.RecoveredAfterRestart = false;
      active.RestartRecoveryDeadlineUtc = null;
      if (active.RequiresHeartRate)
        active.HeartRateAutomationMode = active.DesiredHeartRateAutomationMode;
      active.HeartRateAutomationReason = "Planned controls resumed after fresh treadmill telemetry was confirmed.";
      active.Machine.MarkConfigurationChanged();
      active.ProcessedOperationIds.Add(operationId);
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      snapshotToPublish = active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
    _snapshotBroadcaster.Publish(null, snapshotToPublish);
    return snapshotToPublish!;
  }

  public async Task<bool> ResetAsync(
    CancellationToken cancellationToken = default,
    bool abandonStartupRecovery = false,
    bool reconcileRestoredDatabase = false)
  {
    await _armAdmission.WaitAsync(cancellationToken);
    var startupRecoveryEntered = false;
    try
    {
      if (abandonStartupRecovery)
      {
        await _startupRecoveryGate.WaitAsync(cancellationToken);
        startupRecoveryEntered = true;
      }
      DateTimeOffset resetAt = timeProvider.GetUtcNow();
      Guid? sessionId = null;
      var resetEffects = new LiveEffectBatch();
      Task? persistenceCompletion = null;
      await _gate.WaitAsync(cancellationToken);
      try
      {
        ThrowIfShutdownStarted();
        if (!_startupRecoveryComplete)
        {
          if (!abandonStartupRecovery)
            throw new InvalidOperationException("Gateway startup recovery is still in progress; try resetting again shortly.");
          _startupRecoveryComplete = true;
        }
        _resetPersistencePending = true;
        if (reconcileRestoredDatabase) _unfinishedSweepPending = true;
        if (_active is { } active)
        {
          sessionId = active.Definition.SessionId;
          if (active.Machine.State is not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted))
            active.Machine.Interrupt();

          if (active.Machine.State is SessionState.Interrupted or SessionState.Faulted)
          {
            LiveEffectMetadata metadata = CaptureEffectMetadata(active);
            active.TerminalPersistenceEffect = CreateInterruptionPersistenceEffect(
              metadata.SessionId,
              resetAt,
              "Simulator reset.",
              sweepUnfinished: true,
              missingSessionIsSuccess: true,
              allowIncompleteTelemetry: true);
            resetEffects.Add(
              metadata,
              active.TerminalPersistenceEffect,
              terminal: true);
          }
          else if ((active.Machine.State is SessionState.Completed or SessionState.Stopped) &&
                   active.TerminalPersistenceEffect is not null)
          {
            bool terminalPersistenceFailed;
            lock (_effectsQueueGate) terminalPersistenceFailed = _terminalPersistenceTail.IsFaulted;
            if (terminalPersistenceFailed)
            {
              active.TerminalPersistenceEffect = CreateInterruptionPersistenceEffect(
                active.Definition.SessionId,
                resetAt,
                "Terminal persistence failed; reset recovered the session as interrupted.",
                sweepUnfinished: true,
                missingSessionIsSuccess: true,
                allowIncompleteTelemetry: true);
            }
            resetEffects.Add(
              CaptureEffectMetadata(active),
              active.TerminalPersistenceEffect,
              terminal: true);
          }
        }
        else if (_unfinishedSweepPending)
        {
          resetEffects.Add(
            new LiveEffectMetadata(Guid.Empty, 0, 0, Guid.Empty),
            CreateUnfinishedSweepPersistenceEffect(
              resetAt,
              "Database restore reconciled a session that was not active in this gateway process."),
            terminal: true);
        }
        leaseCoordinator.RevokeCurrent();
        persistenceCompletion = QueueEffects(
          resetEffects,
          _effectsStop.Token,
          deferExecution: true,
          propagateFailure: true);
      }
      finally
      {
        _gate.Release();
      }

      // Persistence must finish before cleanup, but cleanup is not another
      // effect in the same batch: a failed effect would otherwise prevent the
      // cleanup/failure handler from ever running.
      Task resetCompletion = CompleteResetPipelineAsync(sessionId, persistenceCompletion!);
      Volatile.Write(ref _resetTail, IgnoreEffectFailureAsync(resetCompletion));
      return await AwaitTerminalPersistenceForClientAsync(resetCompletion, cancellationToken);
    }
    finally
    {
      if (startupRecoveryEntered) _startupRecoveryGate.Release();
      _armAdmission.Release();
    }
  }

  private async Task CompleteResetPipelineAsync(Guid? sessionId, Task persistenceCompletion)
  {
    try
    {
      await persistenceCompletion.ConfigureAwait(false);
      await CompleteResetAfterPersistenceAsync(sessionId).ConfigureAwait(false);
    }
    catch
    {
      // Retain the terminal in-memory session/effect (and any unfinished sweep)
      // for retry, but release the in-flight latch. Arm retries a retained
      // terminal effect; restored-database recovery remains blocked by the
      // unfinished-sweep flag until startup recovery reconciles it.
      await ReleaseConnectionsAfterFailedResetAsync(sessionId).ConfigureAwait(false);
      await _gate.WaitAsync(CancellationToken.None).ConfigureAwait(false);
      try
      {
        _resetPersistencePending = false;
        if (_unfinishedSweepPending)
        {
          _startupRecoveryComplete = false;
          _startupRecoveryWaitLogged = false;
          _startupRecoveryAttempt = 0;
          _nextStartupRecoveryAttemptUtc = DateTimeOffset.MinValue;
        }
      }
      finally { _gate.Release(); }
      throw;
    }
  }

  private async Task CompleteResetAfterPersistenceAsync(Guid? sessionId)
  {
    await _gate.WaitAsync(CancellationToken.None);
    try
    {
      bool targetStillCurrent = sessionId is { } targetSessionId
        ? _active is null || _active.Definition.SessionId == targetSessionId
        : _active is null;
      if (!targetStillCurrent)
        throw new InvalidOperationException("The active session changed while reset persistence was completing.");
    }
    finally
    {
      _gate.Release();
    }

    await ReleaseDeviceConnectionsAsync(CancellationToken.None);

    await _gate.WaitAsync(CancellationToken.None);
    try
    {
      bool targetStillCurrent = sessionId is { } targetSessionId
        ? _active is null || _active.Definition.SessionId == targetSessionId
        : _active is null;
      if (!targetStillCurrent)
        throw new InvalidOperationException("The active session changed while reset device cleanup was completing.");
      if (_active is not null)
      {
        _active = null;
        Volatile.Write(ref _current, CreateIdleSnapshot(timeProvider.GetUtcNow()));
      }
      _unfinishedSweepPending = false;
      _resetPersistencePending = false;
    }
    finally
    {
      _gate.Release();
    }
  }

  private async Task ReleaseConnectionsAfterFailedResetAsync(Guid? sessionId)
  {
    var releaseConnections = false;
    await _gate.WaitAsync(CancellationToken.None);
    try
    {
      bool targetStillCurrent = sessionId is { } targetSessionId
        ? _active is null || _active.Definition.SessionId == targetSessionId
        : _active is null;
      releaseConnections = targetStillCurrent;
    }
    finally
    {
      _gate.Release();
    }

    if (!releaseConnections) return;
    try
    {
      await ReleaseDeviceConnectionsAsync(CancellationToken.None);
      await _gate.WaitAsync(CancellationToken.None);
      try
      {
        if (sessionId is { } targetSessionId &&
            _active is { } active &&
            active.Definition.SessionId == targetSessionId &&
            !active.DeviceConnectionsReleased)
        {
          active.DeviceConnectionsReleased = true;
          PublishSnapshot(active, timeProvider.GetUtcNow(), AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
        }
      }
      finally
      {
        _gate.Release();
      }
    }
    catch (Exception exception)
    {
      logger.LogWarning(exception, "Run device connections could not be released after reset persistence failed.");
    }
  }

  public async Task<bool> TryBeginMaintenanceAsync(CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive) return false;
      if (_active is { Machine.State: not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted) }) return false;
      if (!applicationMaintenance.TryBegin()) return false;
      _maintenanceActive = true;
      return true;
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task CancelMaintenanceAsync(CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive)
      {
        _maintenanceActive = false;
        applicationMaintenance.End();
      }
    }
    finally
    {
      _gate.Release();
    }
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    logger.LogInformation("Starting authoritative live session loop at {UpdateRateHz} Hz.", 4);
    // These queues run independently of the 250 ms authoritative loop. They
    // get a separate cancellation source so shutdown can give a bounded drain
    // window without allowing a slow database or SignalR client to hang host
    // shutdown indefinitely.
    using var backgroundStop = new CancellationTokenSource();
    Task telemetryWriter = TelemetryWriter.RunAsync(backgroundStop.Token);
    Task snapshotBroadcaster = _snapshotBroadcaster.RunAsync(backgroundStop.Token);
    try
    {
      await TryCompleteStartupRecoveryAsync(stoppingToken);

      using var timer = new PeriodicTimer(UpdateInterval);
      while (await timer.WaitForNextTickAsync(stoppingToken))
      {
        await TickAsync(stoppingToken);
      }
    }
    finally
    {
      // Close admission immediately. An Arm already in preparation will see
      // this flag in its final admission check and execute its normal cleanup;
      // commands already inside _gate may still enqueue before we snapshot.
      _shutdownStarted = true;
      _shutdownStop.Cancel();
      Task pendingEffects;
      bool armAdmissionDrained = await _armAdmission.WaitAsync(
        TimeSpan.FromSeconds(5),
        CancellationToken.None);
      if (!armAdmissionDrained)
        logger.LogWarning("An in-flight arm did not stop during the bounded shutdown admission window.");
      try
      {
        await _gate.WaitAsync(CancellationToken.None);
        try
        {
          lock (_effectsQueueGate) pendingEffects = _effectsTail;
        }
        finally
        {
          _gate.Release();
        }
      }
      finally
      {
        if (armAdmissionDrained) _armAdmission.Release();
      }

      // Once the authoritative loop has stopped there are no legitimate new
      // telemetry producers. Close and drain the writer before canceling a
      // terminal effect that may be waiting on its flush barrier. Otherwise a
      // canceled barrier could let finalization run while the writer still
      // accepts a late sample, leaving that sample behind the terminal row.
      TelemetryWriter.Complete();
      try
      {
        await telemetryWriter.WaitAsync(TimeSpan.FromSeconds(5));
      }
      catch (TimeoutException)
      {
        logger.LogWarning("Live-session telemetry did not drain during the bounded shutdown window.");
        backgroundStop.Cancel();
        try { await telemetryWriter; }
        catch (OperationCanceledException) { }
      }

      try
      {
        await pendingEffects.WaitAsync(TimeSpan.FromSeconds(5));
      }
      catch (TimeoutException)
      {
        logger.LogWarning("Live-session persistence effects did not drain during the bounded shutdown window.");
        _effectsStop.Cancel();
        try { await pendingEffects.WaitAsync(TimeSpan.FromSeconds(5)); }
        catch (TimeoutException)
        {
          logger.LogWarning("Live-session persistence effects did not stop after shutdown cancellation.");
        }
        catch (OperationCanceledException) { }
      }

      _snapshotBroadcaster.Complete();
      try
      {
        await snapshotBroadcaster.WaitAsync(TimeSpan.FromSeconds(5));
      }
      catch (TimeoutException)
      {
        logger.LogWarning("Live-session SignalR publication did not drain during the bounded shutdown window.");
        backgroundStop.Cancel();
        try { await snapshotBroadcaster; }
        catch (OperationCanceledException) { }
      }
    }
  }

  private async Task TickAsync(CancellationToken cancellationToken)
  {
    AutomatedCommandRequest? automatedCommand = null;
    LiveSnapshot? liveSnapshotToPublish = null;
    ActiveSessionSnapshot? sessionSnapshotToPublish = null;
    var startupRecoveryNeeded = false;
    var effectsQueued = false;
    var effects = new LiveEffectBatch();
    var releaseConnections = false;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (!_startupRecoveryComplete)
      {
        // Recovery performs database/device IO and is intentionally retried
        // outside this authoritative state lock.
        startupRecoveryNeeded = true;
      }
      else
      {
        var now = timeProvider.GetUtcNow();
        if (_active is not { } active)
        {
          Volatile.Write(ref _current, CreateIdleSnapshot(now));
          liveSnapshotToPublish = Current;
        }
        else
        {
          if (ShouldFreezeTerminalSnapshot(active.Machine.State, active.DeviceConnectionsReleased))
          {
            // Keep terminal workout telemetry immutable, but continue to expose
            // current lease and device-connection metadata after release.
            RefreshFrozenTerminalSnapshotMetadata(
              active,
              AccessForCurrentLease(),
              leaseCoordinator.Current?.ExpiresAt);
            liveSnapshotToPublish = Current;
            sessionSnapshotToPublish = active.Snapshot;
          }
          else
          {
            DeviceTelemetrySnapshot? hardwareTelemetry = ApplyHardwareTelemetry(active, now, effects);
            if (active.RecoveryState == SessionRecoveryState.Recovered &&
                active.LastReconciledAtUtc is { } reconciledAt &&
                now - reconciledAt >= TimeSpan.FromSeconds(5))
            {
              active.RecoveryState = SessionRecoveryState.None;
              active.ConnectionPhase = SessionConnectionPhase.Ready;
            }
            if (active.RecoveredAfterRestart && active.RestartRecoveryDeadlineUtc <= now &&
                active.RecoveryState == SessionRecoveryState.RestartTracking && !active.IsMoving)
            {
              active.Machine.Interrupt();
              active.ConnectionPhase = SessionConnectionPhase.NeedsAttention;
              active.CommandsSuspendedReason = "Session recovery timed out without fresh treadmill movement.";
              LiveEffectMetadata metadata = CaptureEffectMetadata(active);
              active.TerminalPersistenceEffect = CreateInterruptionPersistenceEffect(
                metadata.SessionId,
                now,
                "Fresh movement from the enrolled treadmill was not confirmed after gateway restart.",
                allowIncompleteTelemetry: true);
              effects.Add(metadata, active.TerminalPersistenceEffect, terminal: true);
            }
            if (!active.HardwareMode)
            {
              active.HeartRateAge = active.HeartRateObservedAt is null
                ? TimeSpan.MaxValue
                : NonNegative(now - active.HeartRateObservedAt.Value);
              if (active.HeartRateAge > FreshTelemetryLimit)
                active.HeartRateBpm = null;
            }
            UpdateMotion(active, now);
            if (active.Machine.State == SessionState.Running)
            {
              int previousStepIndex = active.Progression.CurrentStepIndex;
              foreach (WorkoutStepTransition transition in active.Progression.Advance(active.Elapsed, active.DistanceKilometers))
              {
                LiveEffectMetadata metadata = CaptureEffectMetadata(active);
                WorkoutStepTransitionEvent transitionEvent = new(
                  transition.CompletedStepIndex,
                  transition.CurrentStepIndex,
                  active.Progression.CurrentStep?.Cue,
                  now);
                effects.Add(metadata, async token =>
                {
                  using IServiceScope transitionScope = scopeFactory.CreateScope();
                  await transitionScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
                    metadata.SessionId,
                    transitionEvent,
                    token);
                });
              }

              if (active.Progression.CurrentStepIndex != previousStepIndex)
              {
                active.SpeedOverrideKph = null;
                active.InclineOverridePercent = null;
              }

              if (active.Progression.IsComplete)
              {
                if (CompletionAction(active) == WorkoutCompletionAction.Finalize)
                {
                  FinalizeCompletedSession(active, now, effects);
                }
                else
                {
                  AddWarningOnce(active,
                    "Workout steps are complete. The session will stay open until the treadmill confirms a physical stop; use the physical Stop control if needed.");
                }
              }
              else
              {
                DateTimeOffset sampleAt = GetLogicalSessionTimestamp(active, now);
                if (active.SampleCadence.TryAdvance(sampleAt))
                {
                  SessionSample sample = CreateSample(active, sampleAt);
                  SessionRecoveryCheckpoint checkpoint = CreateRecoveryCheckpoint(active, sampleAt);
                  SessionTelemetryWrite telemetry = new(
                    active.Definition.SessionId,
                    sample,
                    checkpoint,
                    active.Machine.Version,
                    active.ConnectionGeneration,
                    active.AutomationAuthorityId,
                    hardwareTelemetry is null
                      ? null
                      : CaptureHeartRateDiagnostic(
                        active.Definition.UserProfileId,
                        hardwareTelemetry,
                        sample,
                        FreshTelemetryLimit));
                  // Reserve the write while the authoritative gate is still held.
                  // A terminal transition cannot begin its flush until this write
                  // is visible to the writer, so the final sample cannot slip into
                  // the capture-to-enqueue gap after the database row is terminal.
                  if (!TelemetryWriter.TryEnqueue(telemetry))
                  {
                    logger.LogDebug(
                      "The live-session telemetry writer has completed; the next gateway startup will recover from the latest durable checkpoint.");
                  }
                }
              }
            }

            if (ShouldReleaseDeviceConnections(
                  active.Machine.State,
                  active.DeviceConnectionsReleased,
                  active.IsMoving,
                  active.MeasuredSpeedKph,
                  _resetPersistencePending))
            {
              active.DeviceConnectionsReleased = true;
              releaseConnections = true;
            }

            PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
            automatedCommand = BuildAutomatedCommand(active, now);
            liveSnapshotToPublish = Current;
            sessionSnapshotToPublish = active.Snapshot;
            if (effects.HasTerminalEffects)
            {
              _ = QueueEffects(
                effects,
                _effectsStop.Token,
                deferExecution: true,
                propagateFailure: true);
              effectsQueued = true;
            }
            if (!effects.IsEmpty && !effectsQueued)
            {
              _ = QueueEffects(effects, _effectsStop.Token);
              effectsQueued = true;
            }
          }
        }
      }
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogError(exception, "The authoritative simulator tick failed; the next tick will retry without issuing hardware commands.");
    }
    finally
    {
      _gate.Release();
    }

    if (releaseConnections)
      await ReleaseDeviceConnectionsAsync(cancellationToken);

    if (startupRecoveryNeeded)
      await TryCompleteStartupRecoveryAsync(cancellationToken);

    _snapshotBroadcaster.Publish(liveSnapshotToPublish, sessionSnapshotToPublish);

    if (automatedCommand is not null)
    {
      await ExecuteAutomatedCommandAsync(automatedCommand, cancellationToken);
    }

  }

  private async Task RunEffectsAsync(
    LiveEffectBatch effects,
    CancellationToken cancellationToken,
    bool propagateFailure)
  {
    var entered = false;
    try
    {
      await _effectsGate.WaitAsync(effects.HasTerminalEffects ? CancellationToken.None : cancellationToken);
      entered = true;
      await effects.ExecuteAsync(IsCurrentEffectAsync, cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      // Host shutdown owns cancellation; no new physical command is issued by an
      // effect runner after the authoritative loop stops.
      if (propagateFailure) throw;
    }
    catch (Exception exception)
    {
      logger.LogError(exception, "A live-session effect batch failed outside the authoritative state lock.");
      if (propagateFailure) throw;
    }
    finally
    {
      if (entered) _effectsGate.Release();
    }
  }

  private Task QueueEffects(
    LiveEffectBatch effects,
    CancellationToken cancellationToken,
    bool deferExecution = false,
    bool propagateFailure = false)
  {
    lock (_effectsQueueGate)
    {
      Task previous = _effectsTail;
      Task operation = deferExecution
        ? Task.Run(() => RunQueuedEffectsAsync(previous, effects, cancellationToken, propagateFailure))
        : RunQueuedEffectsAsync(previous, effects, cancellationToken, propagateFailure);
      _effectsTail = propagateFailure ? IgnoreEffectFailureAsync(operation) : operation;
      if (effects.HasTerminalEffects) _terminalPersistenceTail = operation;
      return operation;
    }
  }

  private async Task RunQueuedEffectsAsync(
    Task previous,
    LiveEffectBatch effects,
    CancellationToken cancellationToken,
    bool propagateFailure)
  {
    await previous.ConfigureAwait(false);
    await RunEffectsAsync(effects, cancellationToken, propagateFailure).ConfigureAwait(false);
  }

  private static async Task IgnoreEffectFailureAsync(Task operation)
  {
    try { await operation.ConfigureAwait(false); }
    catch { }
  }

  private async Task<bool> IsCurrentEffectAsync(
    LiveEffectMetadata metadata,
    CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      return _active is { } active &&
        active.Definition.SessionId == metadata.SessionId &&
        active.Machine.Version == metadata.SessionVersion &&
        active.ConnectionGeneration == metadata.ConnectionGeneration &&
        active.AutomationAuthorityId == metadata.AuthorityId;
    }
    finally
    {
      _gate.Release();
    }
  }

  private async Task<bool> IsCurrentTelemetryAsync(
    SessionTelemetryWrite write,
    CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      return _active is { } active && IsTelemetryWriteCurrent(
        write,
        active.Definition.SessionId,
        active.Machine.Version,
        active.ConnectionGeneration,
        active.AutomationAuthorityId);
    }
    finally
    {
      _gate.Release();
    }
  }

  internal static bool IsTelemetryWriteCurrent(
    SessionTelemetryWrite write,
    Guid activeSessionId,
    long activeVersion,
    long activeConnectionGeneration,
    Guid activeAuthorityId)
  {
    // A captured sample remains valid across later state-only transitions and
    // reconnects. The authority changes when ownership is replaced; generation
    // only rejects impossible future writes because a queued sample from the
    // immediately preceding connection still belongs to this session authority.
    bool currentOrPriorVersion = write.SessionVersion <= activeVersion;
    bool currentOrPriorConnectionGeneration = write.ConnectionGeneration <= activeConnectionGeneration;
    return write.SessionId == activeSessionId &&
      currentOrPriorConnectionGeneration &&
      write.AuthorityId == activeAuthorityId &&
      currentOrPriorVersion;
  }

  private static LiveEffectMetadata CaptureEffectMetadata(ActiveRun active) => new(
    active.Definition.SessionId,
    active.Machine.Version,
    active.ConnectionGeneration,
    active.AutomationAuthorityId);

  internal static bool ShouldFreezeTerminalSnapshot(SessionState state, bool deviceConnectionsReleased) =>
    deviceConnectionsReleased &&
    state is SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted;

  internal static bool ShouldReleaseDeviceConnections(
    SessionState state,
    bool deviceConnectionsReleased,
    bool isMoving,
    double measuredSpeedKph,
    bool resetPersistencePending = false) =>
    !resetPersistencePending &&
    !deviceConnectionsReleased &&
    state is SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted &&
    !isMoving &&
    measuredSpeedKph <= 0.05;

  private AutomatedCommandRequest? BuildAutomatedCommand(ActiveRun active, DateTimeOffset now)
  {
    if (active.Machine.State != SessionState.Running)
      return null;

    if (active.Progression.IsComplete && active.HardwareMode)
    {
      if (CompletionAction(active) != WorkoutCompletionAction.RequestStop)
      {
        return null;
      }

      active.CompletionStopAttempted = true;
      return new AutomatedCommandRequest(
        TreadmillCommandKind.Stop,
        null,
        TreadmillCommandOrigin.WorkoutCompletion,
        active.Machine.Version,
        active.AutomationAuthorityId,
        active.AutomationAuthorityHolder);
    }

    if (active.CommandsSuspended ||
        active.TelemetryAge > FreshTelemetryLimit)
      return null;

    if (active.RequiresHeartRate &&
        (active.HeartRateBpm is null || active.HeartRateAge is null || active.HeartRateAge > FreshTelemetryLimit))
    {
      SuspendAutomation(active, "Heart-rate telemetry is stale; heart-rate automation is suspended.");
      return null;
    }

    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId);
    if (active.HardwareMode && devices.Treadmill.ConnectionGeneration != active.ConnectionGeneration)
      return null;

    WorkoutStep? step = active.Progression.CurrentStep;
    if (step?.Speed is HeartRateSpeed or HeartRateZoneSpeed)
    {
      (double minimum, double maximum) = step.Speed switch
      {
        HeartRateSpeed target => (target.MinimumKilometersPerHour, target.MaximumKilometersPerHour),
        HeartRateZoneSpeed target => (target.MinimumKilometersPerHour, target.MaximumKilometersPerHour),
        _ => throw new InvalidOperationException(),
      };
      if (active.SpeedRange is { } range)
      {
        minimum = Math.Max(minimum, (double)range.Minimum);
        maximum = Math.Min(Math.Min(maximum, active.MaximumSpeedKph), (double)range.Maximum);
        HeartRateTarget? target = active.Progression.HeartRateTarget;
        HeartRateSpeedDecision decision = active.HeartRateController.Evaluate(new HeartRateSpeedControllerInput(
          now,
          active.HeartRateAutomationMode,
          active.HeartRateBpm,
          target?.MinimumBpm,
          target?.MaximumBpm,
          active.HeartRateAge,
          active.TelemetryAge,
          active.MeasuredSpeedKph,
          minimum,
          maximum,
          (double)range.Increment,
          active.CanSetSpeedRemotely && (!active.HardwareMode || devices.Treadmill.State == DeviceConnectionState.Ready)));
        active.HeartRateAutomationReason = decision.Reason;
        if (decision.ShouldExecute && decision.TargetSpeedKph is { } hrTarget)
          return new AutomatedCommandRequest(
            TreadmillCommandKind.SetSpeed,
            hrTarget,
            TreadmillCommandOrigin.HeartRateAutomation,
            active.Machine.Version,
            active.AutomationAuthorityId,
            active.AutomationAuthorityHolder);
      }
    }
    else if (active.CanSetSpeedRemotely &&
             active.SpeedRange is { } speedRange &&
             active.SpeedOverrideKph is null &&
             (step?.Speed is SpeedRamp || active.AppliedSpeedPlanStepIndex != active.Progression.CurrentStepIndex))
    {
      double requested = Math.Clamp(
        active.RequestedSpeedKph,
        (double)speedRange.Minimum,
        Math.Min(active.MaximumSpeedKph, (double)speedRange.Maximum));
      if (Math.Abs(requested - active.MeasuredSpeedKph) > Math.Max(0.15, (double)speedRange.Increment / 2))
        return new AutomatedCommandRequest(
          TreadmillCommandKind.SetSpeed,
          requested,
          TreadmillCommandOrigin.PlannedTransition,
          active.Machine.Version,
          active.AutomationAuthorityId,
          active.AutomationAuthorityHolder);

      active.AppliedSpeedPlanStepIndex = active.Progression.CurrentStepIndex;
    }

    if (active.CanSetInclineRemotely &&
        active.InclineRange is { } inclineRange &&
        active.InclineOverridePercent is null &&
        (step?.Incline is InclineRamp || active.AppliedInclinePlanStepIndex != active.Progression.CurrentStepIndex))
    {
      double requested = Math.Clamp(
        active.RequestedInclinePercent,
        (double)inclineRange.Minimum,
        (double)inclineRange.Maximum);
      if (Math.Abs(requested - active.MeasuredInclinePercent) > Math.Max(0.15, (double)inclineRange.Increment / 2))
        return new AutomatedCommandRequest(
          TreadmillCommandKind.SetIncline,
          requested,
          TreadmillCommandOrigin.PlannedTransition,
          active.Machine.Version,
          active.AutomationAuthorityId,
          active.AutomationAuthorityHolder);

      active.AppliedInclinePlanStepIndex = active.Progression.CurrentStepIndex;
    }

    return null;
  }

  private WorkoutCompletionAction CompletionAction(ActiveRun active)
  {
    DeviceConnectionSnapshot treadmill = deviceCoordinator
      .CurrentForProfile(active.Definition.UserProfileId)
      .Treadmill;
    return WorkoutCompletionStopPolicy.Evaluate(new WorkoutCompletionStopContext(
      active.Progression.IsComplete,
      active.HardwareMode,
      active.TelemetryAge <= FreshTelemetryLimit,
      active.IsMoving,
      active.MeasuredSpeedKph,
      active.CanStopRemotely,
      treadmill.State == DeviceConnectionState.Ready,
      treadmill.ConnectionGeneration == active.ConnectionGeneration,
      active.CompletionStopAttempted));
  }

  private async Task ExecuteAutomatedCommandAsync(
    AutomatedCommandRequest request,
    CancellationToken cancellationToken)
  {
    try
    {
      TreadmillCommandIntent intent = await PrepareCommandAsync(
        Guid.NewGuid(),
        request.Kind,
        request.ExpectedSessionVersion,
        request.LeaseId,
        request.HolderId,
        request.TargetValue,
        request.Origin,
        cancellationToken);
      bool simulator = _active?.HardwareMode == false;
      TreadmillCommandResult result;
      if (simulator)
      {
        if (!await ApplySimulatedCommandMeasurementAsync(intent, cancellationToken))
        {
          logger.LogInformation(
            "A simulator command was invalidated before its measurement was applied; no result or retry was recorded.");
          return;
        }
        result = new TreadmillCommandResult(
          intent.OperationId,
          intent.Kind,
          TreadmillCommandDisposition.Confirmed,
          intent.RequestedValue,
          intent.RequestedValue,
          intent.RequestedValue,
          "Simulator confirmed the command through the shared command-intent seam.",
          intent.ConnectionGeneration,
          intent.IssuedAt,
          timeProvider.GetUtcNow());
      }
      else
      {
        result = await commandCoordinator.ExecuteAsync(intent, this, cancellationToken);
      }
      await RecordCommandResultAsync(intent, result, CancellationToken.None);
    }
    catch (InvalidOperationException exception)
    {
      logger.LogInformation(exception, "An automatic treadmill command was invalidated before execution; no retry was scheduled.");
    }
  }

  private void FinalizeCompletedSession(
    ActiveRun active,
    DateTimeOffset completedAt,
    LiveEffectBatch effects)
  {
    if (active.Machine.State == SessionState.Completed) return;

    DateTimeOffset terminalAt = GetTerminalTimestamp(active, completedAt);
    SessionSummary summary = CreateSummary(active, SessionState.Completed, terminalAt);
    active.Machine.Complete();
    LiveEffectMetadata metadata = CaptureEffectMetadata(active);
    SessionCompletedEvent completedEvent = new(terminalAt);
    active.TerminalPersistenceEffect = CreateTerminalPersistenceEffect(metadata.SessionId, completedEvent, summary);
    effects.Add(metadata, active.TerminalPersistenceEffect, terminal: true);
  }

  private Func<CancellationToken, Task> CreateTerminalPersistenceEffect(
    Guid sessionId,
    SessionEvent terminalEvent,
    SessionSummary summary)
  {
    var eventAppended = false;
    var finalized = false;
    return async cancellationToken =>
    {
      if (finalized) return;

      // The terminal state transition increments the machine version. Drain the
      // immediately preceding telemetry before making the database row terminal,
      // otherwise the store correctly rejects a late append and loses the tail.
      await PersistAfterTelemetryFlushAsync(
        TelemetryWriter.FlushSessionAsync(sessionId, cancellationToken),
        async () =>
        {
          using var persistenceAttempt = new CancellationTokenSource();
          using CancellationTokenRegistration shutdownDeadline = RegisterShutdownPersistenceDeadline(
            cancellationToken,
            persistenceAttempt);
          CancellationToken persistenceToken = persistenceAttempt.Token;
          try
          {
            using IServiceScope scope = scopeFactory.CreateScope();
            ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
            if (!eventAppended)
            {
              await store.AppendEventAsync(sessionId, terminalEvent, persistenceToken);
              eventAppended = true;
            }

            if (finalized) return;
            await store.FinalizeAsync(summary, persistenceToken);
            await scope.ServiceProvider.GetRequiredService<IPolarH10RecordingStore>()
              .QueueStopAsync(sessionId, timeProvider.GetUtcNow(), persistenceToken);
            finalized = true;
          }
          catch (KeyNotFoundException)
          {
            // Stop-and-discard may intentionally delete the row while a slow
            // terminal effect is still draining. Deletion is already durable.
            finalized = true;
          }
        });
    };
  }

  private Func<CancellationToken, Task> CreateInterruptionPersistenceEffect(
    Guid sessionId,
    DateTimeOffset interruptedAt,
    string reason,
    bool sweepUnfinished = false,
    bool missingSessionIsSuccess = false,
    bool allowIncompleteTelemetry = false) =>
    async cancellationToken =>
    {
      string effectiveReason = reason;
      async Task PersistInterruptionAsync()
      {
        using var persistenceAttempt = new CancellationTokenSource();
        using CancellationTokenRegistration shutdownDeadline = RegisterShutdownPersistenceDeadline(
          cancellationToken,
          persistenceAttempt);
        CancellationToken persistenceToken = persistenceAttempt.Token;
        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore sessionStore = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        try
        {
          await sessionStore.InterruptAsync(sessionId, interruptedAt, effectiveReason, persistenceToken);
        }
        catch (KeyNotFoundException) when (missingSessionIsSuccess)
        {
          // History deletion already made the requested reset durable.
        }
        if (sweepUnfinished)
          await sessionStore.InterruptUnfinishedAsync(interruptedAt, effectiveReason, persistenceToken);
        await scope.ServiceProvider.GetRequiredService<IPolarH10RecordingStore>()
          .QueueStopAsync(sessionId, timeProvider.GetUtcNow(), persistenceToken);
      }

      Task flush = TelemetryWriter.FlushSessionAsync(sessionId, cancellationToken);
      if (!allowIncompleteTelemetry)
      {
        await PersistAfterTelemetryFlushAsync(flush, PersistInterruptionAsync);
        return;
      }
      try
      {
        await flush;
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        throw;
      }
      catch (Exception exception)
      {
        effectiveReason = $"{reason} Accepted telemetry was incomplete and the session was not eligible for automatic export.";
        logger.LogWarning(
          exception,
          "Interrupting session {SessionId} with an explicitly incomplete telemetry tail after reset/recovery.",
          sessionId);
      }
      await PersistInterruptionAsync();
    };

  private Func<CancellationToken, Task> CreateUnfinishedSweepPersistenceEffect(
    DateTimeOffset interruptedAt,
    string reason) =>
    async cancellationToken =>
    {
      using var persistenceAttempt = new CancellationTokenSource();
      using CancellationTokenRegistration shutdownDeadline = RegisterShutdownPersistenceDeadline(
        cancellationToken,
        persistenceAttempt);
      using IServiceScope scope = scopeFactory.CreateScope();
      await scope.ServiceProvider.GetRequiredService<ISessionStore>().InterruptUnfinishedAsync(
        interruptedAt,
        reason,
        persistenceAttempt.Token);
    };

  private static CancellationTokenRegistration RegisterShutdownPersistenceDeadline(
    CancellationToken shutdownToken,
    CancellationTokenSource persistenceAttempt) =>
    shutdownToken.Register(static state =>
    {
      var attempt = (CancellationTokenSource)state!;
      attempt.CancelAfter(ShutdownFinalPersistenceTimeout);
    }, persistenceAttempt);

  internal static async Task PersistAfterTelemetryFlushAsync(
    Task telemetryFlush,
    Func<Task> terminalPersistence)
  {
    // Never make a session terminal after accepted telemetry failed to persist.
    // A normal store outage is retried by the writer; cancellation leaves the
    // row unfinished so startup recovery cannot mistake an incomplete timeline
    // for a coherent completed activity suitable for export.
    await telemetryFlush;
    await terminalPersistence();
  }

  internal static async Task WaitForTerminalPersistenceBarrierAsync(
    Task barrier,
    TimeSpan timeout,
    CancellationToken cancellationToken)
  {
    try
    {
      await barrier.WaitAsync(timeout, cancellationToken);
    }
    catch (TimeoutException)
    {
      throw new InvalidOperationException(
        "A previous terminal session operation is still finishing; try again shortly.");
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      throw new InvalidOperationException(
        "A previous terminal session operation failed; retry starting the workout to retry persistence.",
        exception);
    }
    catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
    {
      throw new InvalidOperationException(
        "A previous terminal session operation was interrupted; retry starting the workout to retry persistence.",
        exception);
    }
  }

  private async Task AwaitQueuedEffectsForClientAsync(
    Task queuedEffects,
    CancellationToken cancellationToken)
  {
    try
    {
      await queuedEffects.WaitAsync(TerminalPersistenceClientWaitTimeout, cancellationToken);
    }
    catch (TimeoutException exception)
    {
      logger.LogWarning(
        exception,
        "Session persistence is still queued after {TimeoutSeconds} seconds; the state change was accepted but durability is not yet confirmed.",
        TerminalPersistenceClientWaitTimeout.TotalSeconds);
    }
  }

  private async Task<bool> AwaitTerminalPersistenceForClientAsync(Task terminalPersistence, CancellationToken cancellationToken)
  {
    try
    {
      await terminalPersistence.WaitAsync(TerminalPersistenceClientWaitTimeout, cancellationToken);
      return true;
    }
    catch (TimeoutException)
    {
      logger.LogWarning(
        "Terminal persistence is still draining telemetry after {TimeoutSeconds} seconds; it will continue in the background before the database row becomes terminal.",
        TerminalPersistenceClientWaitTimeout.TotalSeconds);
      return false;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      throw new InvalidOperationException(
        "Terminal persistence or device cleanup failed; retry the operation after the underlying problem is resolved.",
        exception);
    }
    catch (OperationCanceledException exception) when (!cancellationToken.IsCancellationRequested)
    {
      throw new InvalidOperationException(
        "Terminal persistence was interrupted; retry the operation after the underlying problem is resolved.",
        exception);
    }
  }

  private async Task<bool> ApplySimulatedCommandMeasurementAsync(
    TreadmillCommandIntent intent,
    CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ActiveRun? active = _active;
      if (active is null ||
          active.HardwareMode ||
          active.Machine.State != SessionState.Running ||
          !IsCurrent(intent))
      {
        return false;
      }
      if (intent.Kind == TreadmillCommandKind.SetSpeed && intent.RequestedValue is { } speed)
      {
        active.MeasuredSpeedKph = speed;
        active.IsMoving = speed > SessionStateMachine.PhysicalStartThresholdKph;
      }
      else if (intent.Kind == TreadmillCommandKind.SetIncline && intent.RequestedValue is { } incline)
      {
        active.MeasuredInclinePercent = incline;
      }
      active.TelemetryAge = TimeSpan.Zero;
      return true;
    }
    finally
    {
      _gate.Release();
    }
  }

  private static void SuspendAutomation(ActiveRun active, string reason)
  {
    if (active.HeartRateAutomationMode is HeartRateAutomationMode.Disabled or HeartRateAutomationMode.Shadow or HeartRateAutomationMode.DecreaseOnly or HeartRateAutomationMode.Full)
      active.DesiredHeartRateAutomationMode = active.HeartRateAutomationMode;
    active.HeartRateController.ResetDwell();
    active.CommandsSuspended = true;
    active.CommandsSuspendedReason = reason;
    active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
    active.HeartRateAutomationReason = reason;
    AddWarningOnce(active, reason);
  }

  private async Task TryCompleteStartupRecoveryAsync(CancellationToken cancellationToken)
  {
    await _startupRecoveryGate.WaitAsync(cancellationToken);
    DateTimeOffset now = timeProvider.GetUtcNow();
    try
    {
      if (_startupRecoveryComplete) return;
      if (now < _nextStartupRecoveryAttemptUtc) return;
      using IServiceScope recoveryScope = scopeFactory.CreateScope();
      ISessionStore sessionStore = recoveryScope.ServiceProvider.GetRequiredService<ISessionStore>();
      await sessionStore.ReconcileActiveSessionsAsync(now, cancellationToken);
      RecoverableWorkoutSession? recoverable = await sessionStore.FindRecoverableAsync(cancellationToken);
      if (recoverable is null)
      {
        await sessionStore.InterruptUnfinishedAsync(
          now,
          "Gateway restarted without a complete recovery checkpoint.",
          cancellationToken);
        await MarkStartupRecoveryCompleteAsync(null, cancellationToken);
        return;
      }

      ActiveRun? restored = await RestoreActiveSessionAsync(recoverable, cancellationToken);
      await MarkStartupRecoveryCompleteAsync(restored, cancellationToken);
    }
    catch (SqliteException exception) when (
      exception.SqliteErrorCode == 1 &&
      exception.Message.Contains("no such table", StringComparison.OrdinalIgnoreCase))
    {
      if (!_startupRecoveryWaitLogged)
      {
        logger.LogWarning(
          "Session restart recovery is waiting for database schema readiness; no treadmill command was issued.");
        _startupRecoveryWaitLogged = true;
      }
      ScheduleStartupRecoveryRetry(now);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogError(exception, "Session restart recovery was rejected; no treadmill command was issued.");
      try
      {
        using IServiceScope failureScope = scopeFactory.CreateScope();
        await failureScope.ServiceProvider.GetRequiredService<ISessionStore>().InterruptUnfinishedAsync(
          now,
          "Gateway restart recovery data was invalid or unavailable.",
          cancellationToken);
        await MarkStartupRecoveryCompleteAsync(null, cancellationToken);
      }
      catch (Exception cleanupException) when (cleanupException is not OperationCanceledException)
      {
        logger.LogWarning(cleanupException, "Session restart recovery cleanup was unavailable; retrying with bounded backoff.");
        ScheduleStartupRecoveryRetry(now);
      }
    }
    finally
    {
      _startupRecoveryGate.Release();
    }
  }

  private async Task MarkStartupRecoveryCompleteAsync(
    ActiveRun? restored,
    CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_startupRecoveryComplete) return;
      if (restored is not null)
      {
        if (_active is not null)
          throw new InvalidOperationException("A live session appeared while startup recovery was being committed.");
        _active = restored;
        PublishSnapshot(restored, timeProvider.GetUtcNow(), AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      }

      _startupRecoveryComplete = true;
      _unfinishedSweepPending = false;
      _resetPersistencePending = false;
      _startupRecoveryAttempt = 0;
      _nextStartupRecoveryAttemptUtc = DateTimeOffset.MinValue;
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task RestartStartupRecoveryAsync(CancellationToken cancellationToken = default)
  {
    // A reset returning false has only exceeded the client wait; its terminal
    // persistence may still be running. Re-arm recovery only after that tail
    // settles, and preserve fail-closed admission if persistence actually
    // failed and left reset cleanup pending.
    try
    {
      await Volatile.Read(ref _resetTail).WaitAsync(
        TerminalPersistenceClientWaitTimeout,
        cancellationToken);
    }
    catch (TimeoutException exception)
    {
      throw new InvalidOperationException(
        "Reset persistence is still running; retry startup recovery shortly.",
        exception);
    }
    await _armAdmission.WaitAsync(cancellationToken);
    try
    {
      await _startupRecoveryGate.WaitAsync(cancellationToken);
      try
      {
        await _gate.WaitAsync(cancellationToken);
        try
        {
          if (_resetPersistencePending)
            throw new InvalidOperationException("Startup recovery cannot restart while reset persistence is unresolved.");
          _startupRecoveryComplete = false;
          _startupRecoveryWaitLogged = false;
          _startupRecoveryAttempt = 0;
          _nextStartupRecoveryAttemptUtc = DateTimeOffset.MinValue;
        }
        finally
        {
          _gate.Release();
        }
      }
      finally
      {
        _startupRecoveryGate.Release();
      }
    }
    finally
    {
      _armAdmission.Release();
    }

    await TryCompleteStartupRecoveryAsync(cancellationToken);
  }

  private void ScheduleStartupRecoveryRetry(DateTimeOffset nowUtc)
  {
    _startupRecoveryAttempt = Math.Min(_startupRecoveryAttempt + 1, 8);
    double seconds = Math.Min(30, Math.Pow(2, _startupRecoveryAttempt - 1));
    _nextStartupRecoveryAttemptUtc = nowUtc.AddSeconds(seconds);
  }

  private async Task<ActiveRun?> RestoreActiveSessionAsync(
    RecoverableWorkoutSession recoverable,
    CancellationToken cancellationToken)
  {
    StoredWorkoutSession stored = recoverable.Session;
    SessionRecoveryCheckpoint checkpoint = recoverable.Checkpoint;
    if (stored.StartedAt is null || checkpoint.State != SessionState.Running)
      throw new InvalidOperationException("Only a running session with a start time can be recovered.");

    (_, _, WorkoutDefinition workout) = await LoadPlanAsync(
      stored.Definition.UserProfileId, stored.Definition.WorkoutRevisionId, cancellationToken);
    SessionExecutionConfiguration configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
      stored.Definition.ControllerConfigurationJson,
      new JsonSerializerOptions(JsonSerializerDefaults.Web))
      ?? throw new InvalidOperationException("The stored session configuration is invalid.");
    bool requiresHeartRate = ContainsHeartRateTarget(workout.Blocks);
    await deviceCoordinator.HoldRunConnectionsAsync(
      stored.Definition.UserProfileId,
      requiresHeartRate,
      cancellationToken);
    using IServiceScope enrollmentScope = scopeFactory.CreateScope();
    VersionedDeviceEnrollment? currentEnrollment = await enrollmentScope.ServiceProvider
      .GetRequiredService<IDeviceEnrollmentStore>()
      .FindActiveAsync(DeviceRole.Treadmill, cancellationToken);
    if (configuration.Treadmill?.IdentityFingerprint is not { Length: 64 } expectedFingerprint ||
        currentEnrollment?.Enrollment.IdentityFingerprint is not { } currentFingerprint ||
        !string.Equals(expectedFingerprint, currentFingerprint, StringComparison.Ordinal))
    {
      await enrollmentScope.ServiceProvider.GetRequiredService<ISessionStore>().InterruptUnfinishedAsync(
        timeProvider.GetUtcNow(),
        "Gateway restart recovery could not confirm the same enrolled treadmill.",
        cancellationToken);
      await deviceCoordinator.ReleaseRunConnectionsAsync(cancellationToken);
      return null;
    }
    var progression = new WorkoutProgression(workout);
    progression.Restore(checkpoint.Progression);
    var controllerSettings = configuration.Profile.HeartRateController is { } settings
      ? new HeartRateControllerSettings(
        settings.IncreaseStepKph,
        settings.IncreaseCooldownSeconds,
        settings.DecreaseStepKph,
        settings.DecreaseCooldownSeconds)
      : HeartRateControllerSettings.Default;
    TreadmillCapabilities? capabilities = configuration.Treadmill?.Capabilities;
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(stored.Definition.UserProfileId);
    IReadOnlyList<SessionSample> normalizedStoredSamples = SessionSampleTimeline.Normalize(stored.Samples);
    var active = new ActiveRun(
      stored.Definition,
      workout,
      SessionStateMachine.Restore(timeProvider, SessionState.Running, checkpoint.SessionVersion),
      progression,
      configuration.Profile.MaximumSpeedKph ?? 20,
      configuration.Profile.WeightKilograms,
      timeProvider.GetUtcNow(),
      hardwareMode: stored.Definition.Origin == SessionOrigin.Hardware,
      requiresHeartRate,
      MapHeartRateSource(devices),
      canStartRemotely: false,
      canStopRemotely: capabilities?.CanStopRemotely == true,
      minimumStartSpeedKph: null,
      canSetSpeedRemotely: capabilities?.CanSetSpeedRemotely == true,
      canSetInclineRemotely: capabilities?.CanSetInclineRemotely == true,
      canPauseRemotely: capabilities?.CanPauseRemotely == true,
      capabilities?.SpeedRange,
      capabilities?.InclineRange,
      controllerSettings,
      checkpoint.DesiredHeartRateAutomationMode,
      checkpoint.ConnectionGeneration,
      devices.SelectedHeartRateEnrollmentId,
      devices.HeartRateSelectionGeneration)
    {
      StartedAt = checkpoint.StartedAtUtc,
      Elapsed = checkpoint.Progression.LastElapsed,
      DistanceKilometers = checkpoint.DistanceKilometers,
      MeasuredSpeedKph = checkpoint.MeasuredSpeedKph,
      MeasuredInclinePercent = checkpoint.MeasuredInclinePercent,
      SpeedOverrideKph = checkpoint.SpeedOverrideKph,
      InclineOverridePercent = checkpoint.InclineOverridePercent,
      CommandsSuspended = true,
      CommandsSuspendedReason = "The gateway restarted; confirm fresh treadmill movement before resuming planned controls.",
      ConnectionPhase = SessionConnectionPhase.Reconnecting,
      RecoveryState = SessionRecoveryState.RestartTracking,
      RecoveredAfterRestart = true,
      RestartRecoveryDeadlineUtc = timeProvider.GetUtcNow().AddSeconds(30),
      NextSequence = stored.Samples.Count == 0
        ? 0
        : checked(stored.Samples.Max(static sample => sample.Sequence) + 1),
      LastSampleCapturedAt = normalizedStoredSamples.LastOrDefault()?.CapturedAt,
      EstimatedKilocalories = SessionCalorieCalculator.Calculate(
        normalizedStoredSamples,
        configuration.Profile.WeightKilograms),
    };
    active.DesiredHeartRateAutomationMode = checkpoint.DesiredHeartRateAutomationMode;
    active.HeartRateAutomationMode = active.RequiresHeartRate
      ? HeartRateAutomationMode.SuspendedSafety
      : HeartRateAutomationMode.Disabled;
    AddWarningOnce(active, "Gateway restarted; tracking recovery never issues Start and planned controls remain paused.");
    return active;
  }

  private async Task<(VersionedUserProfile Profile, StoredWorkoutRevision Revision, WorkoutDefinition Workout)> LoadPlanAsync(
    Guid profileId,
    Guid workoutRevisionId,
    CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    VersionedUserProfile profile = await scope.ServiceProvider.GetRequiredService<IProfileStore>()
      .FindAsync(profileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Profile {profileId} was not found.");
    StoredWorkoutRevision revision = await scope.ServiceProvider.GetRequiredService<IWorkoutStore>()
      .FindRevisionAsync(workoutRevisionId, cancellationToken)
      ?? throw new KeyNotFoundException($"Workout revision {workoutRevisionId} was not found.");
    await using var stream = new MemoryStream(Encoding.UTF8.GetBytes(revision.DefinitionJson));
    WorkoutImportResult parsed = await new NativeWorkoutJsonImporter()
      .ImportAsync(stream, "stored-workout.json", cancellationToken);
    return (profile, revision, parsed.Definition);
  }

  private async Task ReleaseDeviceConnectionsAsync(CancellationToken cancellationToken)
  {
    await commandCoordinator.ReleaseConnectionAsync(cancellationToken);
    await deviceCoordinator.ReleaseRunConnectionsAsync(cancellationToken);
  }

  private async Task<TreadmillControlAvailability> LoadControlAvailabilityAsync(
    CancellationToken cancellationToken)
  {
    using IServiceScope scope = scopeFactory.CreateScope();
    VersionedDeviceEnrollment? stored = await scope.ServiceProvider
      .GetRequiredService<IDeviceEnrollmentStore>()
      .FindActiveAsync(DeviceRole.Treadmill, cancellationToken);
    DeviceEnrollment? enrollment = stored?.Enrollment;
    if (enrollment is null ||
        enrollment.TelemetryMode != TreadmillTelemetryMode.Ftms ||
        enrollment.Evidence != TreadmillCapabilityEvidence.HardwareVerified ||
        string.IsNullOrWhiteSpace(enrollment.ModelNumber) ||
        string.IsNullOrWhiteSpace(enrollment.FirmwareRevision))
    {
      return TreadmillControlAvailability.Unavailable;
    }

    TreadmillCapabilities capabilities = enrollment.Capabilities!;
    bool canStart = capabilities.CanStartRemotely && capabilities.SpeedRange is not null;
    return new TreadmillControlAvailability(
      canStart,
      capabilities.CanStopRemotely,
      canStart ? (double)capabilities.SpeedRange!.Minimum : null,
      capabilities.CanSetSpeedRemotely && capabilities.SpeedRange is not null,
      capabilities.CanSetInclineRemotely && capabilities.InclineRange is not null,
      capabilities.CanPauseRemotely,
      capabilities.SpeedRange,
      capabilities.InclineRange,
      enrollment.Id,
      enrollment.IdentityFingerprint);
  }

  private DeviceTelemetrySnapshot? ApplyHardwareTelemetry(
    ActiveRun active,
    DateTimeOffset now,
    LiveEffectBatch effects)
  {
    if (!active.HardwareMode) return null;
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId);
    if (devices.HeartRateSelectionGeneration != active.HeartRateSelectionGeneration)
    {
      bool hadSelection = active.HeartRateSelectionGeneration > 0;
      active.HeartRateSelectionGeneration = devices.HeartRateSelectionGeneration;
      active.HeartRateEnrollmentId = devices.SelectedHeartRateEnrollmentId;
      active.HeartRateSource = MapHeartRateSource(devices);
      active.HeartRateController.Reset();
      if (hadSelection && active.RequiresHeartRate)
      {
        SuspendAutomation(active, "The heart-rate source changed; explicitly re-enable automation after confirming the new sensor.");
        AddWarningOnce(active, "Heart-rate source changed; automatic speed control is paused.");
        SessionWarningEvent warning = new(
          "heart-rate-source-changed",
          $"Heart-rate source changed to {devices.HeartRate.DisplayName ?? "no fresh sensor"}.",
          now);
        LiveEffectMetadata metadata = CaptureEffectMetadata(active);
        effects.Add(metadata, async token =>
        {
          using IServiceScope sourceScope = scopeFactory.CreateScope();
          await sourceScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
            metadata.SessionId,
            warning,
            token);
        });
      }
    }
    TimeSpan? treadmillSpeedAge = devices.TreadmillSpeedAge;
    TimeSpan? treadmillInclineAge = devices.TreadmillInclineAge;
    bool generationChanged = devices.Treadmill.ConnectionGeneration != active.ConnectionGeneration;
    if (devices.TreadmillTelemetry is { } treadmill && treadmillSpeedAge <= FreshTelemetryLimit)
    {
      if (generationChanged && active.TelemetryGapStartedAtUtc is null)
        BeginTelemetryGap(active, now, "The treadmill connection generation changed.", effects);

      SessionState previous = active.Machine.State;
      active.MeasuredSpeedKph = treadmill.SpeedKph;
      if (treadmillInclineAge <= FreshTelemetryLimit)
        active.MeasuredInclinePercent = treadmill.InclinePercent;
      active.IsMoving = treadmill.SpeedKph > SessionStateMachine.PhysicalStartThresholdKph;
      active.TelemetryAge = treadmillSpeedAge ?? TimeSpan.Zero;
      active.Machine.ObserveTelemetry(treadmill.SpeedKph);
      MarkRunningIfTransitioned(active, previous, now, effects);
      active.Warnings.Remove("Treadmill telemetry is stale; session automation is suspended.");

      if (active.TelemetryGapStartedAtUtc is not null)
      {
        if (active.ReconnectCandidateGeneration == devices.Treadmill.ConnectionGeneration)
          active.ReconnectStableSamples++;
        else
        {
          active.ReconnectCandidateGeneration = devices.Treadmill.ConnectionGeneration;
          active.ReconnectStableSamples = 1;
        }

        active.ConnectionPhase = SessionConnectionPhase.Reconnecting;
        active.RecoveryState = SessionRecoveryState.Reconciling;
        if (active.ReconnectStableSamples >= 2)
          CompleteTelemetryReconnect(active, devices, treadmill, now, effects);
      }
    }
    else
    {
      BeginTelemetryGap(active, now, devices.Treadmill.Fault ?? "Treadmill telemetry became stale.", effects);
      active.IsMoving = false;
      active.TelemetryAge = treadmillSpeedAge ?? TimeSpan.MaxValue;
      AddWarningOnce(active, "Treadmill telemetry is stale; session automation is suspended.");
      if (active.Machine.State == SessionState.Running && !active.CommandsSuspended)
        SuspendAutomation(active, "Treadmill telemetry is stale; session automation is suspended.");
    }

    if (!active.RequiresHeartRate)
    {
      active.HeartRateBpm = devices.HeartRateBpm;
      active.HeartRateAge = devices.HeartRateAge;
    }
    else if (devices.HeartRateBpm is { } heartRate && devices.HeartRateAge <= FreshTelemetryLimit)
    {
      active.HeartRateBpm = heartRate;
      active.HeartRateAge = devices.HeartRateAge;
      active.Warnings.Remove("Heart-rate telemetry is stale; heart-rate automation is suspended.");
    }
    else
    {
      active.HeartRateBpm = null;
      active.HeartRateAge = devices.HeartRateAge;
      AddWarningOnce(active, "Heart-rate telemetry is stale; heart-rate automation is suspended.");
      if (active.Machine.State == SessionState.Running && !active.CommandsSuspended)
        SuspendAutomation(active, "Heart-rate telemetry is stale; heart-rate automation is suspended.");
    }
    return devices;
  }

  internal static SessionHeartRateDiagnostic CaptureHeartRateDiagnostic(
    Guid profileId,
    DeviceTelemetrySnapshot devices,
    SessionSample sample,
    TimeSpan freshnessLimit)
  {
    Guid? enrollmentId = devices.SelectedHeartRateEnrollmentId;
    HeartRateSourceSnapshot? source = enrollmentId is { } selectedId
      ? devices.HeartRateSources?.FirstOrDefault(candidate => candidate.EnrollmentId == selectedId)
      : devices.HeartRateSources?.FirstOrDefault(candidate =>
        candidate.ConnectionGeneration == devices.HeartRate.ConnectionGeneration &&
        string.Equals(candidate.DisplayName, devices.HeartRate.DisplayName, StringComparison.Ordinal));
    double? ageSeconds = source?.ObservedAt is { } observedAt && observedAt <= devices.CapturedAt
      ? (devices.CapturedAt - observedAt).TotalSeconds
      : null;
    string reason;
    if (enrollmentId is not null && source is null)
      reason = "SelectedSourceMissing";
    else if (source is null)
      reason = "NoSelectedSource";
    else if (source.State != DeviceConnectionState.Ready)
      reason = "SourceNotReady";
    else if (source.Quality != HeartRateSignalQuality.Valid)
      reason = "QualityNotValid";
    else if (source.ObservedAt is null)
      reason = "MissingObservation";
    else if (source.ObservedAt > devices.CapturedAt)
      reason = "ObservationInFuture";
    else if (devices.CapturedAt - source.ObservedAt.Value > freshnessLimit)
      reason = "ObservationStale";
    else if (sample.HeartRateBpm is null)
      reason = "SampleMissingHeartRate";
    else
      reason = "Available";

    return new SessionHeartRateDiagnostic(
      profileId,
      source?.EnrollmentId ?? enrollmentId,
      source?.ConnectionGeneration ?? devices.HeartRate.ConnectionGeneration,
      ageSeconds,
      (source?.Quality ?? devices.SelectedHeartRateQuality).ToString(),
      sample.HeartRateBpm is not null,
      reason);
  }

  private void BeginTelemetryGap(
    ActiveRun active,
    DateTimeOffset now,
    string reason,
    LiveEffectBatch effects)
  {
    if (active.TelemetryGapStartedAtUtc is not null) return;
    active.TelemetryGapStartedAtUtc = now;
    active.PreGapMeasuredSpeedKph = active.MeasuredSpeedKph;
    active.PreGapMeasuredInclinePercent = active.MeasuredInclinePercent;
    active.ReconnectStableSamples = 0;
    active.ReconnectCandidateGeneration = 0;
    active.ConnectionPhase = SessionConnectionPhase.Reconnecting;
    active.RecoveryState = active.RecoveredAfterRestart
      ? SessionRecoveryState.RestartTracking
      : SessionRecoveryState.TelemetryGap;
    active.CanResumePlannedControls = false;
    LiveEffectMetadata metadata = CaptureEffectMetadata(active);
    DeviceDisconnectedEvent disconnected = new(SessionDeviceRole.Treadmill, reason, now);
    effects.Add(metadata, async token =>
    {
      using IServiceScope scope = scopeFactory.CreateScope();
      await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
        metadata.SessionId,
        disconnected,
        token);
    });
  }

  private void CompleteTelemetryReconnect(
    ActiveRun active,
    DeviceTelemetrySnapshot devices,
    TreadmillTelemetry treadmill,
    DateTimeOffset now,
    LiveEffectBatch effects)
  {
    RecoveryReconciliationDecision decision = RecoveryReconciliationPolicy.Evaluate(new(
      active.Machine.State,
      SameEnrolledTreadmill: true,
      FreshStableTelemetry: active.ReconnectStableSamples >= 2,
      active.RecoveredAfterRestart,
      treadmill.SpeedKph,
      treadmill.InclinePercent,
      active.PreGapMeasuredSpeedKph,
      active.PreGapMeasuredInclinePercent,
      active.SpeedRange is { } speedRange ? (double)speedRange.Increment : 0.1,
      active.InclineRange is { } inclineRange ? (double)inclineRange.Increment : 0.5,
      active.LastCommandResult?.Disposition));

    active.ConnectionGeneration = devices.Treadmill.ConnectionGeneration;
    active.TelemetryGapStartedAtUtc = null;
    active.ReconnectStableSamples = 0;
    active.ReconnectCandidateGeneration = 0;
    active.LastReconciledAtUtc = now;
    LiveEffectMetadata metadata = CaptureEffectMetadata(active);
    DeviceReconnectedEvent reconnected = new(SessionDeviceRole.Treadmill, now);
    effects.Add(metadata, async token =>
    {
      using IServiceScope scope = scopeFactory.CreateScope();
      await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
        metadata.SessionId,
        reconnected,
        token);
    });

    if (decision.Action == RecoveryReconciliationAction.ResumeAutomatically)
    {
      active.CommandsSuspended = false;
      active.CommandsSuspendedReason = null;
      active.CanResumePlannedControls = false;
      active.ConnectionPhase = SessionConnectionPhase.Recovered;
      active.RecoveryState = SessionRecoveryState.Recovered;
      if (active.RequiresHeartRate)
        active.HeartRateAutomationMode = active.DesiredHeartRateAutomationMode;
      active.HeartRateAutomationReason = "Treadmill telemetry recovered; current planned targets will be reconciled with fresh commands.";
      active.Warnings.Remove("Treadmill telemetry is stale; session automation is suspended.");
      return;
    }

    string reason = decision.Reason;
    SuspendAutomation(active, reason);
    active.ConnectionPhase = SessionConnectionPhase.NeedsAttention;
    active.RecoveryState = active.RecoveredAfterRestart
      ? SessionRecoveryState.RestartTracking
      : SessionRecoveryState.AwaitingResume;
    active.CanResumePlannedControls = decision.Action == RecoveryReconciliationAction.RequireExplicitResume;
  }

  private static SessionRecoveryCheckpoint CreateRecoveryCheckpoint(ActiveRun active, DateTimeOffset now) => new(
    active.Definition.SessionId,
    now,
    active.Machine.State,
    active.Machine.Version,
    active.StartedAt ?? now,
    active.Progression.Capture(),
    active.DistanceKilometers,
    active.MeasuredSpeedKph,
    active.MeasuredInclinePercent,
    active.SpeedOverrideKph,
    active.InclineOverridePercent,
    active.DesiredHeartRateAutomationMode,
    active.ConnectionGeneration);

  private void MarkRunningIfTransitioned(
    ActiveRun active,
    SessionState previous,
    DateTimeOffset now,
    LiveEffectBatch effects)
  {
    if (previous == SessionState.Running || active.Machine.State != SessionState.Running) return;
    if (previous == SessionState.ArmedWaitingForPhysicalStart)
      active.StartedAt = now;
    active.LastTickAt = now;
    if (previous == SessionState.ArmedWaitingForPhysicalStart)
    {
      AddWarningOnce(active, "Physical movement detected");
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      SessionWarningEvent warning = new(
        "physical-movement-detected",
        "Physical movement detected",
        now);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        await store.MarkRunningAsync(metadata.SessionId, now, token);
        await store.AppendEventAsync(metadata.SessionId, warning, token);
      });
    }
    else
    {
      LiveEffectMetadata metadata = CaptureEffectMetadata(active);
      SessionResumedEvent resumed = new(now);
      effects.Add(metadata, async token =>
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
          metadata.SessionId,
          resumed,
          token);
      });
    }
  }

  private static void AddWarningOnce(ActiveRun active, string warning)
  {
    if (!active.Warnings.Contains(warning, StringComparer.Ordinal)) active.Warnings.Add(warning);
  }

  private void UpdateMotion(ActiveRun active, DateTimeOffset now)
  {
    if (active.Machine.State != SessionState.Running || active.StartedAt is null)
    {
      active.LastTickAt = now;
      return;
    }

    TimeSpan delta = now - active.LastTickAt;
    if (delta < TimeSpan.Zero)
    {
      delta = TimeSpan.Zero;
    }

    active.Elapsed += delta;
    if (active.IsMoving)
    {
      active.DistanceKilometers += active.MeasuredSpeedKph * delta.TotalHours;
      active.EstimatedKilocalories += SessionCalorieCalculator.CalculateInterval(
        active.WeightKilograms,
        active.MeasuredSpeedKph,
        active.MeasuredInclinePercent,
        delta);
    }

    active.LastTickAt = now;
  }

  private void PublishSnapshot(
    ActiveRun active,
    DateTimeOffset now,
    SessionControlAccess access,
    DateTimeOffset? leaseExpiresAt)
  {
    TimeSpan? pace = active.MeasuredSpeedKph > 0
      ? TimeSpan.FromHours(1 / active.MeasuredSpeedKph)
      : null;
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId);
    TreadmillTelemetry? treadmillTelemetry = devices.TreadmillTelemetry;
    var live = new LiveSnapshot(
      now,
      active.HardwareMode ? devices.Treadmill.State : DeviceConnectionState.Ready,
      active.HardwareMode ? devices.HeartRate.State : DeviceConnectionState.Ready,
      active.Machine.State,
      active.MeasuredSpeedKph,
      active.MeasuredInclinePercent,
      active.HeartRateBpm,
      active.Elapsed,
      active.DistanceKilometers,
      pace,
      active.TelemetryAge,
      active.HardwareMode ? devices.HeartRate.DisplayName : "Simulated HR",
      active.HardwareMode ? devices.HeartRateAge : active.HeartRateAge,
      active.HardwareMode ? devices.SelectedHeartRateEnrollmentId : null,
      active.HardwareMode ? devices.SelectedHeartRateDeviceKind : HeartRateDeviceKind.Sensor,
      active.HardwareMode ? devices.SelectedHeartRateDeviceFamily : HeartRateDeviceFamily.Other,
      active.HardwareMode ? devices.HeartRateSelectionGeneration : 1,
      active.HardwareMode ? devices.HeartRateSelectionReason : "Development simulator source.",
      active.HardwareMode ? devices.Treadmill.DisplayName : "Simulator",
      active.HardwareMode ? devices.Treadmill.ProtocolId : "simulator",
      active.HardwareMode ? devices.Treadmill.TelemetryMode : "Simulated",
      active.HardwareMode ? devices.Treadmill.ModelNumber : null,
      active.HardwareMode ? devices.Treadmill.FirmwareRevision : null,
      active.HardwareMode ? devices.Treadmill.Evidence : TreadmillCapabilityEvidence.ProtocolReported,
      active.HardwareMode ? devices.Treadmill.Capabilities : new TreadmillCapabilities(
        CanSetSpeedRemotely: true,
        CanSetInclineRemotely: true,
        CanPauseRemotely: true,
        CanStopRemotely: true,
        CanStartRemotely: true,
        SpeedRange: active.SpeedRange,
        InclineRange: active.InclineRange),
      active.HardwareMode ? devices.Treadmill.ConnectionGeneration : 1,
      active.HardwareMode ? devices.SelectedHeartRateBatteryPercent : null,
      active.HardwareMode ? devices.SelectedHeartRateBatteryObservedAt : null,
      active.EstimatedKilocalories,
      active.HardwareMode ? devices.SelectedHeartRateQuality : HeartRateSignalQuality.Valid,
      active.HardwareMode ? devices.SelectedHeartRateContactState : HeartRateContactState.NotSupported,
      active.HardwareMode ? treadmillTelemetry?.SpeedObservedAt : now,
      active.HardwareMode ? treadmillTelemetry?.InclineObservedAt : now,
      active.HardwareMode ? devices.TreadmillSpeedAge : TimeSpan.Zero,
      active.HardwareMode ? devices.TreadmillInclineAge : TimeSpan.Zero,
      active.HardwareMode ? devices.HeartRateObservedAt : active.HeartRateObservedAt);
    Volatile.Write(ref _current, live);
    active.Snapshot = new ActiveSessionSnapshot(
      active.Definition.SessionId,
      active.Definition.UserProfileId,
      active.Definition.UserProfileName,
      active.Definition.WorkoutRevisionId,
      active.Definition.WorkoutTitle,
      live,
      active.Machine.Version,
      MapStep(active.Progression.CurrentStep, active.Progression.CurrentStepIndex, active.Progression),
      MapStep(active.Progression.NextStep, active.Progression.CurrentStepIndex + 1, active.Progression),
      active.Progression.RemainingDuration,
      active.Progression.PlannedSpeedKph,
      active.RequestedSpeedKph,
      active.Progression.PlannedInclinePercent,
      active.RequestedInclinePercent,
      active.Progression.HeartRateTarget,
      active.HeartRateSource,
      active.HeartRateAge,
      access,
      leaseExpiresAt,
      active.Warnings,
      active.LastCommandResult,
      active.CanStartRemotely,
      active.CanStopRemotely,
      active.MinimumStartSpeedKph,
      active.CanSetSpeedRemotely,
      active.CanSetInclineRemotely,
      active.CanPauseRemotely,
      active.SpeedRange,
      active.InclineRange,
      active.HeartRateAutomationMode,
      active.HeartRateAutomationReason,
      active.WorkoutPlan,
      active.ConnectionPhase,
      _serviceInstanceId,
      active.RecoveryState,
      active.CommandsSuspendedReason,
      active.TelemetryGapStartedAtUtc,
      active.CanResumePlannedControls,
      active.LastReconciledAtUtc,
      active.Progression.ElapsedSinceRestart);
  }

  private void RefreshFrozenTerminalSnapshotMetadata(
    ActiveRun active,
    SessionControlAccess access,
    DateTimeOffset? leaseExpiresAt)
  {
    ActiveSessionSnapshot frozen = active.Snapshot;
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId);
    LiveSnapshot live = frozen.Live with
    {
      TreadmillConnectionState = active.HardwareMode
        ? devices.Treadmill.State
        : DeviceConnectionState.Ready,
      HeartRateConnectionState = active.HardwareMode
        ? devices.HeartRate.State
        : DeviceConnectionState.Ready,
    };
    Volatile.Write(ref _current, live);
    active.Snapshot = new ActiveSessionSnapshot(
      frozen.SessionId,
      frozen.UserProfileId,
      frozen.UserProfileName,
      frozen.WorkoutRevisionId,
      frozen.WorkoutTitle,
      live,
      frozen.Version,
      frozen.CurrentStep,
      frozen.NextStep,
      frozen.Remaining,
      frozen.PlannedSpeedKph,
      frozen.RequestedSpeedKph,
      frozen.PlannedInclinePercent,
      frozen.RequestedInclinePercent,
      frozen.HeartRateTarget,
      frozen.HeartRateSource,
      frozen.HeartRateAge,
      access,
      leaseExpiresAt,
      frozen.Warnings,
      frozen.LastCommandResult,
      frozen.CanStartRemotely,
      frozen.CanStopRemotely,
      frozen.MinimumStartSpeedKph,
      frozen.CanSetSpeedRemotely,
      frozen.CanSetInclineRemotely,
      frozen.CanPauseRemotely,
      frozen.SpeedRange,
      frozen.InclineRange,
      frozen.HeartRateAutomationMode,
      frozen.HeartRateAutomationReason,
      frozen.WorkoutPlan,
      frozen.ConnectionPhase,
      frozen.ServiceInstanceId,
      frozen.RecoveryState,
      frozen.CommandsSuspendedReason,
      frozen.TelemetryGapStartedAtUtc,
      frozen.CanResumePlannedControls,
      frozen.LastReconciledAtUtc,
      frozen.WorkoutElapsed);
  }

  private static IReadOnlyList<WorkoutPlanPoint> BuildWorkoutPlan(WorkoutDefinition definition)
  {
    var steps = new List<WorkoutStep>();
    ExpandPlanSteps(definition.Blocks, steps);
    if (steps.Count > 128 || steps.Any(static step => step.Goal is not TimeGoal))
      return Array.Empty<WorkoutPlanPoint>();

    var points = new List<WorkoutPlanPoint>(steps.Count * 2);
    TimeSpan elapsed = TimeSpan.Zero;
    foreach (WorkoutStep step in steps)
    {
      (double? speedStart, double? speedEnd) = PlanSpeedBounds(step.Speed);
      (double? inclineStart, double? inclineEnd) = PlanInclineBounds(step.Incline);
      points.Add(new WorkoutPlanPoint(elapsed, speedStart, inclineStart));
      elapsed += ((TimeGoal)step.Goal).Duration;
      points.Add(new WorkoutPlanPoint(elapsed, speedEnd, inclineEnd));
    }

    return points.AsReadOnly();
  }

  private static void ExpandPlanSteps(IReadOnlyList<WorkoutBlock> blocks, List<WorkoutStep> target)
  {
    foreach (WorkoutBlock block in blocks)
    {
      if (block is WorkoutStep step) target.Add(step);
      else if (block is WorkoutRepeat repeat)
      {
        for (var repetition = 0; repetition < repeat.Repetitions; repetition++)
          ExpandPlanSteps(repeat.Blocks, target);
      }
    }
  }

  private static (double? Start, double? End) PlanSpeedBounds(SpeedDirective speed) => speed switch
  {
    FixedSpeed fixedSpeed => (fixedSpeed.KilometersPerHour, fixedSpeed.KilometersPerHour),
    SpeedRamp ramp => (ramp.StartKilometersPerHour, ramp.EndKilometersPerHour),
    HeartRateSpeed heartRate => (heartRate.InitialKilometersPerHour, heartRate.InitialKilometersPerHour),
    HeartRateZoneSpeed zone => (zone.InitialKilometersPerHour, zone.InitialKilometersPerHour),
    OpenSpeed => (null, null),
    _ => (null, null),
  };

  private static (double? Start, double? End) PlanInclineBounds(InclineDirective incline) => incline switch
  {
    FixedIncline fixedIncline => (fixedIncline.Percent, fixedIncline.Percent),
    InclineRamp ramp => (ramp.StartPercent, ramp.EndPercent),
    _ => (null, null),
  };

  private static ActiveWorkoutStep? MapStep(
    WorkoutStep? step,
    int index,
    WorkoutProgression progression) => step is null
      ? null
      : new ActiveWorkoutStep(
        index,
        progression.TotalStepCount,
        step.Cue,
        step.Notes,
        index == progression.CurrentStepIndex ? progression.ProgressFraction : 0,
        PlannedSpeed(step),
        PlannedIncline(step));

  private static double? PlannedSpeed(WorkoutStep step) => step.Speed switch
  {
    FixedSpeed fixedSpeed => fixedSpeed.KilometersPerHour,
    SpeedRamp ramp => ramp.StartKilometersPerHour,
    HeartRateSpeed heartRate => heartRate.InitialKilometersPerHour,
    HeartRateZoneSpeed zone => zone.InitialKilometersPerHour,
    OpenSpeed => null,
    _ => null,
  };

  private static double? PlannedIncline(WorkoutStep step) => step.Incline switch
  {
    FixedIncline fixedIncline => fixedIncline.Percent,
    InclineRamp ramp => ramp.StartPercent,
    _ => null,
  };

  private static SessionSample CreateSample(ActiveRun active, DateTimeOffset now)
  {
    active.LastSampleCapturedAt = now;
    return new SessionSample(
      active.Definition.SessionId,
      active.NextSequence++,
      now,
      active.Elapsed,
      active.Progression.PlannedSpeedKph,
      active.RequestedSpeedKph,
      active.MeasuredSpeedKph,
      active.Progression.PlannedInclinePercent,
      active.RequestedInclinePercent,
      active.MeasuredInclinePercent,
      active.HeartRateBpm,
      active.DistanceKilometers,
      active.EstimatedKilocalories,
      active.TelemetryAge,
      active.Definition.MetricAlgorithmVersion);
  }

  private static DateTimeOffset GetLogicalSessionTimestamp(ActiveRun active, DateTimeOffset observedAt)
  {
    DateTimeOffset logicalAt = active.StartedAt is { } startedAt
      ? startedAt + active.Elapsed
      : active.Definition.ArmedAt;
    if (observedAt > logicalAt) logicalAt = observedAt;
    if (active.LastSampleCapturedAt is { } previous && logicalAt <= previous)
      logicalAt = previous.AddTicks(1);
    return logicalAt;
  }

  private static DateTimeOffset GetTerminalTimestamp(ActiveRun active, DateTimeOffset observedAt)
  {
    DateTimeOffset terminalAt = GetLogicalSessionTimestamp(active, observedAt);
    DateTimeOffset startedAt = active.StartedAt ?? active.Definition.ArmedAt;
    DateTimeOffset minimumEnd = startedAt + (active.StartedAt is null ? TimeSpan.Zero : active.Elapsed);
    return terminalAt < minimumEnd ? minimumEnd : terminalAt;
  }

  private static SessionSummary CreateSummary(
    ActiveRun active,
    SessionState state,
    DateTimeOffset endedAt)
  {
    DateTimeOffset startedAt = active.StartedAt ?? active.Definition.ArmedAt;
    TimeSpan duration = active.StartedAt is null ? TimeSpan.Zero : active.Elapsed;
    return new SessionSummary(
      active.Definition.SessionId,
      active.Definition.UserProfileId,
      active.Definition.UserProfileName,
      active.Definition.WorkoutRevisionId,
      active.Definition.WorkoutTitle,
      state,
      startedAt,
      endedAt,
      duration,
      active.DistanceKilometers,
      active.EstimatedKilocalories,
      active.HeartRateBpm,
      active.HeartRateBpm,
      duration > TimeSpan.Zero ? active.DistanceKilometers / duration.TotalHours : 0,
      active.MeasuredInclinePercent);
  }

  private ActiveRun RequireActive()
  {
    ThrowIfShutdownStarted();
    return _active ?? throw new InvalidOperationException("No simulator workout is armed.");
  }

  private void ThrowIfShutdownStarted()
  {
    if (_shutdownStarted)
      throw new InvalidOperationException("The live-session coordinator is stopping.");
  }

  private static TimeSpan NonNegative(TimeSpan value) =>
    value < TimeSpan.Zero ? TimeSpan.Zero : value;

  private SessionControlAccess AccessForCurrentLease() =>
    leaseCoordinator.Current is null ? SessionControlAccess.Observer : SessionControlAccess.Controller;

  private static bool ContainsHeartRateTarget(IEnumerable<WorkoutBlock> blocks) => blocks.Any(block => block switch
  {
    WorkoutStep { Speed: HeartRateSpeed or HeartRateZoneSpeed } => true,
    WorkoutRepeat repeat => ContainsHeartRateTarget(repeat.Blocks),
    _ => false,
  });

  private static HeartRateSource MapHeartRateSource(DeviceTelemetrySnapshot devices) =>
    devices.SelectedHeartRateDeviceFamily switch
    {
      HeartRateDeviceFamily.Polar => HeartRateSource.PolarH10,
      HeartRateDeviceFamily.Garmin => HeartRateSource.GarminBleBroadcast,
      _ when devices.SelectedHeartRateEnrollmentId is not null => HeartRateSource.BluetoothHeartRate,
      _ => HeartRateSource.None,
    };

  private static LiveSnapshot SimulatorIdleSnapshot(DateTimeOffset now) => new(
    now,
    DeviceConnectionState.Ready,
    DeviceConnectionState.Ready,
    SessionState.Idle,
    0,
    0,
    132,
    TimeSpan.Zero,
    0,
    null,
    TimeSpan.Zero,
    "Simulated HR",
    TimeSpan.Zero,
    null,
    HeartRateDeviceKind.Sensor,
    HeartRateDeviceFamily.Other,
    1,
    "Development simulator source.",
    HeartRateQuality: HeartRateSignalQuality.Valid,
    HeartRateContactState: HeartRateContactState.NotSupported,
    HeartRateObservedAt: now);

  private static LiveSnapshot DisconnectedIdleSnapshot(DateTimeOffset now) => new(
    now,
    DeviceConnectionState.Disconnected,
    DeviceConnectionState.Disconnected,
    SessionState.Idle,
    0,
    0,
    null,
    TimeSpan.Zero,
    0,
    null,
    TimeSpan.MaxValue);

  private LiveSnapshot CreateIdleSnapshot(DateTimeOffset now)
  {
    DeviceTelemetrySnapshot devices = deviceCoordinator.Current;
    bool hardware = !SimulatorAvailable;
    TreadmillTelemetry? treadmill = devices.TreadmillTelemetry;
    return hardware
      ? new LiveSnapshot(
        now,
        devices.Treadmill.State,
        devices.HeartRate.State,
        SessionState.Idle,
        treadmill?.SpeedKph ?? 0,
        treadmill?.InclinePercent ?? 0,
        devices.HeartRateBpm,
        TimeSpan.Zero,
        0,
        treadmill is { SpeedKph: > 0 } ? TimeSpan.FromHours(1 / treadmill.SpeedKph) : null,
        devices.TreadmillSpeedAge ?? TimeSpan.MaxValue,
        devices.HeartRate.DisplayName,
        devices.HeartRateAge,
        devices.SelectedHeartRateEnrollmentId,
        devices.SelectedHeartRateDeviceKind,
        devices.SelectedHeartRateDeviceFamily,
        devices.HeartRateSelectionGeneration,
        devices.HeartRateSelectionReason,
        devices.Treadmill.DisplayName,
        devices.Treadmill.ProtocolId,
        devices.Treadmill.TelemetryMode,
        devices.Treadmill.ModelNumber,
        devices.Treadmill.FirmwareRevision,
        devices.Treadmill.Evidence,
        devices.Treadmill.Capabilities,
        devices.Treadmill.ConnectionGeneration,
        devices.SelectedHeartRateBatteryPercent,
        devices.SelectedHeartRateBatteryObservedAt,
        HeartRateQuality: devices.SelectedHeartRateQuality,
        HeartRateContactState: devices.SelectedHeartRateContactState,
        TreadmillSpeedObservedAt: treadmill?.SpeedObservedAt,
        TreadmillInclineObservedAt: treadmill?.InclineObservedAt,
        TreadmillSpeedTelemetryAge: devices.TreadmillSpeedAge,
        TreadmillInclineTelemetryAge: devices.TreadmillInclineAge,
        HeartRateObservedAt: devices.HeartRateObservedAt)
      : SimulatorIdleSnapshot(now);
  }

  private sealed class ActiveRun(
    NewWorkoutSession definition,
    WorkoutDefinition workout,
    SessionStateMachine machine,
    WorkoutProgression progression,
    double maximumSpeedKph,
    double weightKilograms,
    DateTimeOffset createdAt,
    bool hardwareMode,
    bool requiresHeartRate,
    HeartRateSource heartRateSource,
    bool canStartRemotely,
    bool canStopRemotely,
    double? minimumStartSpeedKph,
    bool canSetSpeedRemotely,
    bool canSetInclineRemotely,
    bool canPauseRemotely,
    TreadmillOperatingRange? speedRange,
    TreadmillOperatingRange? inclineRange,
    HeartRateControllerSettings heartRateControllerSettings,
    HeartRateAutomationMode heartRateAutomationMode,
    long connectionGeneration,
    Guid? heartRateEnrollmentId,
    long heartRateSelectionGeneration)
  {
    public NewWorkoutSession Definition { get; } = definition;
    public WorkoutDefinition Workout { get; } = workout;
    public SessionStateMachine Machine { get; } = machine;
    public WorkoutProgression Progression { get; } = progression;
    public IReadOnlyList<WorkoutPlanPoint> WorkoutPlan { get; } = BuildWorkoutPlan(workout);
    public double MaximumSpeedKph { get; } = maximumSpeedKph;
    public double WeightKilograms { get; } = weightKilograms;
    public bool HardwareMode { get; } = hardwareMode;
    public bool RequiresHeartRate { get; } = requiresHeartRate;
    public HeartRateSource HeartRateSource { get; set; } = heartRateSource;
    public Guid? HeartRateEnrollmentId { get; set; } = heartRateEnrollmentId;
    public long HeartRateSelectionGeneration { get; set; } = heartRateSelectionGeneration;
    public bool CanStartRemotely { get; } = canStartRemotely;
    public bool CanStopRemotely { get; } = canStopRemotely;
    public double? MinimumStartSpeedKph { get; } = minimumStartSpeedKph;
    public bool CanSetSpeedRemotely { get; } = canSetSpeedRemotely;
    public bool CanSetInclineRemotely { get; } = canSetInclineRemotely;
    public bool CanPauseRemotely { get; } = canPauseRemotely;
    public TreadmillOperatingRange? SpeedRange { get; } = speedRange;
    public TreadmillOperatingRange? InclineRange { get; } = inclineRange;
    public HeartRateSpeedController HeartRateController { get; } = new(heartRateControllerSettings);
    public HeartRateAutomationMode HeartRateAutomationMode { get; set; } = heartRateAutomationMode;
    public HeartRateAutomationMode DesiredHeartRateAutomationMode { get; set; } = heartRateAutomationMode;
    public string? HeartRateAutomationReason { get; set; } = heartRateAutomationMode == HeartRateAutomationMode.Shadow
      ? "Shadow mode records decisions without sending speed commands."
      : null;
    public long ConnectionGeneration { get; set; } = connectionGeneration;
    public Guid AutomationAuthorityId { get; } = Guid.NewGuid();
    public string AutomationAuthorityHolder { get; } = $"gateway-session:{definition.SessionId:N}";
    public bool CommandsSuspended { get; set; }
    public string? CommandsSuspendedReason { get; set; }
    public SessionConnectionPhase ConnectionPhase { get; set; } = SessionConnectionPhase.Ready;
    public SessionRecoveryState RecoveryState { get; set; } = SessionRecoveryState.None;
    public DateTimeOffset? TelemetryGapStartedAtUtc { get; set; }
    public DateTimeOffset? LastReconciledAtUtc { get; set; }
    public bool CanResumePlannedControls { get; set; }
    public double PreGapMeasuredSpeedKph { get; set; }
    public double PreGapMeasuredInclinePercent { get; set; }
    public long ReconnectCandidateGeneration { get; set; }
    public int ReconnectStableSamples { get; set; }
    public bool RecoveredAfterRestart { get; set; }
    public DateTimeOffset? RestartRecoveryDeadlineUtc { get; set; }
    public DateTimeOffset? StartedAt { get; set; }
    public DateTimeOffset? LastSampleCapturedAt { get; set; }
    public DateTimeOffset LastTickAt { get; set; } = createdAt;
    public FixedIntervalCadence SampleCadence { get; } = new(PersistenceInterval, createdAt);
    public TimeSpan Elapsed { get; set; }
    public double DistanceKilometers { get; set; }
    public double EstimatedKilocalories { get; set; }
    public double MeasuredSpeedKph { get; set; }
    public double MeasuredInclinePercent { get; set; }
    public bool IsMoving { get; set; }
    public bool DeviceConnectionsReleased { get; set; }
    public Func<CancellationToken, Task>? TerminalPersistenceEffect { get; set; }
    public bool CompletionStopAttempted { get; set; }
    public ushort? HeartRateBpm { get; set; } = hardwareMode ? null : (ushort)132;
    public TimeSpan? HeartRateAge { get; set; } = hardwareMode ? null : TimeSpan.Zero;
    public DateTimeOffset? HeartRateObservedAt { get; set; } = hardwareMode ? null : createdAt;
    public TimeSpan TelemetryAge { get; set; } = TimeSpan.Zero;
    public long NextSequence { get; set; }
    public List<string> Warnings { get; } = [];
    public HashSet<Guid> ProcessedOperationIds { get; } = [];
    public double? SpeedOverrideKph { get; set; }
    public double? InclineOverridePercent { get; set; }
    public int? AppliedSpeedPlanStepIndex { get; set; }
    public int? AppliedInclinePlanStepIndex { get; set; }
    public double RequestedSpeedKph => SpeedOverrideKph ?? Progression.PlannedSpeedKph ?? MeasuredSpeedKph;
    public double RequestedInclinePercent => InclineOverridePercent ?? Progression.PlannedInclinePercent ?? MeasuredInclinePercent;
    public TreadmillCommandResult? LastCommandResult { get; set; }
    public ActiveSessionSnapshot Snapshot { get; set; } = null!;
  }

  private sealed record TreadmillControlAvailability(
    bool CanStart,
    bool CanStop,
    double? MinimumStartSpeedKph,
    bool CanSetSpeed,
    bool CanSetIncline,
    bool CanPause,
    TreadmillOperatingRange? SpeedRange,
    TreadmillOperatingRange? InclineRange,
    Guid? EnrollmentId = null,
    string? IdentityFingerprint = null)
  {
    public static TreadmillControlAvailability Unavailable { get; } =
      new(false, false, null, false, false, false, null, null);

    public static TreadmillControlAvailability Simulated { get; } = new(
      true,
      true,
      0.8,
      true,
      true,
      true,
      new TreadmillOperatingRange(0.8m, 20m, 0.1m, TreadmillCapabilityEvidence.ProtocolReported),
      new TreadmillOperatingRange(0m, 12m, 0.1m, TreadmillCapabilityEvidence.ProtocolReported));

    public TreadmillCapabilities ToCapabilities() => new(
      CanSetSpeed,
      CanSetIncline,
      CanPause,
      CanStop,
      CanStart,
      SpeedRange: SpeedRange,
      InclineRange: InclineRange);
  }

  private sealed record AutomatedCommandRequest(
    TreadmillCommandKind Kind,
    double? TargetValue,
    TreadmillCommandOrigin Origin,
    long ExpectedSessionVersion,
    Guid LeaseId,
    string HolderId);
}
