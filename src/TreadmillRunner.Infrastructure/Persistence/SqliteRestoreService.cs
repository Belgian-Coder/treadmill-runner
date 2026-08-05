using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using System.Text;
using System.Text.Json;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed class SqliteRestoreService(
  IDbContextFactory<TreadmillRunnerDbContext> liveContextFactory)
{
  public async Task RestoreAsync(string candidatePath, CancellationToken cancellationToken = default)
  {
    string candidate = Path.GetFullPath(candidatePath);
    if (!System.IO.File.Exists(candidate)) throw new FileNotFoundException("Restore candidate was not found.", candidate);
    string rollback = Path.Combine(Path.GetTempPath(), $"treadmillrunner-rollback-{Guid.NewGuid():N}.db");
    try
    {
      IDbContextFactory<TreadmillRunnerDbContext> candidateFactory = TreadmillRunnerDatabase.CreateFactory(
        candidate,
        pooling: false);
      await using (TreadmillRunnerDbContext candidateContext = await candidateFactory.CreateDbContextAsync(cancellationToken))
      {
        await candidateContext.Database.MigrateAsync(cancellationToken);
        if (!string.Equals(await IntegrityAsync(candidateContext, cancellationToken), "ok", StringComparison.OrdinalIgnoreCase))
          throw new InvalidDataException("The restore candidate failed SQLite integrity validation.");
        await ValidateSemanticAsync(candidateContext, cancellationToken);
      }

      var backup = new SqliteOnlineBackupService(liveContextFactory);
      await backup.BackupAsync(rollback, cancellationToken);
      try
      {
        await CopyDatabaseAsync(candidate, liveContextFactory, cancellationToken);
        await using TreadmillRunnerDbContext verified = await liveContextFactory.CreateDbContextAsync(cancellationToken);
        if (!string.Equals(await IntegrityAsync(verified, cancellationToken), "ok", StringComparison.OrdinalIgnoreCase))
          throw new InvalidDataException("The restored live database failed integrity validation.");
        await ValidateSemanticAsync(verified, cancellationToken);
      }
      catch
      {
        await CopyDatabaseAsync(rollback, liveContextFactory, CancellationToken.None);
        throw;
      }
    }
    finally
    {
      SqliteConnection.ClearAllPools();
      if (System.IO.File.Exists(rollback)) System.IO.File.Delete(rollback);
    }
  }

  public static async Task<string> IntegrityAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken = default)
  {
    await context.Database.OpenConnectionAsync(cancellationToken);
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = "PRAGMA integrity_check;";
    object? value = await command.ExecuteScalarAsync(cancellationToken);
    return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) ?? string.Empty;
  }

  public static async Task ValidateSemanticAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken = default)
  {
    if ((await context.Database.GetPendingMigrationsAsync(cancellationToken)).Any())
      throw new InvalidDataException("The restored database is not on the current application schema.");
    await context.Database.OpenConnectionAsync(cancellationToken);
    string[] tables = context.Model.GetEntityTypes()
      .Select(static entity => entity.GetTableName())
      .Where(static name => !string.IsNullOrWhiteSpace(name))
      .Distinct(StringComparer.Ordinal)
      .Cast<string>()
      .ToArray();
    foreach (string table in tables)
    {
      await using var command = context.Database.GetDbConnection().CreateCommand();
      command.CommandText = $"SELECT 1 FROM \"{table.Replace("\"", "\"\"")}\" LIMIT 1;";
      try { await command.ExecuteScalarAsync(cancellationToken); }
      catch (SqliteException exception)
      {
        throw new InvalidDataException($"The restored database cannot query required table '{table}'.", exception);
      }
    }

    await ValidateForeignKeysAsync(context, cancellationToken);
    await ValidateJsonContractsAsync(context, cancellationToken);
  }

  private static async Task ValidateForeignKeysAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken)
  {
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = "PRAGMA foreign_key_check;";
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    if (await reader.ReadAsync(cancellationToken))
      throw new InvalidDataException("The restored database contains invalid foreign-key references.");
  }

  private static async Task ValidateJsonContractsAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken)
  {
    await ValidateJsonColumnAsync(context, "DeviceEnrollments", "CapabilitiesJson", allowNull: true, cancellationToken);
    await ValidateJsonColumnAsync(context, "ImportAudits", "WarningSummaryJson", allowNull: false, cancellationToken);
    await ValidateJsonColumnAsync(context, "WorkoutSessions", "ControllerConfigurationJson", allowNull: false, cancellationToken);
    await ValidateJsonColumnAsync(context, "SessionEvents", "DetailsJson", allowNull: false, cancellationToken);
    await ValidateJsonColumnAsync(context, "OperationReceipts", "OutcomeJson", allowNull: false, cancellationToken);

    string[] definitions = await context.WorkoutRevisions.AsNoTracking()
      .Select(static revision => revision.DefinitionJson)
      .ToArrayAsync(cancellationToken);
    var importer = new NativeWorkoutJsonImporter();
    foreach (string definition in definitions)
    {
      byte[] bytes = Encoding.UTF8.GetBytes(definition);
      if (bytes.Length > WorkoutImportLimits.MaximumBytes)
        throw new InvalidDataException("A restored workout definition exceeds the supported size limit.");
      try
      {
        await using var stream = new MemoryStream(bytes, writable: false);
        _ = await importer.ImportAsync(stream, "restored-workout.json", cancellationToken);
      }
      catch (Exception exception) when (exception is WorkoutImportException or JsonException or ArgumentException)
      {
        throw new InvalidDataException("A restored workout definition is not a valid supported workout contract.", exception);
      }
    }
  }

  private static async Task ValidateJsonColumnAsync(
    TreadmillRunnerDbContext context,
    string table,
    string column,
    bool allowNull,
    CancellationToken cancellationToken)
  {
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = $"SELECT \"{column}\" FROM \"{table}\";";
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    while (await reader.ReadAsync(cancellationToken))
    {
      if (reader.IsDBNull(0))
      {
        if (allowNull) continue;
        throw new InvalidDataException($"Restored {table}.{column} contains a null JSON contract.");
      }

      string json = reader.GetString(0);
      try
      {
        using JsonDocument document = JsonDocument.Parse(json, new JsonDocumentOptions
        {
          AllowTrailingCommas = false,
          CommentHandling = JsonCommentHandling.Disallow,
          MaxDepth = 64,
        });
      }
      catch (JsonException exception)
      {
        throw new InvalidDataException($"Restored {table}.{column} contains malformed JSON.", exception);
      }
    }
  }

  private static async Task CopyDatabaseAsync(
    string sourcePath,
    IDbContextFactory<TreadmillRunnerDbContext> destinationFactory,
    CancellationToken cancellationToken)
  {
    var sourceBuilder = new SqliteConnectionStringBuilder
    {
      DataSource = sourcePath,
      Mode = SqliteOpenMode.ReadOnly,
      ForeignKeys = true,
      DefaultTimeout = 5,
    };
    await using var source = new SqliteConnection(sourceBuilder.ToString());
    await source.OpenAsync(cancellationToken);
    await using TreadmillRunnerDbContext destinationContext = await destinationFactory.CreateDbContextAsync(cancellationToken);
    await destinationContext.Database.OpenConnectionAsync(cancellationToken);
    if (destinationContext.Database.GetDbConnection() is not SqliteConnection destination)
      throw new InvalidOperationException("The live database is not SQLite.");
    source.BackupDatabase(destination);
  }
}
