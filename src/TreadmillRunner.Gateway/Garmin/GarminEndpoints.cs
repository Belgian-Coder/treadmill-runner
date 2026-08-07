namespace TreadmillRunner.Gateway.Garmin;

public static class GarminEndpoints
{
  public static IEndpointRouteBuilder MapGarmin(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/integrations/garmin");
    group.MapGet("/profiles/{profileId:guid}/status", GetStatusAsync);
    group.MapPost("/profiles/{profileId:guid}/connect", StartConnectAsync);
    group.MapPost("/profiles/{profileId:guid}/sessions", SyncSessionAsync);
    group.MapPost("/profiles/{profileId:guid}/disconnect", DisconnectAsync);
    group.MapGet("/callback", CompleteConnectAsync);
    return endpoints;
  }

  private static async Task<IResult> GetStatusAsync(
    Guid profileId,
    GarminConnectionService service,
    CancellationToken cancellationToken) =>
    TypedResults.Ok(await service.GetStatusAsync(profileId, cancellationToken));

  private static async Task<IResult> StartConnectAsync(
    Guid profileId,
    HttpContext context,
    GarminConnectionService service,
    CancellationToken cancellationToken)
  {
    try
    {
      var requestBase = new Uri($"{context.Request.Scheme}://{context.Request.Host}{context.Request.PathBase}/");
      GarminConnectStart start = await service.StartConnectAsync(profileId, requestBase, cancellationToken);
      return TypedResults.Ok(new { authorizationUrl = start.AuthorizationUrl.ToString(), start.ExpiresAtUtc });
    }
    catch (KeyNotFoundException exception)
    {
      return TypedResults.NotFound(new { error = exception.Message });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> CompleteConnectAsync(
    string? code,
    string? state,
    string? error,
    GarminConnectionService service,
    CancellationToken cancellationToken)
  {
    if (!string.IsNullOrWhiteSpace(error))
    {
      return TypedResults.Redirect($"/profiles?garmin=error&reason={Uri.EscapeDataString("Garmin authorization was cancelled or denied.")}");
    }
    try
    {
      Guid profileId = await service.CompleteConnectAsync(code ?? string.Empty, state ?? string.Empty, cancellationToken);
      return TypedResults.Redirect($"/profiles?garmin=connected&profileId={profileId:D}");
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception)
    {
      return TypedResults.Redirect($"/profiles?garmin=error&reason={Uri.EscapeDataString("Garmin authorization could not be completed. Start the connection again.")}");
    }
  }

  private static async Task<IResult> SyncSessionAsync(
    Guid profileId,
    GarminSessionSyncRequest request,
    GarminConnectionService service,
    CancellationToken cancellationToken)
  {
    GarminConnectionStatus status = await service.GetStatusAsync(profileId, cancellationToken);
    if (!status.Connected) return TypedResults.Conflict(new { error = "Connect a Garmin account before syncing." });
    try
    {
      GarminSessionSyncResult result = await service.EnqueueSessionAsync(
        profileId, request.Date, request.WorkoutRevisionId, cancellationToken);
      return Results.Accepted(value: new
      {
        added = result.Added,
        message = result.Added == 0
          ? "This session is already queued or synchronized."
          : "This calendar session is queued for Garmin Connect.",
      });
    }
    catch (KeyNotFoundException exception)
    {
      return TypedResults.NotFound(new { error = exception.Message });
    }
  }

  private static async Task<IResult> DisconnectAsync(
    Guid profileId,
    GarminConnectionService service,
    CancellationToken cancellationToken)
  {
    bool removed = await service.DisconnectAsync(profileId, cancellationToken);
    return removed ? TypedResults.NoContent() : TypedResults.NotFound();
  }
}

public sealed record GarminSessionSyncRequest(DateOnly Date, Guid WorkoutRevisionId);
