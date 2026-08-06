using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Devices;

public sealed record UpdateTreadmillMaintenancePolicyRequest(
  Guid OperationId,
  int IntervalMonths,
  double DistanceIntervalKilometers,
  int ExpectedVersion);

public sealed record RecordTreadmillMaintenanceRequest(
  Guid OperationId,
  DateTimeOffset PerformedAtUtc,
  string? Note,
  int ExpectedPolicyVersion);

public static class TreadmillMaintenanceEndpoints
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapTreadmillMaintenance(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/devices/treadmill/maintenance");
    group.MapGet("", GetAsync);
    group.MapPut("/policy", UpdatePolicyAsync);
    group.MapPost("/events", RecordAsync);
    return endpoints;
  }

  private static async Task<IResult> GetAsync(
    ITreadmillMaintenanceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    TreadmillMaintenanceSnapshot? snapshot = await store.GetAsync(timeProvider.GetUtcNow(), cancellationToken);
    return snapshot is null ? TypedResults.NoContent() : TypedResults.Ok(snapshot);
  }

  private static async Task<IResult> UpdatePolicyAsync(
    UpdateTreadmillMaintenancePolicyRequest request,
    ITreadmillMaintenanceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      request.IntervalMonths,
      request.DistanceIntervalKilometers,
      request.ExpectedVersion,
    });
    DateTimeOffset now = timeProvider.GetUtcNow();
    var operation = new PersistenceWriteOperation(
      request.OperationId,
      "treadmill.maintenance.policy",
      StatusCodes.Status200OK,
      "{}",
      now,
      fingerprint);
    try
    {
      return TypedResults.Ok(await store.UpdatePolicyAsync(
        request.IntervalMonths,
        request.DistanceIntervalKilometers,
        request.ExpectedVersion,
        operation,
        now,
        cancellationToken));
    }
    catch (Exception exception) when (TryReplay(exception, operation, out TreadmillMaintenanceSnapshot? replay))
    {
      return TypedResults.Ok(replay!);
    }
    catch (ArgumentException exception) { return TypedResults.BadRequest(new { error = exception.Message }); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
    catch (OperationScopeConflictException) { return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." }); }
  }

  private static async Task<IResult> RecordAsync(
    RecordTreadmillMaintenanceRequest request,
    ITreadmillMaintenanceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    string normalizedNote = request.Note?.Trim() ?? string.Empty;
    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      request.PerformedAtUtc,
      Note = normalizedNote,
      request.ExpectedPolicyVersion,
    });
    DateTimeOffset now = timeProvider.GetUtcNow();
    var operation = new PersistenceWriteOperation(
      request.OperationId,
      "treadmill.maintenance.record",
      StatusCodes.Status200OK,
      "{}",
      now,
      fingerprint);
    try
    {
      return TypedResults.Ok(await store.RecordAsync(
        request.PerformedAtUtc,
        request.Note,
        request.ExpectedPolicyVersion,
        operation,
        now,
        cancellationToken));
    }
    catch (Exception exception) when (TryReplay(exception, operation, out TreadmillMaintenanceSnapshot? replay))
    {
      return TypedResults.Ok(replay!);
    }
    catch (ArgumentException exception) { return TypedResults.BadRequest(new { error = exception.Message }); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
    catch (OperationScopeConflictException) { return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." }); }
  }

  private static bool TryReplay(
    Exception exception,
    PersistenceWriteOperation operation,
    out TreadmillMaintenanceSnapshot? snapshot)
  {
    snapshot = null;
    if (exception is not OperationReplayException replay ||
        replay.Receipt.OperationType != operation.OperationType ||
        replay.Receipt.RequestFingerprint != operation.RequestFingerprint)
      return false;
    snapshot = JsonSerializer.Deserialize<TreadmillMaintenanceSnapshot>(replay.Receipt.OutcomeJson, JsonOptions);
    return snapshot is not null;
  }
}
