namespace TreadmillRunner.Gateway.Security;

public static class OperatorAccessEndpoints
{
  public static IEndpointRouteBuilder MapOperatorAccess(this IEndpointRouteBuilder endpoints)
  {
    endpoints.MapGet("/api/operator/status", static (HttpRequest request, OperatorAccessService access) =>
    {
      bool authenticated = access.IsAuthenticated(request.Headers.Authorization.ToString(), out DateTimeOffset? expiresAtUtc);
      return TypedResults.Ok(new OperatorStatusResponse(access.Enabled, authenticated, expiresAtUtc));
    });
    endpoints.MapPost("/api/operator/login", static (OperatorLoginRequest request, HttpContext context, OperatorAccessService access) =>
    {
      string peer = context.Connection.RemoteIpAddress?.ToString() ?? "unknown";
      OperatorLoginResult result = access.Login(request.Passphrase, peer);
      if (result.RateLimited) return Results.Json(new { error = result.Error }, statusCode: StatusCodes.Status429TooManyRequests);
      if (!result.Succeeded) return Results.Json(new { error = result.Error }, statusCode: StatusCodes.Status401Unauthorized);
      return Results.Ok(new OperatorLoginResponse(result.Token!, result.ExpiresAtUtc!.Value));
    });
    endpoints.MapPost("/api/operator/logout", static (HttpRequest request, OperatorAccessService access) =>
    {
      access.Logout(request.Headers.Authorization.ToString());
      return TypedResults.NoContent();
    });
    return endpoints;
  }
}

public sealed record OperatorLoginRequest(string? Passphrase);
public sealed record OperatorLoginResponse(string Token, DateTimeOffset ExpiresAtUtc);
public sealed record OperatorStatusResponse(bool Enabled, bool Authenticated, DateTimeOffset? ExpiresAtUtc);
