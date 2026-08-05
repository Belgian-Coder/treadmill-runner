using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Design;

namespace TreadmillRunner.Infrastructure.Persistence;

public static class TreadmillRunnerDatabase
{
  public static IDbContextFactory<TreadmillRunnerDbContext> CreateFactory(
    string databasePath,
    bool pooling = true) =>
    new SqliteTreadmillRunnerDbContextFactory(databasePath, pooling);
}

public sealed class SqliteTreadmillRunnerDbContextFactory :
  IDbContextFactory<TreadmillRunnerDbContext>
{
  private readonly DbContextOptions<TreadmillRunnerDbContext> _options;

  public SqliteTreadmillRunnerDbContextFactory(string databasePath, bool pooling = true)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(databasePath);
    DatabasePath = Path.GetFullPath(databasePath);

    var connectionString = new SqliteConnectionStringBuilder
    {
      DataSource = DatabasePath,
      Mode = SqliteOpenMode.ReadWriteCreate,
      Cache = SqliteCacheMode.Shared,
      ForeignKeys = true,
      DefaultTimeout = 5,
      Pooling = pooling,
    }.ToString();

    // Journal mode is database-wide and persistent. Set it once while the factory is
    // created instead of renegotiating it whenever a pooled connection opens; doing
    // the latter can contend with an active writer and produce avoidable SQLITE_BUSY
    // failures. Foreign keys and the busy timeout remain per-connection interceptors.
    using (var connection = new SqliteConnection(connectionString))
    {
      connection.Open();
      using var command = connection.CreateCommand();
      command.CommandText = "PRAGMA journal_mode=WAL;";
      command.ExecuteNonQuery();
    }

    _options = new DbContextOptionsBuilder<TreadmillRunnerDbContext>()
      .UseSqlite(connectionString)
      .AddInterceptors(SqlitePragmaConnectionInterceptor.Instance)
      .Options;
  }

  public string DatabasePath { get; }

  public TreadmillRunnerDbContext CreateDbContext() => new(_options);

  public Task<TreadmillRunnerDbContext> CreateDbContextAsync(
    CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    return Task.FromResult(CreateDbContext());
  }
}

public sealed class TreadmillRunnerDesignTimeDbContextFactory :
  IDesignTimeDbContextFactory<TreadmillRunnerDbContext>
{
  public TreadmillRunnerDbContext CreateDbContext(string[] args)
  {
    var databasePath = args.Length > 0
      ? args[0]
      : Path.Combine(Path.GetTempPath(), "treadmillrunner-design.db");
    return TreadmillRunnerDatabase.CreateFactory(databasePath).CreateDbContext();
  }
}
