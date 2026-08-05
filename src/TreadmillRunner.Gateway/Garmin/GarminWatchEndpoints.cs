using System.Security.Cryptography;
using System.Text;
using System.Net;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminWatchPairRequest(string DeviceLabel);
public sealed record GarminWatchRevokeRequest(int ExpectedVersion);

public static class GarminWatchEndpoints
{
  public static IEndpointRouteBuilder MapGarminWatch(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder setup = endpoints.MapGroup("/api/integrations/garmin/watch");
    setup.MapGet("/profiles/{profileId:guid}", GetBindingAsync);
    setup.MapPost("/profiles/{profileId:guid}", PairAsync);
    setup.MapPost("/profiles/{profileId:guid}/revoke", RevokeAsync);
    endpoints.MapGet("/api/watch/status", GetWatchStatusAsync);
    return endpoints;
  }

  private static async Task<IResult> GetBindingAsync(
    Guid profileId,
    IGarminWatchBindingStore store,
    CancellationToken cancellationToken)
  {
    GarminWatchBinding? binding = await store.FindForProfileAsync(profileId, cancellationToken);
    return binding is null ? TypedResults.NoContent() : TypedResults.Ok(binding);
  }

  private static async Task<IResult> PairAsync(
    Guid profileId,
    GarminWatchPairRequest request,
    HttpContext context,
    ILiveSessionCoordinator sessions,
    IGarminWatchBindingStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (!context.Request.IsHttps && (context.Connection.RemoteIpAddress is not { } address || !IPAddress.IsLoopback(address)))
      return Results.Json(new { error = "Create a watch token only over HTTPS or from a browser on the NUC itself." }, statusCode: StatusCodes.Status426UpgradeRequired);
    if (sessions.CurrentSession?.Live.SessionState is SessionState.ArmedWaitingForPhysicalStart or
        SessionState.Running or SessionState.PausedWaitingForPhysicalResume)
      return TypedResults.Conflict(new { error = "Watch pairing is available only while no run is active." });
    byte[] secret = RandomNumberGenerator.GetBytes(32);
    string token = WebEncoders.Base64UrlEncode(secret);
    string hash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(token)));
    try
    {
      GarminWatchBinding binding = await store.ReplaceAsync(
        profileId,
        request.DeviceLabel,
        hash,
        timeProvider.GetUtcNow(),
        cancellationToken);
      return TypedResults.Ok(new
      {
        binding,
        token,
        message = "Copy this token now. TreadmillRunner stores only its SHA-256 hash and cannot show it again.",
      });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.NotFound(new { error = exception.Message });
    }
    finally
    {
      CryptographicOperations.ZeroMemory(secret);
    }
  }

  private static async Task<IResult> RevokeAsync(
    Guid profileId,
    GarminWatchRevokeRequest request,
    ILiveSessionCoordinator sessions,
    IGarminWatchBindingStore store,
    CancellationToken cancellationToken)
  {
    if (sessions.CurrentSession?.Live.SessionState is SessionState.ArmedWaitingForPhysicalStart or
        SessionState.Running or SessionState.PausedWaitingForPhysicalResume)
      return TypedResults.Conflict(new { error = "Watch pairing is available only while no run is active." });
    try
    {
      return await store.RevokeAsync(profileId, request.ExpectedVersion, cancellationToken)
        ? TypedResults.NoContent()
        : TypedResults.NotFound();
    }
    catch (DbUpdateConcurrencyException exception)
    {
      return TypedResults.Conflict(new { error = exception.Message });
    }
  }

  private static async Task<IResult> GetWatchStatusAsync(
    HttpContext context,
    ILiveSessionCoordinator sessions,
    IGarminWatchBindingStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string authorization = context.Request.Headers.Authorization.ToString();
    if (!authorization.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
      return TypedResults.Unauthorized();
    string token = authorization[7..].Trim();
    if (token.Length is < 20 or > 128) return TypedResults.Unauthorized();
    string hash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(token)));
    GarminWatchBinding? binding = await store.FindByTokenHashAsync(hash, timeProvider.GetUtcNow(), cancellationToken);
    if (binding is null) return TypedResults.Unauthorized();
    ActiveSessionSnapshot? active = sessions.CurrentSession;
    bool ownsSession = active?.UserProfileId == binding.UserProfileId;
    return TypedResults.Ok(new
    {
      runnerName = binding.RunnerName,
      sessionTitle = ownsSession ? active!.WorkoutTitle : "Manual treadmill",
      state = ownsSession ? active!.Live.SessionState.ToString() : "Ready",
      sessionId = ownsSession ? active!.SessionId : (Guid?)null,
    });
  }
}
