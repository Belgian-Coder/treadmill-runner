using System.Net;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminActivityConnectRequest(string Email, string Password, bool Enabled = false);
public sealed record GarminActivityMfaRequest(Guid ChallengeId, string Code);
public sealed record GarminActivityEnabledRequest(bool Enabled, int ExpectedVersion);
public sealed record GarminActivityDisconnectRequest(int ExpectedVersion);
public sealed record GarminActivityUploadStatusResponse(
  Guid ProfileId,
  bool Connected,
  bool Enabled,
  string? AccountLabel,
  string State,
  int Pending,
  int Confirmed,
  int Failed,
  int Unknown,
  DateTimeOffset? LastSuccessAtUtc,
  string? LastError,
  int? Version,
  string AdapterState,
  string AdapterMessage,
  bool CanConnect);

public static class GarminActivityUploadEndpoints
{
  public static IEndpointRouteBuilder MapGarminActivityUpload(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/integrations/garmin/activity-upload");
    group.MapGet("/profiles/{profileId:guid}/status", GetStatusAsync);
    group.MapGet("/profiles/{profileId:guid}/jobs", GetJobsAsync);
    group.MapPost("/profiles/{profileId:guid}/connect", ConnectAsync);
    group.MapPost("/profiles/{profileId:guid}/mfa", CompleteMfaAsync);
    group.MapPost("/profiles/{profileId:guid}/enabled", SetEnabledAsync);
    group.MapPost("/profiles/{profileId:guid}/disconnect", DisconnectAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/retry", RetryAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/dismiss", DismissAsync);
    return endpoints;
  }

  private static async Task<IResult> GetStatusAsync(
    Guid profileId,
    IGarminActivityUploadStore store,
    IGarminActivityAdapterReadiness readiness,
    CancellationToken cancellationToken) =>
    TypedResults.Ok(await StatusAsync(profileId, store, readiness, cancellationToken));

  private static async Task<IResult> GetJobsAsync(Guid profileId, IGarminActivityUploadStore store, CancellationToken cancellationToken) =>
    TypedResults.Ok(await store.ListJobsAsync(profileId, cancellationToken));

  private static async Task<IResult> ConnectAsync(
    Guid profileId,
    GarminActivityConnectRequest request,
    HttpContext context,
    ILiveSessionCoordinator sessions,
    IGarminActivityAdapterReadiness readiness,
    GarminActivityConnectionService service,
    CancellationToken cancellationToken)
  {
    if (!IsProtectedCredentialRequest(context))
      return Results.Json(new { error = "Enter Garmin credentials only over HTTPS or from a browser on the NUC itself." }, statusCode: StatusCodes.Status426UpgradeRequired);
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Connect Garmin activity upload only while no run is active." });
    GarminAdapterReadiness adapter = await readiness.CheckAsync(cancellationToken);
    if (!adapter.CanConnect)
      return Results.Json(new { error = adapter.Message, adapterState = adapter.State }, statusCode: StatusCodes.Status503ServiceUnavailable);
    if (string.IsNullOrWhiteSpace(request.Email) || request.Email.Length > 254 || string.IsNullOrEmpty(request.Password) || request.Password.Length > 512)
      return TypedResults.BadRequest(new { error = "A bounded Garmin email and password are required for this one-time login." });
    try { return TypedResults.Ok(await service.BeginAsync(profileId, request.Email.Trim(), request.Password, request.Enabled, cancellationToken)); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (Exception) { return TypedResults.Conflict(new { error = "The unsupported Garmin provider could not authenticate. Verify the adapter installation and account details." }); }
  }

  private static async Task<IResult> CompleteMfaAsync(
    Guid profileId,
    GarminActivityMfaRequest request,
    HttpContext context,
    ILiveSessionCoordinator sessions,
    GarminActivityConnectionService service,
    CancellationToken cancellationToken)
  {
    if (!IsProtectedCredentialRequest(context)) return Results.StatusCode(StatusCodes.Status426UpgradeRequired);
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Complete Garmin login only while no run is active." });
    if (request.Code.Length is < 4 or > 16) return TypedResults.BadRequest(new { error = "A valid verification code is required." });
    try { return TypedResults.Ok(await service.CompleteMfaAsync(profileId, request.ChallengeId, request.Code, cancellationToken)); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (Exception) { return TypedResults.Conflict(new { error = "Garmin verification failed. Start the connection again." }); }
  }

  private static async Task<IResult> SetEnabledAsync(Guid profileId, GarminActivityEnabledRequest request, ILiveSessionCoordinator sessions, IGarminActivityUploadStore store, IGarminActivityAdapterReadiness readiness, GarminActivityUploadWorker worker, TimeProvider timeProvider, CancellationToken cancellationToken)
  {
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Change Garmin upload settings only while no run is active." });
    try
    {
      GarminActivityUploadAccount account = await store.SetEnabledAsync(profileId, request.Enabled, request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken);
      if (account.Enabled) worker.Wake();
      return TypedResults.Ok(await StatusAsync(profileId, store, readiness, cancellationToken));
    }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> DisconnectAsync(Guid profileId, GarminActivityDisconnectRequest request, ILiveSessionCoordinator sessions, IGarminActivityUploadStore store, CancellationToken cancellationToken)
  {
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Disconnect Garmin upload only while no run is active." });
    try { return await store.DisconnectAsync(profileId, request.ExpectedVersion, cancellationToken) ? TypedResults.NoContent() : TypedResults.NotFound(); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> RetryAsync(Guid profileId, Guid jobId, IGarminActivityUploadStore store, GarminActivityUploadWorker worker, TimeProvider timeProvider, CancellationToken cancellationToken)
  {
    bool changed = await store.RetryFailedAsync(jobId, profileId, timeProvider.GetUtcNow(), cancellationToken);
    if (changed) worker.Wake();
    return changed ? Results.Accepted() : TypedResults.NotFound();
  }

  private static async Task<IResult> DismissAsync(Guid profileId, Guid jobId, IGarminActivityUploadStore store, TimeProvider timeProvider, CancellationToken cancellationToken) =>
    await store.DismissAsync(jobId, profileId, timeProvider.GetUtcNow(), cancellationToken) ? TypedResults.NoContent() : TypedResults.NotFound();

  private static bool HasActiveRun(ILiveSessionCoordinator sessions) => sessions.CurrentSession?.Live.SessionState is
    SessionState.ArmedWaitingForPhysicalStart or SessionState.Running or SessionState.PausedWaitingForPhysicalResume;

  private static bool IsProtectedCredentialRequest(HttpContext context) => context.Request.IsHttps ||
    (context.Connection.RemoteIpAddress is { } address && IPAddress.IsLoopback(address));

  private static async Task<GarminActivityUploadStatusResponse> StatusAsync(
    Guid profileId,
    IGarminActivityUploadStore store,
    IGarminActivityAdapterReadiness readiness,
    CancellationToken cancellationToken)
  {
    GarminActivityUploadStatus status = await store.GetStatusAsync(profileId, cancellationToken);
    GarminAdapterReadiness adapter = await readiness.CheckAsync(cancellationToken);
    return new(
      status.ProfileId,
      status.Connected,
      status.Enabled,
      status.AccountLabel,
      status.State,
      status.Pending,
      status.Confirmed,
      status.Failed,
      status.Unknown,
      status.LastSuccessAtUtc,
      status.LastError,
      status.Version,
      adapter.State,
      adapter.Message,
      adapter.CanConnect);
  }
}
