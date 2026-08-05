using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed class SqliteOnlineBackupService(IDbContextFactory<TreadmillRunnerDbContext> contextFactory)
{
  public async Task BackupAsync(string destinationPath, CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(destinationPath);
    var fullDestinationPath = Path.GetFullPath(destinationPath);
    var directory = Path.GetDirectoryName(fullDestinationPath)!;
    Directory.CreateDirectory(directory);

    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await context.Database.OpenConnectionAsync(cancellationToken);
    var source = (SqliteConnection)context.Database.GetDbConnection();
    if (string.Equals(Path.GetFullPath(source.DataSource), fullDestinationPath, StringComparison.OrdinalIgnoreCase))
    {
      throw new ArgumentException("Backup destination must differ from the live database.", nameof(destinationPath));
    }

    var destinationConnectionString = new SqliteConnectionStringBuilder
    {
      DataSource = fullDestinationPath,
      Mode = SqliteOpenMode.ReadWriteCreate,
      Pooling = false,
    }.ToString();
    await using var destination = new SqliteConnection(destinationConnectionString);
    await destination.OpenAsync(cancellationToken);
    cancellationToken.ThrowIfCancellationRequested();
    source.BackupDatabase(destination);
    cancellationToken.ThrowIfCancellationRequested();
  }
}
