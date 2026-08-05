using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Health;

public sealed class DatabaseReadyHealthCheck(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
  IHostEnvironment environment) : IHealthCheck
{
  public async Task<HealthCheckResult> CheckHealthAsync(
    HealthCheckContext context,
    CancellationToken cancellationToken = default)
  {
    try
    {
      await using TreadmillRunnerDbContext database = await contextFactory.CreateDbContextAsync(cancellationToken);
      if (!await database.Database.CanConnectAsync(cancellationToken))
        return HealthCheckResult.Unhealthy("The application database cannot be opened.");

      IEnumerable<string> pending = await database.Database.GetPendingMigrationsAsync(cancellationToken);
      if (environment.IsProduction() && pending.Any())
        return HealthCheckResult.Unhealthy("The application database has pending reviewed migrations.");

      await database.Database.OpenConnectionAsync(cancellationToken);
      await using var command = database.Database.GetDbConnection().CreateCommand();
      command.CommandText = "PRAGMA quick_check;";
      object? result = await command.ExecuteScalarAsync(cancellationToken);
      return string.Equals(Convert.ToString(result), "ok", StringComparison.OrdinalIgnoreCase)
        ? HealthCheckResult.Healthy("The database is current and passed SQLite quick_check.")
        : HealthCheckResult.Unhealthy("The application database failed SQLite quick_check.");
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      return HealthCheckResult.Unhealthy("The application database readiness check failed.", exception);
    }
  }
}
