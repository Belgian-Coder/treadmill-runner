using System.Text.Json;
using Microsoft.EntityFrameworkCore;
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
public sealed record ResumePlannedControlsRequest(
  Guid OperationId,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion,
  long ConnectionGeneration);
public sealed record TreadmillCommandRequest(
  Guid OperationId,
  string HolderId,
  Guid LeaseId,
  long ExpectedSessionVersion);
public sealed record DeleteHistorySessionRequest(
  Guid OperationId,
  Guid ProfileId,
  string ExpectedRevision,
  bool Confirmed);
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
    live.MapPost("/sessions/stop", StopAsync);
    live.MapPost("/sessions/end", EndSessionAsync);
    live.MapPost("/sessions/reset-progress", ResetWorkoutProgressAsync);
    live.MapPost("/sessions/heart-rate-automation", SetHeartRateAutomationAsync);
    live.MapPost("/sessions/resume-planned-controls", ResumePlannedControlsAsync);
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
    history.MapGet("/{sessionId:guid}/deletion-preview", GetDeletionPreviewAsync);
    history.MapPost("/{sessionId:guid}/delete", DeleteHistoryAsync);
    history.MapGet("/{sessionId:guid}/export.csv", ExportCsvAsync);
    history.MapGet("/{sessionId:guid}/export.fit", ExportFitAsync);
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
    return coordinator.TryAcquire(request.HolderId) is { } lease
      ? Results.Ok(lease)
      : Results.Conflict(new { error = "Another browser currently holds the controller lease." });
  }

  private static async Task<IResult> ResumePlannedControlsAsync(
    ResumePlannedControlsRequest request,
    ILiveSessionCoordinator coordinator,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    const string operationType = "session.resume-planned-controls";
    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      request.HolderId,
      request.LeaseId,
      request.ExpectedSessionVersion,
      request.ConnectionGeneration,
    });
    try
    {
      if (request.OperationId == Guid.Empty)
        return Results.BadRequest(new { error = "An operation ID is required." });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } stored)
      {
        return stored.OperationType == operationType && stored.RequestFingerprint == fingerprint
          ? Results.Content(stored.OutcomeJson, "application/json", statusCode: stored.StatusCode)
          : Results.Conflict(new { error = "That operation ID was already used for another action or request." });
      }

      ActiveSessionSnapshot snapshot = await coordinator.ResumePlannedControlsAsync(
        request.OperationId,
        request.ExpectedSessionVersion,
        request.ConnectionGeneration,
        request.LeaseId,
        request.HolderId,
        cancellationToken);
      string outcome = JsonSerializer.Serialize(snapshot, WebJsonOptions);
      var receipt = new OperationReceipt(
        Guid.NewGuid(), request.OperationId, operationType, StatusCodes.Status200OK,
        outcome, timeProvider.GetUtcNow(), fingerprint);
      if (!await receiptStore.TryAddAsync(receipt, cancellationToken) &&
          await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } raced &&
          (raced.OperationType != operationType || raced.RequestFingerprint != fingerprint))
        return Results.Conflict(new { error = "That operation ID was already used for another action or request." });
      return Results.Ok(snapshot);
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

  private static Task<IResult> StopAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator liveCoordinator,
    ITreadmillCommandCoordinator commandCoordinator,
    CancellationToken cancellationToken) =>
    ExecuteCommandAsync(
      request,
      TreadmillCommandKind.Stop,
      liveCoordinator,
      commandCoordinator,
      cancellationToken);

  private static async Task<IResult> EndSessionAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator coordinator,
    GarminActivityUploadWorker activityUploadWorker,
    CancellationToken cancellationToken)
  {
    try
    {
      ActiveSessionSnapshot snapshot = await coordinator.EndSessionAsync(
        request.OperationId, request.ExpectedSessionVersion, request.LeaseId, request.HolderId, cancellationToken);
      activityUploadWorker.Wake();
      return Results.Ok(snapshot);
    }
    catch (ArgumentException exception) { return Results.BadRequest(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return Results.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> ResetWorkoutProgressAsync(
    TreadmillCommandRequest request,
    ILiveSessionCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    try
    {
      return Results.Ok(await coordinator.ResetWorkoutProgressAsync(
        request.OperationId, request.ExpectedSessionVersion, request.LeaseId, request.HolderId, cancellationToken));
    }
    catch (ArgumentException exception) { return Results.BadRequest(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return Results.Conflict(new { error = exception.Message }); }
  }

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
    int take = 50,
    bool includeTests = false)
  {
    if (take is < 1 or > 5_000)
    {
      return Results.BadRequest(new { error = "History take must be between 1 and 5000." });
    }
    return Results.Ok(await store.ListSummariesAsync(profileId, take, cancellationToken, includeTests));
  }

  private static async Task<IResult> GetHistoryAsync(
    Guid sessionId,
    ISessionStore store,
    CancellationToken cancellationToken)
  {
    StoredWorkoutSessionDisplay? display = await store.FindDisplayAsync(sessionId, cancellationToken);
    if (display is null)
    {
      return Results.NotFound();
    }

    StoredWorkoutSession session = display.Session;
    SessionAnalytics? analytics = await store.CalculateAnalyticsAsync(
      sessionId,
      ReadProfileHeartRateZones(session.Definition.ControllerConfigurationJson),
      cancellationToken);
    SessionSampleStatistics? statistics = await store.CalculateSampleStatisticsAsync(sessionId, cancellationToken);
    if (analytics is null || statistics is null)
    {
      return Results.NotFound();
    }
    return Results.Ok(new
    {
      session.Definition,
      session.State,
      session.StartedAt,
      session.EndedAt,
      session.Duration,
      session.DistanceKilometers,
      EstimatedKilocalories = statistics.EstimatedKilocalories ?? session.EstimatedKilocalories,
      session.AverageHeartRateBpm,
      session.MaximumHeartRateBpm,
      session.AverageSpeedKph,
      session.AverageInclinePercent,
      statistics.TotalAscentMeters,
      statistics.TotalDescentMeters,
      statistics.NetElevationMeters,
      session.Debrief,
      Samples = session.Samples,
      TotalSampleCount = display.TotalSampleCount,
      Events = session.Events.Select(ToHistoryEvent).ToArray(),
      Analytics = analytics,
    });
  }

  private static async Task<IResult> GetDeletionPreviewAsync(
    Guid sessionId,
    Guid profileId,
    ISessionStore store,
    CancellationToken cancellationToken)
  {
    HistoryDeletionPreview? preview = await store.PreviewDeletionAsync(sessionId, profileId, cancellationToken);
    return preview is null ? TypedResults.NotFound() : TypedResults.Ok(preview);
  }

  private static async Task<IResult> DeleteHistoryAsync(
    Guid sessionId,
    DeleteHistorySessionRequest request,
    ISessionStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty || request.ProfileId == Guid.Empty || sessionId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "Operation, profile, and session IDs are required." });
    if (!request.Confirmed)
      return TypedResults.BadRequest(new { error = "Explicit deletion confirmation is required." });
    if (string.IsNullOrWhiteSpace(request.ExpectedRevision) || request.ExpectedRevision.Length != 64)
      return TypedResults.BadRequest(new { error = "Review the deletion preview before confirming." });

    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      request.ProfileId,
      SessionId = sessionId,
      request.ExpectedRevision,
      request.Confirmed,
    });
    try
    {
      HistoryDeletionResult result = await store.DeleteAsync(new DeleteHistorySessionOperation(
        request.OperationId,
        sessionId,
        request.ProfileId,
        request.ExpectedRevision,
        fingerprint,
        timeProvider.GetUtcNow()), cancellationToken);
      return TypedResults.Ok(result);
    }
    catch (OperationReplayException replay)
    {
      if (!string.Equals(replay.Receipt.OperationType, "history.delete", StringComparison.Ordinal) ||
          !string.Equals(replay.Receipt.RequestFingerprint, fingerprint, StringComparison.Ordinal))
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
      HistoryDeletionResult? result = JsonSerializer.Deserialize<HistoryDeletionResult>(
        replay.Receipt.OutcomeJson,
        WebJsonOptions);
      return result is null
        ? TypedResults.Conflict(new { error = "The completed deletion receipt could not be read." })
        : TypedResults.Ok(result);
    }
    catch (OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
    }
    catch (DbUpdateConcurrencyException exception)
    {
      return TypedResults.Conflict(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.Conflict(new { error = exception.Message });
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return TypedResults.BadRequest(new { error = exception.Message });
    }
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
    WorkoutProgressResetEvent reset => new HistoryEventResponse(
      reset.EventType,
      reset.OccurredAt,
      Message: $"Workout progress reset from step {reset.PreviousStepIndex + 1} after {reset.PreviousWorkoutElapsed:hh\\:mm\\:ss}."),
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

}
