namespace TreadmillRunner.Gateway.Diagnostics;

public static class OperationalTelemetryEndpoints
{
  public static IEndpointRouteBuilder MapOperationalTelemetry(this IEndpointRouteBuilder endpoints)
  {
    endpoints.MapGet("/api/operations/telemetry", static (OperationalTelemetry telemetry) =>
      TypedResults.Ok(telemetry.Snapshot()));
    return endpoints;
  }
}
