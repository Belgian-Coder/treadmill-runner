using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Hosting;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Health;

public sealed class DatabaseReadyHealthCheck(
  IDatabaseIntegrityCoordinator integrity,
  IHostEnvironment environment) : IHealthCheck
{
  public async Task<HealthCheckResult> CheckHealthAsync(
    HealthCheckContext context,
    CancellationToken cancellationToken = default)
  {
    try
    {
      DatabaseIntegrityStatus status = integrity.Current;
      if (status.State is DatabaseIntegrityState.Healthy or DatabaseIntegrityState.HealthyWithBackupWarning)
      {
        string message = status.State == DatabaseIntegrityState.HealthyWithBackupWarning
          ? status.Message
          : "The database is current according to the cached integrity status.";
        return HealthCheckResult.Healthy(message);
      }

      string description = status.Issues.FirstOrDefault() ?? status.Message;
      if (status.State is DatabaseIntegrityState.Checking or DatabaseIntegrityState.Deferred or DatabaseIntegrityState.NotChecked)
        return HealthCheckResult.Degraded(description);
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
