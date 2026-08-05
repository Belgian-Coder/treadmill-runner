using Microsoft.Extensions.Diagnostics.HealthChecks;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Health;

public sealed class DatabaseReadyHealthCheck(
  IDatabaseIntegrityChecker integrity,
  IHostEnvironment environment) : IHealthCheck
{
  public async Task<HealthCheckResult> CheckHealthAsync(
    HealthCheckContext context,
    CancellationToken cancellationToken = default)
  {
    try
    {
      DatabaseIntegrityCheckResult result = await integrity.CheckAsync(
        DatabaseIntegrityCheckLevel.Quick,
        cancellationToken);
      if (result.IsHealthy)
      {
        return HealthCheckResult.Healthy("The database is current and passed SQLite quick_check.");
      }

      string description = result.Issues.FirstOrDefault()
        ?? "The application database failed SQLite quick_check.";
      return environment.IsProduction()
        ? HealthCheckResult.Unhealthy(description)
        : HealthCheckResult.Degraded(description);
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      return HealthCheckResult.Unhealthy("The application database readiness check failed.", exception);
    }
  }
}
