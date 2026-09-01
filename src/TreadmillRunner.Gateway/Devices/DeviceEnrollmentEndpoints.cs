using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Devices;

public sealed record EnrollDeviceRequest(
  Guid OperationId,
  string Role,
  string DeviceId,
  string DisplayName,
  string? AdvertisedName,
  IReadOnlyList<Guid> ServiceUuids,
  string? ModelNumber,
  string? FirmwareRevision,
  string? TelemetryMode,
  IReadOnlyList<Guid>? OwnerProfileIds = null,
  bool AutoConnect = true,
  bool IsPreferred = false);

public sealed record ForgetDeviceRequest(Guid OperationId, int ExpectedVersion);
public sealed record RenameDeviceRequest(Guid OperationId, int ExpectedVersion, string DisplayName);
public sealed record ConfigureTreadmillControlsRequest(int ExpectedVersion, bool Enabled);
public sealed record ConfigureHeartRateAssignmentsRequest(
  Guid OperationId,
  IReadOnlyList<Guid> OwnerProfileIds,
  bool AutoConnect,
  bool IsPreferred,
  int Priority = 0);
public sealed record HeartRateAssignmentDto(
  Guid Id,
  Guid UserProfileId,
  int Priority,
  bool AutoConnect,
  bool IsPreferred,
  int Version);

public sealed record BleDeviceReliabilityDto(
  Guid EnrollmentId,
  string DisplayName,
  string Role,
  string CurrentState,
  long ConnectionGeneration,
  DateTimeOffset? LastTelemetryAtUtc,
  int IncidentCount,
  int RecoveredIncidentCount,
  DateTimeOffset? CurrentOutageStartedAtUtc,
  int CurrentFailedAttemptCount,
  double? LastRecoverySeconds,
  double? LongestRecoverySeconds,
  string? LastFailureKind,
  string? LastSanitizedFault,
  byte? BatteryPercent,
  DateTimeOffset? BatteryObservedAtUtc);

public sealed record BleReliabilityReportDto(
  DateTimeOffset CapturedAtUtc,
  DateTimeOffset WindowStartedAtUtc,
  int WindowDays,
  IReadOnlyList<BleDeviceReliabilityDto> Devices);

public sealed record DeviceEnrollmentDto(
  Guid Id,
  string Role,
  string DeviceId,
  string ProtocolId,
  string IdentityFingerprint,
  string DisplayName,
  string? ModelNumber,
  string? FirmwareRevision,
  string? TelemetryMode,
  TreadmillCapabilities? Capabilities,
  string Evidence,
  DateTimeOffset? LastVerifiedAtUtc,
  int Version,
  string? HeartRateDeviceKind,
  string? HeartRateDeviceFamily,
  IReadOnlyList<HeartRateAssignmentDto> Assignments);

public static class DeviceEnrollmentEndpoints
{
  private static readonly Guid HeartRateService = HeartRateDeviceClassifier.HeartRateService;
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapDeviceEnrollments(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder devices = endpoints.MapGroup("/api/devices");
    devices.MapGet("/scan", ScanAsync);
    devices.MapGet("/status", static (Guid? profileId, IReadOnlyDeviceCoordinator coordinator) =>
      TypedResults.Ok(coordinator.CurrentForProfile(profileId)));
    devices.MapGet("/reliability", ReliabilityAsync);
    RouteGroupBuilder group = devices.MapGroup("/enrollments");
    group.MapGet("/", ListAsync);
    group.MapPost("/", EnrollAsync);
    group.MapPost("/{id:guid}/retry", RetryConnectionAsync);
    group.MapPost("/{id:guid}/disconnect", DisconnectAsync);
    group.MapPut("/{id:guid}/name", RenameAsync);
    group.MapPut("/{id:guid}/treadmill-controls", ConfigureTreadmillControlsAsync);
    group.MapPut("/{id:guid}/assignments", ConfigureAssignmentsAsync);
    group.MapDelete("/{id:guid}", ForgetByIdAsync);
    group.MapDelete("/{role}", ForgetAsync);
    return endpoints;
  }

  private static async Task<IResult> RetryConnectionAsync(
    Guid id,
    IReadOnlyDeviceCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    bool found = await coordinator.RetryConnectionAsync(id, cancellationToken);
    return found
      ? TypedResults.Accepted($"/api/devices/enrollments/{id}/retry", new
      {
        message = "Connection requested until Disconnect or gateway restart. Wait for fresh telemetry before treating the device as connected.",
      })
      : TypedResults.NotFound();
  }

  private static async Task<IResult> DisconnectAsync(
    Guid id,
    IReadOnlyDeviceCoordinator coordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    IDeviceEnrollmentStore enrollmentStore,
    ILiveSessionCoordinator sessions,
    CancellationToken cancellationToken)
  {
    try
    {
      EnsureConnectionCanDisconnect(sessions);
      DeviceEnrollment? enrollment = (await enrollmentStore.ListActiveAsync(cancellationToken))
        .Select(static item => item.Enrollment)
        .SingleOrDefault(item => item.Id == id);
      if (enrollment is null) return TypedResults.NotFound();
      bool found = await coordinator.DisconnectAsync(id, cancellationToken);
      if (!found) return TypedResults.NotFound();
      if (enrollment.Role == DeviceRole.Treadmill)
        await commandCoordinator.ReleaseConnectionAsync(cancellationToken);
      return TypedResults.Ok(new
      {
        message = "The local Bluetooth connection was closed. No treadmill command was sent.",
      });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
  }

  private static async Task<IResult> RenameAsync(
    Guid id,
    RenameDeviceRequest request,
    IDeviceEnrollmentStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      ValidateOperationId(request.OperationId);
      string name = request.DisplayName?.Trim() ?? string.Empty;
      string fingerprint = PlanningOperationFingerprint.Compute(new { id, name });
      DateTimeOffset now = timeProvider.GetUtcNow();
      VersionedDeviceEnrollment saved = await store.RenameAsync(
        id,
        name,
        request.ExpectedVersion,
        now,
        new PersistenceWriteOperation(
          request.OperationId,
          "device.rename",
          StatusCodes.Status200OK,
          JsonSerializer.Serialize(new { id, displayName = name }, JsonOptions),
          now,
          fingerprint),
        cancellationToken);
      IReadOnlyList<HeartRateDeviceAssignment> assignments = await store.ListHeartRateAssignmentsAsync(cancellationToken);
      return TypedResults.Ok(ToDto(saved, assignments));
    }
    catch (ArgumentException exception)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]> { ["displayName"] = [exception.Message] });
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The device changed. Refresh and try again." });
    }
  }

  private static async Task<IResult> ConfigureTreadmillControlsAsync(
    Guid id,
    ConfigureTreadmillControlsRequest request,
    IDeviceEnrollmentStore store,
    ILiveSessionCoordinator sessions,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      EnsureIdle(sessions);
      VersionedDeviceEnrollment current = (await store.ListActiveAsync(cancellationToken))
        .SingleOrDefault(item => item.Enrollment.Id == id)
        ?? throw new KeyNotFoundException();
      if (!AcceptedTreadmillControlProfile.Matches(current.Enrollment))
        return TypedResults.Conflict(new { message = "Remote controls can be configured only for the accepted OMEGA Z / V10.23.17 FTMS profile." });
      TreadmillCapabilities capabilities = request.Enabled
        ? AcceptedTreadmillControlProfile.Enable(current.Enrollment.Capabilities)
        : AcceptedTreadmillControlProfile.Disable(current.Enrollment.Capabilities);
      VersionedDeviceEnrollment saved = await store.UpdateEvidenceAsync(
        id,
        request.ExpectedVersion,
        current.Enrollment.ModelNumber,
        current.Enrollment.FirmwareRevision,
        capabilities,
        TreadmillCapabilityEvidence.HardwareVerified,
        timeProvider.GetUtcNow(),
        cancellationToken);
      return TypedResults.Ok(new
      {
        enabled = saved.Enrollment.Capabilities?.CanStartRemotely == true,
        message = request.Enabled
          ? "Verified Start, Stop, speed, and incline controls are enabled. Raw Pause remains disabled."
          : "Remote treadmill controls are disabled.",
        version = saved.Version,
      });
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The treadmill settings changed. Refresh and try again." });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
  }

  private static async Task<IResult> ReliabilityAsync(
    int? days,
    IDeviceEnrollmentStore enrollmentStore,
    [FromServices] IBleReliabilityStore reliabilityStore,
    IReadOnlyDeviceCoordinator coordinator,
    TimeProvider timeProvider,
    HttpContext httpContext,
    CancellationToken cancellationToken)
  {
    int windowDays = days ?? 7;
    if (windowDays is < 1 or > 90)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["days"] = ["Use a reporting window between 1 and 90 days."],
      });
    }

    DateTimeOffset now = timeProvider.GetUtcNow();
    DateTimeOffset since = now.AddDays(-windowDays);
    IReadOnlyList<VersionedDeviceEnrollment> enrollments = await enrollmentStore.ListActiveAsync(cancellationToken);
    IReadOnlyList<BleReliabilityIncident> incidents = await reliabilityStore.ListSinceAsync(
      since,
      maximumCount: 1000,
      cancellationToken);
    DeviceTelemetrySnapshot status = coordinator.Current;
    BleDeviceReliabilityDto[] devices = enrollments.Select(enrollment =>
    {
      DeviceEnrollment device = enrollment.Enrollment;
      BleReliabilityIncident[] deviceIncidents = incidents
        .Where(incident => incident.DeviceEnrollmentId == device.Id)
        .OrderByDescending(incident => incident.StartedAtUtc)
        .ToArray();
      BleReliabilityIncident? lastRecovered = deviceIncidents.FirstOrDefault(incident => incident.RecoveredAtUtc is not null);
      double? longestRecovery = deviceIncidents
        .Where(incident => incident.RecoveryDuration is not null)
        .Select(incident => (double?)incident.RecoveryDuration!.Value.TotalSeconds)
        .Max();
      HeartRateSourceSnapshot? heartRate = device.Role == DeviceRole.HeartRate
        ? status.HeartRateSources?.FirstOrDefault(source => source.EnrollmentId == device.Id)
        : null;
      DeviceConnectionSnapshot connection = device.Role == DeviceRole.Treadmill
        ? status.Treadmill
        : heartRate is null
          ? new DeviceConnectionSnapshot(DeviceRole.HeartRate, DeviceConnectionState.Disconnected, 0, device.DisplayName, device.ProtocolId, null, null, null)
          : new DeviceConnectionSnapshot(DeviceRole.HeartRate, heartRate.State, heartRate.ConnectionGeneration, heartRate.DisplayName, device.ProtocolId, null, heartRate.ObservedAt, heartRate.Fault);
      bool canHaveCurrentOutage = connection.State is not (DeviceConnectionState.Disconnected or DeviceConnectionState.Ready);
      BleReliabilityIncident? currentOutage = canHaveCurrentOutage
        ? deviceIncidents.FirstOrDefault(incident => incident.RecoveredAtUtc is null)
        : null;
      BleReliabilityIncident? latest = deviceIncidents.FirstOrDefault();
      int activeFailureCount = canHaveCurrentOutage
        ? coordinator.ActiveReliabilityFailureCount(device.Id)
        : 0;
      return new BleDeviceReliabilityDto(
        device.Id,
        device.DisplayName,
        device.Role.ToString(),
        connection.State.ToString(),
        connection.ConnectionGeneration,
        connection.LastObservedAt,
        deviceIncidents.Length,
        deviceIncidents.Count(incident => incident.RecoveredAtUtc is not null),
        currentOutage?.StartedAtUtc,
        Math.Max(currentOutage?.FailedAttemptCount ?? 0, activeFailureCount),
        lastRecovered?.RecoveryDuration?.TotalSeconds,
        longestRecovery,
        latest?.FailureKind.ToString(),
        latest?.LastSanitizedFault,
        heartRate?.BatteryPercent,
        heartRate?.BatteryObservedAt);
    }).ToArray();

    httpContext.Response.Headers.CacheControl = "no-store";
    return TypedResults.Ok(new BleReliabilityReportDto(now, since, windowDays, devices));
  }

  private static async Task<IResult> ScanAsync(
    int durationSeconds,
    IBleAdvertisementBroker advertisementBroker,
    TreadmillProtocolRegistry protocols,
    ILoggerFactory loggerFactory,
    CancellationToken cancellationToken)
  {
    if (durationSeconds is < 1 or > 30)
    {
      return TypedResults.ValidationProblem(
        new Dictionary<string, string[]> { ["durationSeconds"] = ["Duration must be between 1 and 30 seconds."] });
    }

    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    timeout.CancelAfter(TimeSpan.FromSeconds(durationSeconds));
    var advertisements = new Dictionary<string, BleAdvertisement>(StringComparer.Ordinal);
    try
    {
      await foreach (BleAdvertisement advertisement in advertisementBroker.ScanAsync(timeout.Token))
      {
        advertisements.TryGetValue(advertisement.DeviceId, out BleAdvertisement? current);
        advertisements[advertisement.DeviceId] = MergeAdvertisement(current, advertisement);
      }
    }
    catch (OperationCanceledException) when (timeout.IsCancellationRequested && !cancellationToken.IsCancellationRequested)
    {
      // The bounded scan completed normally.
    }
    catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
    {
      loggerFactory.CreateLogger("DeviceEnrollmentScan").LogWarning(
        exception,
        "BLE enrollment discovery ended before a complete bounded scan was observed.");
      return TypedResults.Problem(
        "BLE device discovery was unavailable. Try the scan again.",
        statusCode: StatusCodes.Status503ServiceUnavailable);
    }

    object[] result = advertisements.Values
      .Select(advertisement => new
      {
        advertisement.DeviceId,
        advertisement.Name,
        advertisement.SignalStrength,
        advertisement.ServiceUuids,
        SupportedRoles = SupportedRoles(advertisement, protocols),
        HeartRateDeviceKind = HeartRateDeviceClassifier.Classify(
          advertisement.Name,
          advertisement.ServiceUuids).ToString(),
        IsPreferredHeartRate = HeartRateDeviceClassifier.IsPreferredPolar(
          advertisement.Name,
          advertisement.ServiceUuids),
        HeartRatePriority = HeartRateDeviceClassifier.Priority(
          advertisement.Name,
          advertisement.ServiceUuids),
      })
      .Where(static candidate => candidate.SupportedRoles.Length > 0)
      .OrderBy(static candidate =>
        candidate.SupportedRoles.Contains(DeviceRole.HeartRate.ToString(), StringComparer.Ordinal)
          ? candidate.HeartRatePriority
          : int.MaxValue)
      .ThenBy(static candidate => candidate.DeviceId, StringComparer.Ordinal)
      .Select(static candidate => new
      {
        candidate.DeviceId,
        candidate.Name,
        candidate.SignalStrength,
        candidate.ServiceUuids,
        candidate.SupportedRoles,
        candidate.HeartRateDeviceKind,
        candidate.IsPreferredHeartRate,
      })
      .Cast<object>()
      .ToArray();
    return TypedResults.Ok(result);
  }

  private static BleAdvertisement MergeAdvertisement(
    BleAdvertisement? current,
    BleAdvertisement incoming)
  {
    if (current is null)
    {
      return incoming with
      {
        ServiceUuids = [.. incoming.ServiceUuids.Distinct().OrderBy(static uuid => uuid)],
      };
    }

    bool useIncoming = incoming.SignalStrength is not null &&
      (current.SignalStrength is null || incoming.SignalStrength > current.SignalStrength);
    BleAdvertisement strongest = useIncoming ? incoming : current;
    string? name = strongest.Name ?? (useIncoming ? current.Name : incoming.Name);
    return strongest with
    {
      Name = name,
      ServiceUuids = [.. current.ServiceUuids.Concat(incoming.ServiceUuids).Distinct().OrderBy(static uuid => uuid)],
    };
  }

  private static string[] SupportedRoles(
    BleAdvertisement advertisement,
    TreadmillProtocolRegistry protocols)
  {
    var roles = new List<string>(2);
    if (protocols.Resolve(new TreadmillAdvertisementIdentity(
      advertisement.Name,
      advertisement.ServiceUuids)) is not null)
    {
      roles.Add(DeviceRole.Treadmill.ToString());
    }

    if (advertisement.ServiceUuids.Contains(HeartRateService))
    {
      roles.Add(DeviceRole.HeartRate.ToString());
    }

    return roles.ToArray();
  }

  private static async Task<IResult> ListAsync(
    IDeviceEnrollmentStore store,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<HeartRateDeviceAssignment> assignments = await store.ListHeartRateAssignmentsAsync(cancellationToken);
    return TypedResults.Ok((await store.ListActiveAsync(cancellationToken))
      .Select(value => ToDto(value, assignments))
      .ToArray());
  }

  private static async Task<IResult> EnrollAsync(
    EnrollDeviceRequest request,
    IDeviceEnrollmentStore store,
    IOperationReceiptStore receiptStore,
    TreadmillProtocolRegistry protocolRegistry,
    ILiveSessionCoordinator sessionCoordinator,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      DeviceRole role = ParseRole(request.Role);
      EnsureEnrollmentAllowed(sessionCoordinator, role);
      ValidateOperationId(request.OperationId);
      if (request.ServiceUuids is null)
      {
        throw new ArgumentException("ServiceUuids cannot be null.");
      }

      DeviceEnrollment enrollment = CreateEnrollment(request, role, protocolRegistry);
      fingerprint = PlanningOperationFingerprint.Compute(new
      {
        enrollment.Role,
        enrollment.DeviceId,
        enrollment.ProtocolId,
        enrollment.IdentityFingerprint,
        enrollment.DisplayName,
        request.AdvertisedName,
        enrollment.ModelNumber,
        enrollment.FirmwareRevision,
        enrollment.TelemetryMode,
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "device.enroll", fingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      HeartRateAssignmentPreference[] assignments = role == DeviceRole.HeartRate
        ? CreateAssignmentPreferences(request)
        : [];
      var expected = new VersionedDeviceEnrollment(enrollment, 1, false, null);
      VersionedDeviceEnrollment saved = await store.EnrollWithAssignmentsAsync(
        enrollment,
        assignments,
        now,
        new PersistenceWriteOperation(
          request.OperationId,
          "device.enroll",
          StatusCodes.Status201Created,
          JsonSerializer.Serialize(ToDto(expected, []), JsonOptions),
          now,
          fingerprint),
        cancellationToken);
      IReadOnlyList<HeartRateDeviceAssignment> savedAssignments = await store.ListHeartRateAssignmentsAsync(cancellationToken);
      return TypedResults.Created($"/api/devices/enrollments/{saved.Enrollment.Id}", ToDto(saved, savedAssignments));
    }
    catch (ArgumentException exception)
    {
      return TypedResults.ValidationProblem(
        new Dictionary<string, string[]> { ["request"] = [exception.Message] });
    }
    catch (InvalidOperationException exception) when (
      exception is not OperationReplayException and not OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
    catch (DbUpdateException)
    {
      return TypedResults.Conflict(new { message = "A device is already enrolled for that role." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay.Receipt, "device.enroll", fingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> ConfigureAssignmentsAsync(
    Guid id,
    ConfigureHeartRateAssignmentsRequest request,
    IDeviceEnrollmentStore store,
    ILiveSessionCoordinator sessionCoordinator,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      EnsureIdle(sessionCoordinator);
      ValidateOperationId(request.OperationId);
      VersionedDeviceEnrollment enrollment = (await store.ListActiveAsync(cancellationToken))
        .SingleOrDefault(item => item.Enrollment.Id == id)
        ?? throw new KeyNotFoundException($"Enrollment {id} was not found.");
      bool polar = HeartRateReconnectResolver.EffectiveFamily(enrollment.Enrollment) ==
        HeartRateDeviceFamily.Polar;
      HeartRateAssignmentPreference[] assignments = CreateAssignmentPreferences(
        request.OwnerProfileIds,
        polar ? 0 : request.Priority,
        polar || request.AutoConnect,
        polar || request.IsPreferred);
      fingerprint = PlanningOperationFingerprint.Compute(new { id, assignments });
      DateTimeOffset now = timeProvider.GetUtcNow();
      IReadOnlyList<HeartRateDeviceAssignment> saved = await store.ConfigureHeartRateAssignmentsAsync(
        id,
        assignments,
        now,
        new PersistenceWriteOperation(
          request.OperationId,
          "device.assign-heart-rate",
          StatusCodes.Status200OK,
          JsonSerializer.Serialize(assignments, JsonOptions),
          now,
          fingerprint),
        cancellationToken);
      return TypedResults.Ok(saved.Select(ToAssignmentDto).ToArray());
    }
    catch (ArgumentException exception)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [exception.Message] });
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (InvalidOperationException exception) when (exception is not OperationReplayException and not OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
    catch (DbUpdateException)
    {
      return TypedResults.Conflict(new { message = "A runner can have only one preferred heart-rate sensor." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay.Receipt, "device.assign-heart-rate", fingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> ForgetByIdAsync(
    Guid id,
    [FromBody] ForgetDeviceRequest request,
    IDeviceEnrollmentStore store,
    ILiveSessionCoordinator sessionCoordinator,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      EnsureIdle(sessionCoordinator);
      ValidateOperationId(request.OperationId);
      fingerprint = PlanningOperationFingerprint.Compute(new { id, request.ExpectedVersion });
      DateTimeOffset now = timeProvider.GetUtcNow();
      bool forgotten = await store.ForgetByIdAsync(
        id,
        request.ExpectedVersion,
        now,
        new PersistenceWriteOperation(
          request.OperationId,
          "device.forget",
          StatusCodes.Status204NoContent,
          "{}",
          now,
          fingerprint),
        cancellationToken);
      return forgotten ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (InvalidOperationException exception) when (exception is not OperationReplayException and not OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The enrollment changed in another client. Reload and try again." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay.Receipt, "device.forget", fingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> ForgetAsync(
    string role,
    [FromBody] ForgetDeviceRequest request,
    IDeviceEnrollmentStore store,
    IOperationReceiptStore receiptStore,
    ILiveSessionCoordinator sessionCoordinator,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      EnsureIdle(sessionCoordinator);
      DeviceRole parsedRole = ParseRole(role);
      ValidateOperationId(request.OperationId);
      if (request.ExpectedVersion < 1)
      {
        throw new ArgumentException("ExpectedVersion must be greater than zero.");
      }

      fingerprint = PlanningOperationFingerprint.Compute(new { Role = parsedRole, request.ExpectedVersion });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "device.forget", fingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      bool forgotten = await store.ForgetAsync(
        parsedRole,
        request.ExpectedVersion,
        now,
        new PersistenceWriteOperation(
          request.OperationId,
          "device.forget",
          StatusCodes.Status204NoContent,
          "{}",
          now,
          fingerprint),
        cancellationToken);
      return forgotten ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return TypedResults.ValidationProblem(
        new Dictionary<string, string[]> { ["request"] = [exception.Message] });
    }
    catch (InvalidOperationException exception) when (
      exception is not OperationReplayException and not OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { message = exception.Message });
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The enrollment changed in another client. Reload and try again." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay.Receipt, "device.forget", fingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static DeviceEnrollment CreateEnrollment(
    EnrollDeviceRequest request,
    DeviceRole role,
    TreadmillProtocolRegistry protocolRegistry)
  {
    string protocolId;
    TreadmillTelemetryMode? mode = null;
    TreadmillCapabilities? capabilities = null;
    if (role == DeviceRole.Treadmill)
    {
      ITreadmillProtocol protocol = protocolRegistry.Resolve(
        new TreadmillAdvertisementIdentity(request.AdvertisedName, request.ServiceUuids))
        ?? throw new ArgumentException("The selected device is not supported as a treadmill.");
      if (!Enum.TryParse(request.TelemetryMode, ignoreCase: true, out TreadmillTelemetryMode parsedMode) ||
          !Enum.IsDefined(parsedMode))
      {
        throw new ArgumentException("TelemetryMode must be Ftms or Vendor for a treadmill.");
      }

      protocolId = protocol.ProtocolId;
      mode = parsedMode;
      capabilities = protocol.Capabilities;
    }
    else
    {
      if (!request.ServiceUuids.Contains(HeartRateService))
      {
        throw new ArgumentException("The selected heart-rate device does not advertise the Heart Rate service.");
      }

      if (!string.IsNullOrWhiteSpace(request.TelemetryMode))
      {
        throw new ArgumentException("TelemetryMode is only valid for a treadmill.");
      }

      protocolId = "bluetooth-heart-rate";
    }

    string identityFingerprint = PlanningOperationFingerprint.Compute(new
    {
      Role = role,
      request.DeviceId,
      ProtocolId = protocolId,
      request.AdvertisedName,
      request.DisplayName,
      Services = request.ServiceUuids.Order().ToArray(),
    });
    return new DeviceEnrollment(
      Guid.NewGuid(),
      role,
      request.DeviceId,
      protocolId,
      identityFingerprint,
      request.DisplayName,
      request.ModelNumber,
      request.FirmwareRevision,
      mode,
      capabilities,
      TreadmillCapabilityEvidence.Unknown,
      lastVerifiedAtUtc: null,
      role == DeviceRole.HeartRate ? HeartRateDeviceClassifier.Classify(request.DisplayName, request.ServiceUuids) : null,
      role == DeviceRole.HeartRate ? HeartRateDeviceClassifier.Family(request.DisplayName, request.ServiceUuids) : null);
  }

  private static DeviceEnrollmentDto ToDto(
    VersionedDeviceEnrollment value,
    IReadOnlyList<HeartRateDeviceAssignment> assignments) => new(
    value.Enrollment.Id,
    value.Enrollment.Role.ToString(),
    value.Enrollment.DeviceId,
    value.Enrollment.ProtocolId,
    value.Enrollment.IdentityFingerprint,
    value.Enrollment.DisplayName,
    value.Enrollment.ModelNumber,
    value.Enrollment.FirmwareRevision,
    value.Enrollment.TelemetryMode?.ToString(),
    value.Enrollment.Capabilities,
    value.Enrollment.Evidence.ToString(),
    value.Enrollment.LastVerifiedAtUtc,
    value.Version,
    value.Enrollment.Role == DeviceRole.HeartRate
      ? HeartRateReconnectResolver.EffectiveKind(value.Enrollment).ToString()
      : null,
    value.Enrollment.Role == DeviceRole.HeartRate
      ? HeartRateReconnectResolver.EffectiveFamily(value.Enrollment).ToString()
      : null,
    assignments.Where(item => item.DeviceEnrollmentId == value.Enrollment.Id).Select(ToAssignmentDto).ToArray());

  private static HeartRateAssignmentDto ToAssignmentDto(HeartRateDeviceAssignment value) => new(
    value.Id,
    value.UserProfileId,
    value.Priority,
    value.AutoConnect,
    value.IsPreferred,
    value.Version);

  private static HeartRateAssignmentPreference[] CreateAssignmentPreferences(EnrollDeviceRequest request) =>
    CreateAssignmentPreferences(
      request.OwnerProfileIds ?? [],
      HeartRateDeviceClassifier.Priority(request.DisplayName, request.ServiceUuids),
      request.AutoConnect,
      request.IsPreferred || HeartRateDeviceClassifier.IsPreferredPolar(request.DisplayName, request.ServiceUuids));

  private static HeartRateAssignmentPreference[] CreateAssignmentPreferences(
    IReadOnlyList<Guid> profileIds,
    int priority,
    bool autoConnect,
    bool preferred) => profileIds
      .Distinct()
      .Select(profileId => new HeartRateAssignmentPreference(profileId, priority, autoConnect, preferred))
      .ToArray();

  private static DeviceRole ParseRole(string value)
  {
    if (int.TryParse(value, out _) ||
        !Enum.TryParse(value, ignoreCase: true, out DeviceRole role) ||
        !Enum.IsDefined(role))
    {
      throw new ArgumentException("Role must be Treadmill or HeartRate.");
    }

    return role;
  }

  private static void EnsureIdle(ILiveSessionCoordinator coordinator)
  {
    if (coordinator.CurrentSession?.Live.SessionState is
        SessionState.ArmedWaitingForPhysicalStart or
        SessionState.Running or
        SessionState.PausedWaitingForPhysicalResume)
    {
      throw new InvalidOperationException("Device enrollment cannot change while a workout is active.");
    }
  }

  private static void EnsureConnectionCanDisconnect(ILiveSessionCoordinator coordinator)
  {
    if (coordinator.CurrentSession?.Live.SessionState is
        SessionState.ArmedWaitingForPhysicalStart or
        SessionState.Running or
        SessionState.PausedWaitingForPhysicalResume)
    {
      throw new InvalidOperationException(
        "End the active session before disconnecting a device. Bluetooth disconnect is not a treadmill stop mechanism.");
    }
  }

  private static void EnsureEnrollmentAllowed(
    ILiveSessionCoordinator coordinator,
    DeviceRole role)
  {
    SessionState? state = coordinator.CurrentSession?.Live.SessionState;
    if (state is SessionState.Running or SessionState.PausedWaitingForPhysicalResume ||
        role == DeviceRole.Treadmill && state == SessionState.ArmedWaitingForPhysicalStart)
    {
      throw new InvalidOperationException("Device enrollment cannot change while a workout is active.");
    }
  }

  private static void ValidateOperationId(Guid value)
  {
    if (value == Guid.Empty) throw new ArgumentException("OperationId cannot be empty.");
  }

  private static IResult Replay(OperationReceipt receipt, string type, string fingerprint)
  {
    if (receipt.OperationType != type || receipt.RequestFingerprint != fingerprint)
    {
      return OperationConflict();
    }

    return receipt.StatusCode == StatusCodes.Status204NoContent
      ? TypedResults.NoContent()
      : Results.Content(receipt.OutcomeJson, "application/json", statusCode: receipt.StatusCode);
  }

  private static IResult OperationConflict() =>
    TypedResults.Conflict(new { message = "That operation ID was already used for another action or request." });
}
