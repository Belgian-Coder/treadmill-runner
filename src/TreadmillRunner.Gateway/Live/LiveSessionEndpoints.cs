using System.Text.Json;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Exports;

namespace TreadmillRunner.Gateway.Live;

public sealed record AcquireLeaseRequest(string HolderId);
public sealed record ArmSessionRequest(
  Guid ProfileId,
  Guid WorkoutRevisionId,
  string HolderId,
  Guid LeaseId,
  Guid OperationId,
  string SelectionSource = "Library",
  Guid? ProgramRunId = null,
  Guid? ProgramItemId = null);
public sealed record PhysicalMotionRequest(bool IsMoving, double MeasuredSpeedKph, double MeasuredInclinePercent);
public sealed record SimulatedHeartRateRequest(ushort? BeatsPerMinute);
public sealed record SpeedOverrideRequest(
  Guid OperationId,
  double AdjustmentKph,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion);
public sealed record InclineOverrideRequest(
  Guid OperationId,
  double TargetPercent,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion);
public sealed record ManualControlResponse(
  ActiveSessionSnapshot Snapshot,
  TreadmillCommandResult? CommandResult);
public sealed record HeartRateAutomationRequest(
  Guid OperationId,
  HeartRateAutomationMode Mode,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion);
public sealed record TreadmillCommandRequest(
  Guid OperationId,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion);
public sealed record SaveDebriefRequest(int? PerceivedExertion, string? Note);
public sealed record HistoryEventResponse(
  string EventType,
  DateTimeOffset OccurredAt,
  double? PreviousSpeedKph = null,
  double? RequestedSpeedKph = null,
  double? PreviousInclinePercent = null,
  double? RequestedInclinePercent = null,
  string? Message = null);

public static class LiveSessionEndpoints
{
  private static readonly JsonSerializerOptions WebJsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapLiveSessions(
    this IEndpointRouteBuilder endpoints,
    bool includeSimulatorRoutes)
  {
    RouteGroupBuilder live = endpoints.MapGroup("/api/live");
    live.MapGet("/session", static (ILiveSessionCoordinator coordinator) =>
      coordinator.CurrentSession is { } snapshot ? Results.Ok(snapshot) : Results.NoContent());
    live.MapGet("/preflight", GetPreflightAsync);
    live.MapPost("/lease/acquire", AcquireLease);
    live.MapPost("/lease/heartbeat", HeartbeatLease);
    live.MapPost("/sessions/arm", ArmAsync);
    live.MapPost("/sessions/speed-override", AdjustSpeedAsync);
    live.MapPost("/sessions/incline-override", AdjustInclineAsync);
    live.MapPost("/sessions/start", StartAsync);
    live.MapPost("/sessions/pause", PauseAsync);
    live.MapPost("/sessions/stop", StopAsync);
    live.MapPost("/sessions/heart-rate-automation", SetHeartRateAutomationAsync);
    if (includeSimulatorRoutes)
    {
      live.MapPost("/simulator/reset", ResetAsync);
      live.MapPost("/simulator/physical-motion", SetPhysicalMotionAsync);
      live.MapPost("/simulator/heart-rate", SetSimulatedHeartRateAsync);
      live.MapPost("/simulator/complete-physical-session", CompletePhysicalSessionAsync);
    }

    RouteGroupBuilder history = endpoints.MapGroup("/api/history");
    history.MapGet("", ListHistoryAsync);
    history.MapGet("/weekly", GetWeeklyHistoryAsync);
    history.MapGet("/{sessionId:guid}", GetHistoryAsync);
    history.MapGet("/{sessionId:guid}/export.csv", ExportCsvAsync);
    history.MapGet("/{sessionId:guid}/export.fit", ExportFitAsync);
    history.MapPut("/{sessionId:guid}/debrief", SaveDebriefAsync);
    return endpoints;
  }

  private static Task<IResult> ExportCsvAsync(
    Guid sessionId,
    TreadmillRunner.Core.Sessions.ISessionStore store,
    CancellationToken cancellationToken) => ExportAsync(sessionId, store, isFit: false, cancellationToken);

  private static Task<IResult> ExportFitAsync(
    Guid sessionId,
    TreadmillRunner.Core.Sessions.ISessionStore store,
    CancellationToken cancellationToken) => ExportAsync(sessionId, store, isFit: true, cancellationToken);

  private static async Task<IResult> ExportAsync(
    Guid sessionId,
    TreadmillRunner.Core.Sessions.ISessionStore store,
    bool isFit,
    CancellationToken cancellationToken)
  {
    StoredWorkoutSession? session = await store.FindAsync(sessionId, cancellationToken);
    if (session is null) return Results.NotFound();
    if (session.Samples.Count > 100_000)
      return Results.Problem("The session exceeds the bounded export sample limit.", statusCode: 413);
    byte[] content = isFit
      ? SessionFitActivityExporter.Export(session)
      : SessionCsvExporter.Export(session);
    if (content.Length > 64 * 1024 * 1024)
      return Results.Problem("The generated export exceeds 64 MiB.", statusCode: 413);
    return Results.File(
      content,
      isFit ? "application/vnd.ant.fit" : "text/csv; charset=utf-8",
      $"treadmillrunner-{sessionId:N}.{(isFit ? "fit" : "csv")}");
  }

  private static async Task<IResult> GetPreflightAsync(
    Guid profileId,
    Guid workoutRevisionId,
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      return Results.Ok(await coordinator.GetPreflightAsync(profileId, workoutRevisionId, cancellationToken));
    }
    catch (KeyNotFoundException exception)
    {
      return Results.NotFound(new { error = exception.Message });
    }
  }

  private static IResult AcquireLease(
    AcquireLeaseRequest request,
    IControlLeaseCoordinator coordinator)
  {
    ControlLease? current = coordinator.Current;
    if (current is not null && string.Equals(current.HolderId, request.HolderId, StringComparison.Ordinal))
    {
      return Results.Ok(current);
    }

    return coordinator.TryAcquire(request.HolderId) is { } lease
      ? Results.Ok(lease)
      : Results.Conflict(new { error = "Another browser currently holds the controller lease." });
  }

  private static IResult HeartbeatLease(
    ControlLease request,
    IControlLeaseCoordinator coordinator) =>
    coordinator.Heartbeat(request.Id, request.HolderId) is { } lease
      ? Results.Ok(lease)
      : Results.Conflict(new { error = "The controller lease expired or belongs to another browser." });

  private static async Task<IResult> ArmAsync(
    ArmSessionRequest request,
    ILiveSessionCoordinator coordinator,
    IWorkoutProgramStore programStore,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    const string operationType = "session.arm";
    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      request.ProfileId,
      request.WorkoutRevisionId,
      request.HolderId,
      request.LeaseId,
      request.SelectionSource,
      request.ProgramRunId,
      request.ProgramItemId,
    });
    try
    {
      if (request.OperationId == Guid.Empty)
      {
        return Results.ValidationProblem(new Dictionary<string, string[]>
        {
          [nameof(request.OperationId)] = ["OperationId is required."],
        });
      }

      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } stored)
      {
        return stored.OperationType == operationType && stored.RequestFingerprint == fingerprint
          ? Results.Content(stored.OutcomeJson, "application/json", statusCode: stored.StatusCode)
          : Results.Conflict(new { error = "That operation ID was already used for another action or request." });
      }

      if (!Enum.TryParse(request.SelectionSource, ignoreCase: true, out WorkoutSelectionSource source) ||
          source == WorkoutSelectionSource.Legacy)
      {
        return Results.ValidationProblem(new Dictionary<string, string[]>
        {
          [nameof(request.SelectionSource)] = ["SelectionSource must be Manual, Library, Calendar, or Program."],
        });
      }

      var selection = new WorkoutSessionSelection(source, request.ProgramRunId, request.ProgramItemId);
      if (source == WorkoutSelectionSource.Program)
      {
        if (request.ProgramRunId is not { } runId || request.ProgramItemId is not { } itemId ||
            await programStore.ValidateSelectionAsync(
              request.ProfileId, runId, itemId, request.WorkoutRevisionId, cancellationToken) is null)
        {
          return Results.Conflict(new { error = "The selected workout is not the next item in this runner's active training plan." });
        }
      }

      ActiveSessionSnapshot snapshot = await coordinator.ArmAsync(
        request.ProfileId,
        request.WorkoutRevisionId,
        request.LeaseId,
        request.HolderId,
        cancellationToken,
        selection);
      string outcome = JsonSerializer.Serialize(snapshot, WebJsonOptions);
      var receipt = new OperationReceipt(
        Guid.NewGuid(), request.OperationId, operationType, StatusCodes.Status201Created,
        outcome, timeProvider.GetUtcNow(), fingerprint);
      if (!await receiptStore.TryAddAsync(receipt, cancellationToken) &&
          await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } raced &&
          (raced.OperationType != operationType || raced.RequestFingerprint != fingerprint))
      {
        return Results.Conflict(new { error = "That operation ID was already used for another action or request." });
      }

      return Results.Created(
        "/api/live/session",
        snapshot);
    }
    catch (ArgumentException exception)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]>
      {
        ["request"] = [exception.Message],
      });
    }
    catch (KeyNotFoundException exception)
    {
      return Results.NotFound(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> ResetAsync(
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    await coordinator.ResetAsync(cancellationToken);
    return Results.NoContent();
  }

  private static async Task<IResult> AdjustSpeedAsync(
    SpeedOverrideRequest request,
    ILiveSessionCoordinator coordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      if (!coordinator.IsHardwareSession)
      {
        ActiveSessionSnapshot snapshot = await coordinator.AdjustRequestedSpeedAsync(
          request.OperationId,
          request.AdjustmentKph,
          request.ExpectedSessionVersion,
          request.LeaseId,
          request.HolderId,
          cancellationToken);
        return Results.Ok(new ManualControlResponse(snapshot, null));
      }

      ActiveSessionSnapshot current = coordinator.CurrentSession
        ?? throw new InvalidOperationException("No workout is armed.");
      double requestedSpeed = current.RequestedSpeedKph + request.AdjustmentKph;
      TreadmillCommandIntent intent = await coordinator.PrepareCommandAsync(
        request.OperationId,
        TreadmillCommandKind.SetSpeed,
        request.ExpectedSessionVersion,
        request.LeaseId,
        request.HolderId,
        requestedSpeed,
        TreadmillCommandOrigin.Manual,
        cancellationToken);
      TreadmillCommandResult result = await commandCoordinator.ExecuteAsync(intent, coordinator, cancellationToken);
      await coordinator.RecordCommandResultAsync(intent, result, CancellationToken.None);
      return Results.Ok(new ManualControlResponse(coordinator.CurrentSession!, result));
    }
    catch (ArgumentException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> AdjustInclineAsync(
    InclineOverrideRequest request,
    ILiveSessionCoordinator coordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      if (!coordinator.IsHardwareSession)
      {
        ActiveSessionSnapshot snapshot = await coordinator.AdjustRequestedInclineAsync(
          request.OperationId,
          request.TargetPercent,
          request.ExpectedSessionVersion,
          request.LeaseId,
          request.HolderId,
          cancellationToken);
        return Results.Ok(new ManualControlResponse(snapshot, null));
      }

      TreadmillCommandIntent intent = await coordinator.PrepareCommandAsync(
        request.OperationId,
        TreadmillCommandKind.SetIncline,
        request.ExpectedSessionVersion,
        request.LeaseId,
        request.HolderId,
        request.TargetPercent,
        TreadmillCommandOrigin.Manual,
        cancellationToken);
      TreadmillCommandResult result = await commandCoordinator.ExecuteAsync(intent, coordinator, cancellationToken);
      await coordinator.RecordCommandResultAsync(intent, result, CancellationToken.None);
      return Results.Ok(new ManualControlResponse(coordinator.CurrentSession!, result));
    }
    catch (ArgumentException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static Task<IResult> StartAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken) =>
    ExecuteCommandAsync(
      request,
      TreadmillCommandKind.Start,
      liveCoordinator,
      commandCoordinator,
      cancellationToken);

  private static async Task<IResult> StopAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    GarminActivityUploadWorker activityUploadWorker,
    CancellationToken cancellationToken) =>
    await ExecuteStopAndWakeUploadAsync(
      request,
      liveCoordinator,
      commandCoordinator,
      activityUploadWorker,
      cancellationToken);

  private static async Task<IResult> ExecuteStopAndWakeUploadAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    GarminActivityUploadWorker activityUploadWorker,
    CancellationToken cancellationToken)
  {
    IResult result = await ExecuteCommandAsync(request, TreadmillCommandKind.Stop, liveCoordinator, commandCoordinator, cancellationToken);
    activityUploadWorker.Wake();
    return result;
  }

  private static Task<IResult> PauseAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken) =>
    ExecuteCommandAsync(
      request,
      TreadmillCommandKind.Pause,
      liveCoordinator,
      commandCoordinator,
      cancellationToken);

  private static async Task<IResult> SetHeartRateAutomationAsync(
    HeartRateAutomationRequest request,
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      return Results.Ok(await coordinator.SetHeartRateAutomationAsync(
        request.OperationId,
        request.Mode,
        request.ExpectedSessionVersion,
        request.LeaseId,
        request.HolderId,
        cancellationToken));
    }
    catch (ArgumentException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> ExecuteCommandAsync(
    TreadmillCommandRequest request,
    TreadmillCommandKind kind,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      if (!liveCoordinator.IsHardwareSession && kind == TreadmillCommandKind.Stop)
      {
        return Results.Ok(await liveCoordinator.StopSimulatorAsync(
          request.OperationId,
          request.ExpectedSessionVersion,
          request.LeaseId,
          request.HolderId,
          cancellationToken));
      }

      TreadmillCommandIntent intent = await liveCoordinator.PrepareCommandAsync(
        request.OperationId,
        kind,
        request.ExpectedSessionVersion,
        request.LeaseId,
        request.HolderId,
        null,
        TreadmillCommandOrigin.Manual,
        cancellationToken);
      TreadmillCommandResult result = await commandCoordinator.ExecuteAsync(
        intent,
        liveCoordinator,
        cancellationToken);
      await liveCoordinator.RecordCommandResultAsync(intent, result, CancellationToken.None);
      return Results.Ok(result);
    }
    catch (ArgumentException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> SetPhysicalMotionAsync(
    PhysicalMotionRequest request,
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      await coordinator.SetPhysicalMotionAsync(
        request.IsMoving,
        request.MeasuredSpeedKph,
        request.MeasuredInclinePercent,
        cancellationToken);
      return Results.NoContent();
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> CompletePhysicalSessionAsync(
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      await coordinator.CompletePhysicalSessionAsync(cancellationToken);
      return Results.NoContent();
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> SetSimulatedHeartRateAsync(
    SimulatedHeartRateRequest request,
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      await coordinator.SetSimulatedHeartRateAsync(request.BeatsPerMinute, cancellationToken);
      return Results.Ok(coordinator.CurrentSession);
    }
    catch (ArgumentOutOfRangeException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return Results.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> ListHistoryAsync(
    Guid profileId,
    ISessionStore store,
    CancellationToken cancellationToken,
    int take = 50)
  {
    if (take is < 1 or > 5_000)
    {
      return Results.BadRequest(new { error = "History take must be between 1 and 5000." });
    }
    return Results.Ok(await store.ListSummariesAsync(profileId, take, cancellationToken));
  }

  private static async Task<IResult> GetHistoryAsync(
    Guid sessionId,
    ISessionStore store,
    CancellationToken cancellationToken)
  {
    StoredWorkoutSession? session = await store.FindAsync(sessionId, cancellationToken);
    if (session is null)
    {
      return Results.NotFound();
    }

    SessionAnalytics analytics = SessionAnalyticsCalculator.Calculate(
      sessionId,
      session.Samples,
      session.Events,
      ReadProfileHeartRateZones(session.Definition.ControllerConfigurationJson));
    return Results.Ok(new
    {
      session.Definition,
      session.State,
      session.StartedAt,
      session.EndedAt,
      session.Duration,
      session.DistanceKilometers,
      session.EstimatedKilocalories,
      session.AverageHeartRateBpm,
      session.MaximumHeartRateBpm,
      session.AverageSpeedKph,
      session.AverageInclinePercent,
      session.Debrief,
      session.Samples,
      Events = session.Events.Select(ToHistoryEvent).ToArray(),
      Analytics = analytics,
    });
  }

  private static async Task<IResult> GetWeeklyHistoryAsync(
    Guid profileId,
    ISessionStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    (DateTimeOffset from, DateTimeOffset throughExclusive) = CurrentBrusselsWeek(timeProvider.GetUtcNow());
    IReadOnlyList<SessionSummary> summaries = await store.ListSummariesAsync(
      profileId,
      take: 500,
      cancellationToken);
    return Results.Ok(SessionAnalyticsCalculator.CalculateWeeklyTotals(summaries, from, throughExclusive));
  }

  private static IReadOnlyList<HeartRateZone> ReadProfileHeartRateZones(string configurationJson)
  {
    try
    {
      SessionExecutionConfiguration? configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
        configurationJson,
        new JsonSerializerOptions(JsonSerializerDefaults.Web));
      return configuration?.Profile.HeartRateZones
        .Select(static zone => zone.ToHeartRateZone())
        .ToArray() ?? [];
    }
    catch (JsonException)
    {
      // Older sessions remain readable without silently applying a mutable current profile.
      return [];
    }
  }

  private static (DateTimeOffset From, DateTimeOffset ThroughExclusive) CurrentBrusselsWeek(
    DateTimeOffset nowUtc)
  {
    TimeZoneInfo timeZone = TimeZoneInfo.FindSystemTimeZoneById("Europe/Brussels");
    DateTime localNow = TimeZoneInfo.ConvertTime(nowUtc, timeZone).DateTime;
    int daysSinceMonday = ((int)localNow.DayOfWeek + 6) % 7;
    DateTime monday = DateTime.SpecifyKind(localNow.Date.AddDays(-daysSinceMonday), DateTimeKind.Unspecified);
    DateTime nextMonday = monday.AddDays(7);
    var localFrom = new DateTimeOffset(monday, timeZone.GetUtcOffset(monday));
    var localThrough = new DateTimeOffset(nextMonday, timeZone.GetUtcOffset(nextMonday));
    return (localFrom.ToUniversalTime(), localThrough.ToUniversalTime());
  }

  private static HistoryEventResponse ToHistoryEvent(SessionEvent sessionEvent) => sessionEvent switch
  {
    ManualSpeedOverrideEvent speed => new HistoryEventResponse(
      speed.EventType,
      speed.OccurredAt,
      speed.ExpectedSpeedKph,
      speed.ObservedSpeedKph),
    ManualInclineOverrideEvent incline => new HistoryEventResponse(
      incline.EventType,
      incline.OccurredAt,
      PreviousInclinePercent: incline.PreviousInclinePercent,
      RequestedInclinePercent: incline.RequestedInclinePercent),
    SessionWarningEvent warning => new HistoryEventResponse(
      warning.EventType,
      warning.OccurredAt,
      Message: warning.Message),
    DeviceDisconnectedEvent disconnected => new HistoryEventResponse(
      disconnected.EventType,
      disconnected.OccurredAt,
      Message: disconnected.Reason),
    SessionFaultedEvent fault => new HistoryEventResponse(
      fault.EventType,
      fault.OccurredAt,
      Message: fault.Message),
    SessionInterruptedEvent interrupted => new HistoryEventResponse(
      interrupted.EventType,
      interrupted.OccurredAt,
      Message: interrupted.Reason),
    _ => new HistoryEventResponse(sessionEvent.EventType, sessionEvent.OccurredAt),
  };

  private static async Task<IResult> SaveDebriefAsync(
    Guid sessionId,
    SaveDebriefRequest request,
    ISessionStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      var debrief = new SessionDebrief(sessionId, request.PerceivedExertion, request.Note, timeProvider.GetUtcNow());
      await store.SaveDebriefAsync(debrief, cancellationToken);
      return Results.Ok(debrief);
    }
    catch (ArgumentException exception)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    catch (KeyNotFoundException)
    {
      return Results.NotFound();
    }
  }
}
