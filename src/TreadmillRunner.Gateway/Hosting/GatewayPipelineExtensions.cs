using System.Diagnostics;
using TreadmillRunner.Core.System;
using TreadmillRunner.Gateway.Diagnostics;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Web.Runtime;

namespace TreadmillRunner.Gateway.Hosting;

public static class GatewayPipelineExtensions
{
  public const string CorrelationHeaderName = "X-Correlation-ID";

  public static IApplicationBuilder UseTreadmillRunnerRequestTelemetry(this IApplicationBuilder app) =>
    app.Use(async (context, next) =>
    {
      string correlationId = ReadCorrelationId(context.Request.Headers[CorrelationHeaderName].ToString())
        ?? Activity.Current?.TraceId.ToString()
        ?? Guid.NewGuid().ToString("N");
      context.TraceIdentifier = correlationId;
      context.Response.Headers[CorrelationHeaderName] = correlationId;
      long started = Stopwatch.GetTimestamp();
      int statusCode = StatusCodes.Status500InternalServerError;
      try
      {
        await next();
        statusCode = context.Response.StatusCode;
      }
      catch
      {
        statusCode = StatusCodes.Status500InternalServerError;
        throw;
      }
      finally
      {
        context.RequestServices.GetRequiredService<OperationalTelemetry>().RecordRequest(
          context.Request.Method,
          context.Request.Path,
          statusCode,
          Stopwatch.GetElapsedTime(started));
      }
    });

  public static IApplicationBuilder UseClientBuildContract(this IApplicationBuilder app) =>
    app.Use(async (context, next) =>
    {
      bool mutation = IsMutation(context.Request.Method);
      bool browserApiMutation = mutation && context.Request.Path.StartsWithSegments("/api", StringComparison.OrdinalIgnoreCase) &&
        context.Request.Headers.ContainsKey("Sec-Fetch-Site");
      bool hasFingerprint = context.Request.Headers.TryGetValue(ClientRuntimeState.HeaderName, out var fingerprint);
      if (mutation && (browserApiMutation || hasFingerprint) &&
          (!hasFingerprint || !string.Equals(fingerprint.ToString(), AppBuildInfo.Fingerprint, StringComparison.Ordinal)))
      {
        context.Response.StatusCode = StatusCodes.Status409Conflict;
        context.Response.Headers["X-TreadmillRunner-Server-Build"] = AppBuildInfo.Fingerprint;
        await context.Response.WriteAsJsonAsync(new
        {
          type = "https://treadmillrunner.local/problems/client-update-required",
          title = "Client update required",
          status = StatusCodes.Status409Conflict,
          code = "ClientUpdateRequired",
          detail = "Reload the application before changing state.",
        });
        return;
      }
      await next();
    });

  public static IApplicationBuilder UseTreadmillRunnerNoStorePolicy(this IApplicationBuilder app) =>
    app.Use(async (context, next) =>
    {
      string path = context.Request.Path.Value ?? string.Empty;
      if (ShouldDisableCaching(path))
      {
        context.Response.OnStarting(static state =>
        {
          HttpResponse response = (HttpResponse)state;
          response.Headers.CacheControl = "no-store";
          response.Headers.Pragma = "no-cache";
          response.Headers.Expires = "0";
          return Task.CompletedTask;
        }, context.Response);
      }
      await next();
    });

  public static IApplicationBuilder UseMaintenanceMutationGate(this IApplicationBuilder app) =>
    app.Use(async (context, next) =>
    {
      IApplicationMaintenanceState maintenance = context.RequestServices.GetRequiredService<IApplicationMaintenanceState>();
      bool mutation = IsMutation(context.Request.Method);
      bool maintenanceRequest = context.Request.Path.Equals("/api/operations/restore/confirm", StringComparison.OrdinalIgnoreCase) ||
        context.Request.Path.Equals("/api/updates/activate", StringComparison.OrdinalIgnoreCase) ||
        context.Request.Path.Equals("/api/operations/database/check", StringComparison.OrdinalIgnoreCase);
      if (mutation && maintenanceRequest)
      {
        if (maintenance.IsActive)
        {
          context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
          await context.Response.WriteAsJsonAsync(new { error = "The application is completing an idle maintenance operation; mutations are temporarily unavailable." });
          return;
        }
        await next();
        return;
      }

      if (mutation)
      {
        if (!maintenance.TryBeginMutation())
        {
          context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
          await context.Response.WriteAsJsonAsync(new { error = "The application is completing an idle maintenance operation; mutations are temporarily unavailable." });
          return;
        }
        try
        {
          await next();
          if (context.Response.StatusCode < StatusCodes.Status400BadRequest && IsPlanningMutation(context.Request.Path))
          {
            context.RequestServices.GetRequiredService<GarminSyncWorker>().Wake();
          }
        }
        finally
        {
          maintenance.EndMutation();
        }
        return;
      }

      await next();
    });

  private static bool IsMutation(string method) =>
    !HttpMethods.IsGet(method) && !HttpMethods.IsHead(method) && !HttpMethods.IsOptions(method);

  private static bool IsPlanningMutation(PathString path) =>
    path.StartsWithSegments("/api/planning/workouts", StringComparison.OrdinalIgnoreCase) ||
    path.StartsWithSegments("/api/planning/programs", StringComparison.OrdinalIgnoreCase) ||
    path.StartsWithSegments("/api/planning/calendar", StringComparison.OrdinalIgnoreCase);

  private static string? ReadCorrelationId(string value)
  {
    if (value.Length is < 8 or > 64) return null;
    return value.All(static character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_') ? value : null;
  }

  private static bool ShouldDisableCaching(string path) =>
    path.StartsWith("/api/", StringComparison.OrdinalIgnoreCase) ||
    path.StartsWith("/hubs/", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/manifest.webmanifest", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/pwa-shell.js", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/runner-sound.js", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/service-worker.js", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/offline.html", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/apple-touch-icon-180.png", StringComparison.OrdinalIgnoreCase) ||
    path.StartsWith("/app-icon-", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/_framework/blazor.boot.json", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/_framework/blazor.web.js", StringComparison.OrdinalIgnoreCase) ||
    path.Equals("/_framework/resource-collection.js", StringComparison.OrdinalIgnoreCase) ||
    string.IsNullOrEmpty(Path.GetExtension(path));
}
