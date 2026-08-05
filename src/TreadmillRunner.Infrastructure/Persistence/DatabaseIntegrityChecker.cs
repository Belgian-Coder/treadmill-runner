using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public enum DatabaseIntegrityCheckLevel
{
  Quick,
  Full,
}

public sealed record DatabaseIntegrityCheckResult(
  DatabaseIntegrityCheckLevel Level,
  bool IsHealthy,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset CompletedAtUtc,
  IReadOnlyList<string> Issues);

public sealed record DatabaseMaintenanceResult(
  DateTimeOffset CompletedAtUtc,
  IReadOnlyList<string> CompletedActions,
  IReadOnlyList<string> Issues);

public interface IDatabaseIntegrityChecker
{
  Task<DatabaseIntegrityCheckResult> CheckAsync(
    DatabaseIntegrityCheckLevel level,
    CancellationToken cancellationToken = default);

  Task<DatabaseMaintenanceResult> RunSafeMaintenanceAsync(
    CancellationToken cancellationToken = default);
}

public sealed class DatabaseIntegrityChecker(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
  TimeProvider timeProvider) : IDatabaseIntegrityChecker
{
  private const int MaximumReportedIssues = 100;

  public async Task<DatabaseIntegrityCheckResult> CheckAsync(
    DatabaseIntegrityCheckLevel level,
    CancellationToken cancellationToken = default)
  {
    DateTimeOffset startedAt = timeProvider.GetUtcNow();
    var issues = new List<string>();
    try
    {
      await using TreadmillRunnerDbContext context =
        await contextFactory.CreateDbContextAsync(cancellationToken);
      if (!await context.Database.CanConnectAsync(cancellationToken))
      {
        issues.Add("The application database cannot be opened.");
      }
      else
      {
        string[] pendingMigrations = (await context.Database
          .GetPendingMigrationsAsync(cancellationToken))
          .Take(MaximumReportedIssues)
          .ToArray();
        if (pendingMigrations.Length != 0)
        {
          issues.Add($"The database has {pendingMigrations.Length} pending reviewed migration(s).");
        }

        await context.Database.OpenConnectionAsync(cancellationToken);
        await CollectCheckIssuesAsync(context, level, issues, cancellationToken);
        if (level == DatabaseIntegrityCheckLevel.Full && issues.Count == 0)
        {
          try
          {
            await SqliteRestoreService.ValidateSemanticAsync(context, cancellationToken);
          }
          catch (InvalidDataException exception)
          {
            issues.Add(exception.Message);
          }
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception) when (exception is SqliteException or InvalidDataException or IOException)
    {
      issues.Add(SafeFailureMessage(exception));
    }

    return new DatabaseIntegrityCheckResult(
      level,
      issues.Count == 0,
      startedAt,
      timeProvider.GetUtcNow(),
      issues.Take(MaximumReportedIssues).ToArray());
  }

  public async Task<DatabaseMaintenanceResult> RunSafeMaintenanceAsync(
    CancellationToken cancellationToken = default)
  {
    var actions = new List<string>();
    var issues = new List<string>();
    try
    {
      await using TreadmillRunnerDbContext context =
        await contextFactory.CreateDbContextAsync(cancellationToken);
      await context.Database.OpenConnectionAsync(cancellationToken);

      await using (var checkpoint = context.Database.GetDbConnection().CreateCommand())
      {
        checkpoint.CommandText = "PRAGMA wal_checkpoint(PASSIVE);";
        await checkpoint.ExecuteNonQueryAsync(cancellationToken);
        actions.Add("WAL checkpoint");
      }

      await using (var optimize = context.Database.GetDbConnection().CreateCommand())
      {
        optimize.CommandText = "PRAGMA optimize;";
        await optimize.ExecuteNonQueryAsync(cancellationToken);
        actions.Add("SQLite optimize");
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception) when (exception is SqliteException or IOException)
    {
      issues.Add(SafeFailureMessage(exception));
    }

    return new DatabaseMaintenanceResult(
      timeProvider.GetUtcNow(),
      actions,
      issues);
  }

  private static async Task CollectCheckIssuesAsync(
    TreadmillRunnerDbContext context,
    DatabaseIntegrityCheckLevel level,
    List<string> issues,
    CancellationToken cancellationToken)
  {
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = level == DatabaseIntegrityCheckLevel.Quick
      ? $"PRAGMA quick_check({MaximumReportedIssues});"
      : $"PRAGMA integrity_check({MaximumReportedIssues});";
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    while (issues.Count < MaximumReportedIssues && await reader.ReadAsync(cancellationToken))
    {
      string message = reader.IsDBNull(0) ? string.Empty : reader.GetString(0);
      if (!string.Equals(message, "ok", StringComparison.OrdinalIgnoreCase))
      {
        issues.Add(string.IsNullOrWhiteSpace(message)
          ? "SQLite returned an empty integrity error."
          : message[..Math.Min(message.Length, 500)]);
      }
    }
  }

  private static string SafeFailureMessage(Exception exception) => exception switch
  {
    SqliteException => "SQLite could not complete the integrity operation.",
    InvalidDataException => exception.Message,
    IOException => "The database integrity operation could not access its local files.",
    _ => "The database integrity operation failed.",
  };
}
