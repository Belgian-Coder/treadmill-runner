using System.Collections.Concurrent;
using System.Diagnostics;
using System.Diagnostics.Metrics;

namespace TreadmillRunner.Gateway.Diagnostics;

public sealed class OperationalTelemetry
{
  private const int MaximumRouteKeys = 64;
  private readonly ConcurrentDictionary<string, RouteAccumulator> routes = new(StringComparer.Ordinal);
  private readonly Counter<long> requests;
  private readonly Counter<long> failures;
  private readonly Counter<long> authenticationAttempts;
  private readonly Histogram<double> requestDuration;

  public OperationalTelemetry(IMeterFactory meterFactory)
  {
    Meter meter = meterFactory.Create("TreadmillRunner.Gateway");
    requests = meter.CreateCounter<long>("treadmillrunner.http.requests");
    failures = meter.CreateCounter<long>("treadmillrunner.http.failures");
    authenticationAttempts = meter.CreateCounter<long>("treadmillrunner.operator.authentication.attempts");
    requestDuration = meter.CreateHistogram<double>("treadmillrunner.http.request.duration", "ms");
  }

  public void RecordRequest(string method, PathString path, int statusCode, TimeSpan elapsed)
  {
    string route = NormalizeRoute(path);
    TagList tags = new()
    {
      { "http.request.method", method },
      { "http.route", route },
      { "http.response.status_code", statusCode },
    };
    requests.Add(1, tags);
    requestDuration.Record(elapsed.TotalMilliseconds, tags);
    if (statusCode >= StatusCodes.Status400BadRequest) failures.Add(1, tags);

    RouteAccumulator? accumulator = routes.GetValueOrDefault(route);
    if (accumulator is null && routes.Count < MaximumRouteKeys)
    {
      accumulator = routes.GetOrAdd(route, static _ => new RouteAccumulator());
    }
    accumulator?.Record(statusCode, elapsed);
  }

  public void RecordAuthentication(string outcome)
  {
    authenticationAttempts.Add(1, new TagList { { "outcome", outcome } });
  }

  public OperationalTelemetrySnapshot Snapshot()
  {
    OperationalRouteTelemetry[] routeSnapshots = routes
      .Select(static pair => pair.Value.Snapshot(pair.Key))
      .OrderByDescending(static item => item.LastObservedAtUtc)
      .ThenBy(static item => item.Route, StringComparer.Ordinal)
      .ToArray();
    return new OperationalTelemetrySnapshot(DateTimeOffset.UtcNow, routeSnapshots);
  }

  internal static string NormalizeRoute(PathString path)
  {
    string value = path.Value ?? "/";
    if (value.Length == 0 || value == "/") return "/";
    string[] segments = value.Split('/', StringSplitOptions.RemoveEmptyEntries);
    for (var index = 0; index < segments.Length; index++)
    {
      if (Guid.TryParse(segments[index], out _) || long.TryParse(segments[index], out _)) segments[index] = "{id}";
      else if (segments[index].Length > 64) segments[index] = "{value}";
    }
    return "/" + string.Join('/', segments.Take(6));
  }

  private sealed class RouteAccumulator
  {
    private readonly Lock gate = new();
    private long count;
    private long failureCount;
    private double totalMilliseconds;
    private double maximumMilliseconds;
    private int lastStatusCode;
    private DateTimeOffset lastObservedAtUtc;

    public void Record(int statusCode, TimeSpan elapsed)
    {
      lock (gate)
      {
        count++;
        if (statusCode >= StatusCodes.Status400BadRequest) failureCount++;
        totalMilliseconds += elapsed.TotalMilliseconds;
        maximumMilliseconds = Math.Max(maximumMilliseconds, elapsed.TotalMilliseconds);
        lastStatusCode = statusCode;
        lastObservedAtUtc = DateTimeOffset.UtcNow;
      }
    }

    public OperationalRouteTelemetry Snapshot(string route)
    {
      lock (gate)
      {
        return new OperationalRouteTelemetry(
          route,
          count,
          failureCount,
          count == 0 ? 0 : totalMilliseconds / count,
          maximumMilliseconds,
          lastStatusCode,
          lastObservedAtUtc);
      }
    }
  }
}

public sealed record OperationalTelemetrySnapshot(
  DateTimeOffset CapturedAtUtc,
  IReadOnlyList<OperationalRouteTelemetry> Routes);

public sealed record OperationalRouteTelemetry(
  string Route,
  long RequestCount,
  long FailureCount,
  double AverageMilliseconds,
  double MaximumMilliseconds,
  int LastStatusCode,
  DateTimeOffset LastObservedAtUtc);
