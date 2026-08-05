using TreadmillRunner.Gateway.Live;
using Microsoft.AspNetCore.Mvc;

namespace TreadmillRunner.Gateway.Updates;

public static class UpdateEndpoints
{
  public static IEndpointRouteBuilder MapUpdates(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/updates");
    group.MapGet("/status", (UpdateManager manager) => Results.Ok(ToDto(manager.Status)));
    group.MapPost("/check", CheckAsync);
    group.MapPost("/stage", StageAsync);
    group.MapPost("/upload", UploadAsync)
      .WithMetadata(new RequestSizeLimitAttribute(UpdateManager.MaximumUploadedBundleBytes));
    group.MapPost("/activate", ActivateAsync);
    return endpoints;
  }

  private static async Task<IResult> CheckAsync(UpdateManager manager, CancellationToken cancellationToken)
  {
    try
    {
      await manager.CheckAsync(cancellationToken);
      return Results.Ok(ToDto(manager.Status));
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    { return Results.Problem("The update feed is unavailable or invalid.", statusCode: 503); }
  }

  private static async Task<IResult> StageAsync(
    StageUpdateRequest request,
    UpdateManager manager,
    ILiveSessionCoordinator live,
    CancellationToken cancellationToken)
  {
    if (!IsIdle(live)) return Results.Conflict(new { error = "Updates can be staged only while idle." });
    try
    {
      await manager.StageAsync(request.ExpectedVersion, cancellationToken);
      return Results.Ok(ToDto(manager.Status));
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    { return Results.Problem("The update could not be verified and staged.", statusCode: 503); }
  }

  private static async Task<IResult> UploadAsync(
    HttpRequest request,
    UpdateManager manager,
    ILiveSessionCoordinator live,
    CancellationToken cancellationToken)
  {
    if (!IsIdle(live)) return Results.Conflict(new { error = "Updates can be uploaded only while idle." });
    if (!string.Equals(
          request.ContentType,
          "application/vnd.treadmillrunner.update+zip",
          StringComparison.OrdinalIgnoreCase))
      return Results.BadRequest(new { error = "Upload a TreadmillRunner signed update bundle ZIP." });
    if (request.ContentLength is not { } length || length <= 0 || length > UpdateManager.MaximumUploadedBundleBytes)
      return Results.StatusCode(StatusCodes.Status413PayloadTooLarge);
    try
    {
      await manager.StageUploadedBundleAsync(request.Body, length, cancellationToken);
      return Results.Ok(ToDto(manager.Status));
    }
    catch (Exception exception) when (exception is InvalidDataException or InvalidOperationException or ArgumentException)
    {
      return Results.BadRequest(new { error = "The uploaded bundle is not a valid newer signed TreadmillRunner update." });
    }
  }

  private static async Task<IResult> ActivateAsync(
    ActivateUpdateRequest request,
    UpdateManager manager,
    ILiveSessionCoordinator live,
    CancellationToken cancellationToken)
  {
    if (!string.Equals(request.Confirmation, "ACTIVATE", StringComparison.Ordinal))
      return Results.BadRequest(new { error = "Confirmation must exactly equal ACTIVATE." });
    if (!await live.TryBeginMaintenanceAsync(cancellationToken))
      return Results.Conflict(new { error = "An update cannot start while a session is active, recovering, or another activation is in progress." });
    bool activationAccepted = false;
    try
    {
      await manager.ActivateAsync(request.ExpectedVersion, cancellationToken);
      activationAccepted = true;
      return Results.Accepted(value: new { status = ToDto(manager.Status) });
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      return Results.Problem("Update activation could not be started.", statusCode: 503);
    }
    finally
    {
      if (!activationAccepted) await live.CancelMaintenanceAsync(CancellationToken.None);
    }
  }

  private static bool IsIdle(ILiveSessionCoordinator live) => live.CurrentSession is null ||
    live.CurrentSession.Live.SessionState is TreadmillRunner.Core.Sessions.SessionState.Completed or
      TreadmillRunner.Core.Sessions.SessionState.Stopped;

  private static object ToDto(UpdateStatusSnapshot status) => new
  {
    state = status.State.ToString(),
    status.CurrentVersion,
    status.AvailableVersion,
    status.StagedVersion,
    status.ReleaseNotes,
    status.LastCheckedAtUtc,
    status.Message,
    status.FeedSource,
  };
}
