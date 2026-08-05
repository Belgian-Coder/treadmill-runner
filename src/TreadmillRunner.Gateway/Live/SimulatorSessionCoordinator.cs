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

  Task ResetAsync(CancellationToken cancellationToken = default);

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
    ILogger<LiveSessionCoordinator> logger) :
  BackgroundService,
  ILiveSessionCoordinator,
  ILiveSnapshotSource
{
  private static readonly TimeSpan UpdateInterval = TimeSpan.FromMilliseconds(250);
  private static readonly TimeSpan PersistenceInterval = TimeSpan.FromSeconds(1);
  private static readonly TimeSpan FreshTelemetryLimit = TimeSpan.FromSeconds(5);
  private readonly SemaphoreSlim _gate = new(1, 1);
  private ActiveRun? _active;
  private LiveSnapshot _current = DisconnectedIdleSnapshot(timeProvider.GetUtcNow());
  private bool _startupRecoveryComplete;
  private bool _startupRecoveryWaitLogged;
  private bool _maintenanceActive;

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
    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(profileId);
    bool hardwareMode = !SimulatorAvailable;
    bool treadmillFresh = hardwareMode &&
      devices.Treadmill.State == DeviceConnectionState.Ready &&
      devices.TreadmillAge <= FreshTelemetryLimit;
    bool heartRateFresh = devices.HeartRate.DisplayName is not null &&
      devices.HeartRate.State == DeviceConnectionState.Ready &&
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
      [
        new PreflightCheck("gateway", "Gateway", PreflightCheckStatus.Ready, "Gateway ready"),
        new PreflightCheck("database", "Database", PreflightCheckStatus.Ready, "Database ready"),
        new PreflightCheck(
          "workout-targets",
          "Workout targets",
          capabilityResult.IsValid ? PreflightCheckStatus.Ready : PreflightCheckStatus.Blocked,
          capabilityResult.IsValid
            ? capabilityResult.Targets.Any(static target => target.Disposition == WorkoutTargetDisposition.Normalized)
              ? "Targets are valid; safer treadmill-increment alignment will be applied."
              : "Targets fit the selected profile and verified treadmill limits."
            : string.Join(" ", capabilityResult.Rejected.Take(3).Select(static target => $"{target.Path}: {target.Reason}"))),
        new PreflightCheck(
          "treadmill",
          "Treadmill",
          hardwareMode
            ? treadmillFresh ? PreflightCheckStatus.Ready : PreflightCheckStatus.Waiting
            : PreflightCheckStatus.Ready,
          hardwareMode
            ? treadmillFresh
              ? $"{devices.Treadmill.DisplayName} ready via {devices.Treadmill.TelemetryMode}"
              : devices.Treadmill.Fault ?? (devices.Treadmill.DisplayName is null
                ? "The enrolled treadmill is disconnected"
                : $"{devices.Treadmill.DisplayName} is {devices.Treadmill.State}")
            : "Treadmill connected (simulator)"),
        new PreflightCheck(
          "heart-rate",
          "Heart rate",
          requiresHeartRate
            ? hardwareMode
              ? heartRateFresh ? PreflightCheckStatus.Ready : PreflightCheckStatus.Waiting
              : PreflightCheckStatus.Ready
            : PreflightCheckStatus.NotRequired,
          requiresHeartRate
            ? hardwareMode
              ? heartRateFresh
                ? $"{devices.HeartRate.DisplayName} ready"
                : devices.HeartRate.Fault ?? "The selected heart-rate sensor is not providing fresh telemetry"
              : "Heart rate connected (simulator)"
            : "Not required for this workout"),
      ],
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

  public async Task<ActiveSessionSnapshot> ArmAsync(
    Guid profileId,
    Guid workoutRevisionId,
    Guid leaseId,
    string holderId,
    CancellationToken cancellationToken = default,
    WorkoutSessionSelection? selection = null)
  {
    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("A current controller lease is required to arm a workout.");
    }

    (VersionedUserProfile profile, StoredWorkoutRevision revision, WorkoutDefinition workout) =
      await LoadPlanAsync(profileId, workoutRevisionId, cancellationToken);
    PreflightSnapshot preflight = await GetPreflightAsync(
      profileId,
      workoutRevisionId,
      cancellationToken);
    if (!preflight.IsReady)
    {
      throw new InvalidOperationException("Preflight is not ready; fresh required device telemetry is missing.");
    }
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive)
        throw new InvalidOperationException("A software update is being activated; new sessions are temporarily unavailable.");
      if (_active is { Machine.State: not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted) })
      {
        throw new InvalidOperationException("Another workout session is already active.");
      }

      var now = timeProvider.GetUtcNow();
      DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(profileId);
      bool hardwareMode = !SimulatorAvailable;
      bool requiresHeartRate = ContainsHeartRateTarget(workout.Blocks);
      TreadmillControlAvailability control = await LoadControlAvailabilityAsync(cancellationToken);
      if (!hardwareMode) control = TreadmillControlAvailability.Simulated;
      WorkoutCapabilityResult capabilityResult = WorkoutCapabilityPolicy.Evaluate(
        workout,
        control.SpeedRange,
        control.InclineRange,
        profile.Profile.MaximumSpeedKph);
      if (!capabilityResult.IsValid)
        throw new InvalidOperationException("Workout targets exceed the selected profile or verified treadmill capabilities.");
      workout = capabilityResult.Definition;
      SessionTreadmillSnapshot? treadmillSnapshot = hardwareMode
        ? new SessionTreadmillSnapshot(
          devices.Treadmill.DisplayName ?? "Treadmill",
          devices.Treadmill.ProtocolId ?? "unknown",
          devices.Treadmill.TelemetryMode ?? "unknown",
          devices.Treadmill.ModelNumber,
          devices.Treadmill.FirmwareRevision,
          devices.Treadmill.Evidence,
          devices.Treadmill.Capabilities ?? control.ToCapabilities(),
          devices.Treadmill.ConnectionGeneration)
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
        SessionMetricAlgorithms.EstimatedCaloriesV1,
        selection);
      var machine = new SessionStateMachine(timeProvider);
      machine.Arm();
      var active = new ActiveRun(
        definition,
        workout,
        machine,
        new WorkoutProgression(workout),
        profile.Profile.MaximumSpeedKph ?? 20,
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
      await store.CreateAsync(definition, cancellationToken);
      _active = active;
      PublishSnapshot(active, now, SessionControlAccess.Controller, lease.ExpiresAt);
      return active.Snapshot;
    }
    finally
    {
      _gate.Release();
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
      await MarkRunningIfTransitionedAsync(active, previous, now, cancellationToken);

      PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task SetSimulatedHeartRateAsync(
    ushort? beatsPerMinute,
    CancellationToken cancellationToken = default)
  {
    if (beatsPerMinute is 0 or > 250)
      throw new ArgumentOutOfRangeException(nameof(beatsPerMinute), "Heart rate must be between 1 and 250 bpm, or null to simulate stale telemetry.");
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
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task CompletePhysicalSessionAsync(CancellationToken cancellationToken = default)
  {
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
      active.Machine.Complete();
      active.IsMoving = false;
      active.MeasuredSpeedKph = 0;
      using IServiceScope scope = scopeFactory.CreateScope();
      ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
      await store.AppendEventAsync(
        active.Definition.SessionId,
        new SessionCompletedEvent(now),
        cancellationToken);
      await store.FinalizeAsync(CreateSummary(active, SessionState.Completed, now), cancellationToken);
      PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
    }
    finally
    {
      _gate.Release();
    }
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
      active.Machine.Stop();
      active.IsMoving = false;
      active.MeasuredSpeedKph = 0;
      active.ProcessedOperationIds.Add(operationId);
      var result = new TreadmillCommandResult(
        operationId,
        TreadmillCommandKind.Stop,
        TreadmillCommandDisposition.Confirmed,
        null,
        null,
        0,
        "Simulator Stop confirmed and the session was finalized.",
        1,
        issuedAt,
        timeProvider.GetUtcNow());
      active.LastCommandResult = result;

      using IServiceScope scope = scopeFactory.CreateScope();
      ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
      await store.AppendEventAsync(active.Definition.SessionId, new SessionStoppedEvent(result.CompletedAt), cancellationToken);
      await store.FinalizeAsync(CreateSummary(active, SessionState.Stopped, result.CompletedAt), cancellationToken);
      PublishSnapshot(active, result.CompletedAt, SessionControlAccess.Controller, lease.ExpiresAt);
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
      return result;
    }
    finally
    {
      _gate.Release();
    }
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
      using IServiceScope scope = scopeFactory.CreateScope();
      await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
        active.Definition.SessionId,
        speedOverride,
        cancellationToken);
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
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
      return active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
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
      using IServiceScope scope = scopeFactory.CreateScope();
      await scope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
        active.Definition.SessionId,
        inclineOverride,
        cancellationToken);
      active.InclineOverridePercent = requested;
      active.MeasuredInclinePercent = requested;
      active.TelemetryAge = TimeSpan.Zero;
      active.Machine.MarkConfigurationChanged();
      active.ProcessedOperationIds.Add(operationId);
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
      return active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
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

    if (leaseCoordinator.Current is not ControlLease lease ||
        lease.Id != leaseId ||
        !string.Equals(lease.HolderId, holderId, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("A current controller lease is required for treadmill commands.");
    }

    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive)
        throw new InvalidOperationException("A software update is being activated; treadmill commands are unavailable.");
      ActiveRun active = RequireActive();

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
    ControlLease? lease = leaseCoordinator.Current;
    return snapshot is not null &&
      snapshot.SessionId == intent.SessionId &&
      snapshot.Version == intent.ExpectedSessionVersion &&
      snapshot.Live.SessionState == intent.ExpectedSessionState &&
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

    await _gate.WaitAsync(cancellationToken);
    try
    {
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
      }
      else if (result.Disposition == TreadmillCommandDisposition.Rejected &&
               intent.Origin is TreadmillCommandOrigin.PlannedTransition or TreadmillCommandOrigin.HeartRateAutomation)
      {
        active.HeartRateController.ResetDwell();
        active.CommandsSuspended = true;
        active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
        active.HeartRateAutomationReason = result.Reason;
        AddWarningOnce(active, "Automatic treadmill commands are suspended after a rejected command.");
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
          using IServiceScope speedScope = scopeFactory.CreateScope();
          await speedScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
            active.Definition.SessionId,
            speedOverride,
            cancellationToken);
          active.SpeedOverrideKph = acceptedSpeed;
          active.ProcessedOperationIds.Add(intent.OperationId);
          active.HeartRateController.ResetDwell();
          active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedManualOverride;
          active.HeartRateAutomationReason = "A manual speed override suspends heart-rate automation until explicitly re-enabled.";
        }
        else if (result.Kind == TreadmillCommandKind.SetIncline &&
                 intent.Origin == TreadmillCommandOrigin.Manual &&
                 result.AcceptedValue is { } acceptedIncline)
        {
          double previous = active.RequestedInclinePercent;
          active.InclineOverridePercent = acceptedIncline;
          active.ProcessedOperationIds.Add(intent.OperationId);
          using IServiceScope inclineScope = scopeFactory.CreateScope();
          await inclineScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
            active.Definition.SessionId,
            new ManualInclineOverrideEvent(previous, acceptedIncline, timeProvider.GetUtcNow()),
            cancellationToken);
        }
        else if (result.Kind == TreadmillCommandKind.Pause && active.Machine.State == SessionState.Running)
        {
          active.HeartRateController.ResetDwell();
          active.Machine.PauseWaitingForPhysicalResume();
          active.CommandsSuspended = true;
          active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
          active.HeartRateAutomationReason = "Pause suspends automation until explicitly re-enabled after physical resume.";
          using IServiceScope pauseScope = scopeFactory.CreateScope();
          await pauseScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
            active.Definition.SessionId,
            new SessionPausedEvent(SessionPauseReason.WebControl, timeProvider.GetUtcNow()),
            cancellationToken);
        }
        else if (result.Kind == TreadmillCommandKind.Stop &&
            active.Machine.State is SessionState.ArmedWaitingForPhysicalStart or SessionState.Running or SessionState.PausedWaitingForPhysicalResume)
        {
          var now = timeProvider.GetUtcNow();
          active.Machine.Stop();
          using IServiceScope scope = scopeFactory.CreateScope();
          ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
          await store.AppendEventAsync(active.Definition.SessionId, new SessionStoppedEvent(now), cancellationToken);
          await store.FinalizeAsync(CreateSummary(active, SessionState.Stopped, now), cancellationToken);
        }
      }

      var capturedAt = timeProvider.GetUtcNow();
      PublishSnapshot(active, capturedAt, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
    }
    finally
    {
      _gate.Release();
    }
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
      active.ProcessedOperationIds.Add(operationId);
      active.Warnings.Remove("Automatic treadmill commands are suspended after a rejected command.");
      active.Warnings.Remove("A treadmill command has an unknown physical outcome; automation is suspended.");
      PublishSnapshot(active, timeProvider.GetUtcNow(), SessionControlAccess.Controller, lease.ExpiresAt);
      await hubContext.Clients.All.SendAsync("sessionSnapshot", active.Snapshot, cancellationToken);
      return active.Snapshot;
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task ResetAsync(CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_active is { } active &&
          active.Machine.State is not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted))
      {
        var now = timeProvider.GetUtcNow();
        using IServiceScope scope = scopeFactory.CreateScope();
        ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
        await store.InterruptUnfinishedAsync(now, "Simulator reset.", cancellationToken);
      }

      _active = null;
      leaseCoordinator.RevokeCurrent();
      Volatile.Write(ref _current, CreateIdleSnapshot(timeProvider.GetUtcNow()));
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task<bool> TryBeginMaintenanceAsync(CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (_maintenanceActive) return false;
      if (_active is { Machine.State: not (SessionState.Completed or SessionState.Stopped) }) return false;
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
    await TryCompleteStartupRecoveryAsync(stoppingToken);

    using var timer = new PeriodicTimer(UpdateInterval);
    while (await timer.WaitForNextTickAsync(stoppingToken))
    {
      await TickAsync(stoppingToken);
    }
  }

  private async Task TickAsync(CancellationToken cancellationToken)
  {
    AutomatedCommandRequest? automatedCommand = null;
    LiveSnapshot? liveSnapshotToPublish = null;
    ActiveSessionSnapshot? sessionSnapshotToPublish = null;
    await _gate.WaitAsync(cancellationToken);
    try
    {
      if (!_startupRecoveryComplete)
      {
        await TryCompleteStartupRecoveryAsync(cancellationToken);
      }

      var now = timeProvider.GetUtcNow();
      if (_active is not { } active)
      {
        Volatile.Write(ref _current, CreateIdleSnapshot(now));
        liveSnapshotToPublish = Current;
      }
      else
      {
        await ApplyHardwareTelemetryAsync(active, now, cancellationToken);
        if (!active.HardwareMode)
        {
          active.HeartRateAge = active.HeartRateObservedAt is null
            ? TimeSpan.MaxValue
            : NonNegative(now - active.HeartRateObservedAt.Value);
        }
        UpdateMotion(active, now);
        if (active.Machine.State == SessionState.Running)
        {
          int previousStepIndex = active.Progression.CurrentStepIndex;
          foreach (WorkoutStepTransition transition in active.Progression.Advance(active.Elapsed, active.DistanceKilometers))
          {
            using IServiceScope transitionScope = scopeFactory.CreateScope();
            await transitionScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
              active.Definition.SessionId,
              new WorkoutStepTransitionEvent(
                transition.CompletedStepIndex,
                transition.CurrentStepIndex,
                active.Progression.CurrentStep?.Cue,
                now),
              cancellationToken);
          }

          if (active.Progression.CurrentStepIndex != previousStepIndex)
          {
            active.SpeedOverrideKph = null;
            active.InclineOverridePercent = null;
          }

          if (active.Progression.IsComplete)
          {
            active.Machine.Complete();
            using IServiceScope completionScope = scopeFactory.CreateScope();
            ISessionStore store = completionScope.ServiceProvider.GetRequiredService<ISessionStore>();
            await store.AppendEventAsync(active.Definition.SessionId, new SessionCompletedEvent(now), cancellationToken);
            await store.FinalizeAsync(CreateSummary(active, SessionState.Completed, now), cancellationToken);
          }
          else if (active.SampleCadence.TryAdvance(now))
          {
            using IServiceScope sampleScope = scopeFactory.CreateScope();
            await sampleScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendSampleAsync(
              CreateSample(active, now),
              cancellationToken);
          }
        }

        PublishSnapshot(active, now, AccessForCurrentLease(), leaseCoordinator.Current?.ExpiresAt);
        automatedCommand = BuildAutomatedCommand(active, now);
        liveSnapshotToPublish = Current;
        sessionSnapshotToPublish = active.Snapshot;
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

    try
    {
      if (liveSnapshotToPublish is not null)
        await hubContext.Clients.All.SendAsync("snapshot", liveSnapshotToPublish, cancellationToken);
      if (sessionSnapshotToPublish is not null)
        await hubContext.Clients.All.SendAsync("sessionSnapshot", sessionSnapshotToPublish, cancellationToken);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      logger.LogWarning(exception, "Live snapshot publication failed; the authoritative session loop will continue.");
    }

    if (automatedCommand is not null)
    {
      await ExecuteAutomatedCommandAsync(automatedCommand, cancellationToken);
    }
  }

  private AutomatedCommandRequest? BuildAutomatedCommand(ActiveRun active, DateTimeOffset now)
  {
    if (active.Machine.State != SessionState.Running ||
        active.CommandsSuspended ||
        active.TelemetryAge > FreshTelemetryLimit)
      return null;

    if (active.RequiresHeartRate &&
        (active.HeartRateBpm is null || active.HeartRateAge is null || active.HeartRateAge > FreshTelemetryLimit))
    {
      SuspendAutomation(active, "Heart-rate telemetry is stale; heart-rate automation is suspended.");
      return null;
    }

    if (leaseCoordinator.Current is not ControlLease lease)
    {
      SuspendAutomation(active, "Controller lease loss suspended automatic treadmill commands.");
      return null;
    }

    DeviceTelemetrySnapshot devices = deviceCoordinator.CurrentForProfile(active.Definition.UserProfileId);
    if (active.HardwareMode && devices.Treadmill.ConnectionGeneration != active.ConnectionGeneration)
    {
      active.ConnectionGeneration = devices.Treadmill.ConnectionGeneration;
      SuspendAutomation(active, "A treadmill reconnect expired every pending command; explicitly re-enable automation.");
      return null;
    }

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
            lease.Id,
            lease.HolderId);
      }
    }
    else if (active.CanSetSpeedRemotely && active.SpeedRange is { } speedRange)
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
          lease.Id,
          lease.HolderId);
    }

    if (active.CanSetInclineRemotely && active.InclineRange is { } inclineRange)
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
          lease.Id,
          lease.HolderId);
    }

    return null;
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
    active.HeartRateController.ResetDwell();
    active.CommandsSuspended = true;
    active.HeartRateAutomationMode = HeartRateAutomationMode.SuspendedSafety;
    active.HeartRateAutomationReason = reason;
    AddWarningOnce(active, reason);
  }

  private async Task TryCompleteStartupRecoveryAsync(CancellationToken cancellationToken)
  {
    try
    {
      using IServiceScope recoveryScope = scopeFactory.CreateScope();
      await recoveryScope.ServiceProvider.GetRequiredService<ISessionStore>()
        .InterruptUnfinishedAsync(
          timeProvider.GetUtcNow(),
          "Gateway restarted before the session reached a terminal state.",
          cancellationToken);
      _startupRecoveryComplete = true;
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
    }
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
      capabilities.InclineRange);
  }

  private async Task ApplyHardwareTelemetryAsync(
    ActiveRun active,
    DateTimeOffset now,
    CancellationToken cancellationToken)
  {
    if (!active.HardwareMode) return;
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
        using IServiceScope sourceScope = scopeFactory.CreateScope();
        await sourceScope.ServiceProvider.GetRequiredService<ISessionStore>().AppendEventAsync(
          active.Definition.SessionId,
          new SessionWarningEvent(
            "heart-rate-source-changed",
            $"Heart-rate source changed to {devices.HeartRate.DisplayName ?? "no fresh sensor"}.",
            now),
          cancellationToken);
      }
    }
    TimeSpan? treadmillAge = devices.TreadmillAge;
    if (devices.TreadmillTelemetry is { } treadmill && treadmillAge <= FreshTelemetryLimit)
    {
      SessionState previous = active.Machine.State;
      active.MeasuredSpeedKph = treadmill.SpeedKph;
      active.MeasuredInclinePercent = treadmill.InclinePercent;
      active.IsMoving = treadmill.SpeedKph > SessionStateMachine.PhysicalStartThresholdKph;
      active.TelemetryAge = treadmillAge ?? TimeSpan.Zero;
      active.Machine.ObserveTelemetry(treadmill.SpeedKph);
      await MarkRunningIfTransitionedAsync(active, previous, now, cancellationToken);
      active.Warnings.Remove("Treadmill telemetry is stale; session automation is suspended.");
    }
    else
    {
      active.IsMoving = false;
      active.TelemetryAge = treadmillAge ?? TimeSpan.MaxValue;
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
  }

  private async Task MarkRunningIfTransitionedAsync(
    ActiveRun active,
    SessionState previous,
    DateTimeOffset now,
    CancellationToken cancellationToken)
  {
    if (previous == SessionState.Running || active.Machine.State != SessionState.Running) return;
    if (previous == SessionState.ArmedWaitingForPhysicalStart)
      active.StartedAt = now;
    active.LastTickAt = now;
    using IServiceScope scope = scopeFactory.CreateScope();
    ISessionStore store = scope.ServiceProvider.GetRequiredService<ISessionStore>();
    if (previous == SessionState.ArmedWaitingForPhysicalStart)
    {
      AddWarningOnce(active, "Physical movement detected");
      await store.MarkRunningAsync(active.Definition.SessionId, now, cancellationToken);
      await store.AppendEventAsync(active.Definition.SessionId, new SessionWarningEvent(
        "physical-movement-detected",
        "Physical movement detected",
        now), cancellationToken);
    }
    else
    {
      await store.AppendEventAsync(active.Definition.SessionId, new SessionResumedEvent(now), cancellationToken);
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

    active.Elapsed = now - active.StartedAt.Value;
    if (active.IsMoving)
    {
      active.DistanceKilometers += active.MeasuredSpeedKph * delta.TotalHours;
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
      active.HardwareMode ? devices.Treadmill.ConnectionGeneration : 1);
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
      active.WorkoutPlan);
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

  private static SessionSample CreateSample(ActiveRun active, DateTimeOffset now) => new(
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
    active.Elapsed.TotalMinutes * 9,
    active.TelemetryAge,
    active.Definition.MetricAlgorithmVersion);

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
      active.Elapsed.TotalMinutes * 9,
      active.HeartRateBpm,
      active.HeartRateBpm,
      duration > TimeSpan.Zero ? active.DistanceKilometers / duration.TotalHours : 0,
      active.MeasuredInclinePercent);
  }

  private ActiveRun RequireActive() => _active
    ?? throw new InvalidOperationException("No simulator workout is armed.");

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
    "Development simulator source.");

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
        devices.TreadmillAge ?? TimeSpan.MaxValue,
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
        devices.Treadmill.ConnectionGeneration)
      : SimulatorIdleSnapshot(now);
  }

  private sealed class ActiveRun(
    NewWorkoutSession definition,
    WorkoutDefinition workout,
    SessionStateMachine machine,
    WorkoutProgression progression,
    double maximumSpeedKph,
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
    public string? HeartRateAutomationReason { get; set; } = heartRateAutomationMode == HeartRateAutomationMode.Shadow
      ? "Shadow mode records decisions without sending speed commands."
      : null;
    public long ConnectionGeneration { get; set; } = connectionGeneration;
    public bool CommandsSuspended { get; set; }
    public DateTimeOffset? StartedAt { get; set; }
    public DateTimeOffset LastTickAt { get; set; } = createdAt;
    public FixedIntervalCadence SampleCadence { get; } = new(PersistenceInterval, createdAt);
    public TimeSpan Elapsed { get; set; }
    public double DistanceKilometers { get; set; }
    public double MeasuredSpeedKph { get; set; }
    public double MeasuredInclinePercent { get; set; }
    public bool IsMoving { get; set; }
    public ushort? HeartRateBpm { get; set; } = hardwareMode ? null : (ushort)132;
    public TimeSpan? HeartRateAge { get; set; } = hardwareMode ? null : TimeSpan.Zero;
    public DateTimeOffset? HeartRateObservedAt { get; set; } = hardwareMode ? null : createdAt;
    public TimeSpan TelemetryAge { get; set; } = TimeSpan.Zero;
    public long NextSequence { get; set; }
    public List<string> Warnings { get; } = [];
    public HashSet<Guid> ProcessedOperationIds { get; } = [];
    public double? SpeedOverrideKph { get; set; }
    public double? InclineOverridePercent { get; set; }
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
    TreadmillOperatingRange? InclineRange)
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
    double TargetValue,
    TreadmillCommandOrigin Origin,
    long ExpectedSessionVersion,
    Guid LeaseId,
    string HolderId);
}
