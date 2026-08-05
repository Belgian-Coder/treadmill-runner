using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class DatabaseIntegrityCheckerTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(_directory, "integrity.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory)) Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Migrated_database_passes_quick_full_and_safe_maintenance()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateMigratedFactoryAsync();
    var checker = new DatabaseIntegrityChecker(factory, TimeProvider.System);

    DatabaseIntegrityCheckResult quick = await checker.CheckAsync(DatabaseIntegrityCheckLevel.Quick);
    DatabaseMaintenanceResult maintenance = await checker.RunSafeMaintenanceAsync();
    DatabaseIntegrityCheckResult full = await checker.CheckAsync(DatabaseIntegrityCheckLevel.Full);

    Assert.True(quick.IsHealthy);
    Assert.True(full.IsHealthy);
    Assert.Empty(quick.Issues);
    Assert.Empty(full.Issues);
    Assert.Empty(maintenance.Issues);
    Assert.Equal(["WAL checkpoint", "SQLite optimize"], maintenance.CompletedActions);
  }

  [Fact]
  public async Task Full_check_reports_semantic_json_damage_without_modifying_it()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateMigratedFactoryAsync();
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      await context.Database.ExecuteSqlRawAsync("""
        INSERT INTO OperationReceipts
          (Id, ClientOperationId, OperationType, StatusCode, OutcomeJson, CreatedAtUtc, RequestFingerprint)
        VALUES
          ('11111111-1111-1111-1111-111111111111',
           '22222222-2222-2222-2222-222222222222', 'test', 200, '{{not-json',
           '2026-08-05T00:00:00+00:00', '0000000000000000000000000000000000000000000000000000000000000000');
        """);
    }

    var checker = new DatabaseIntegrityChecker(factory, TimeProvider.System);
    DatabaseIntegrityCheckResult full = await checker.CheckAsync(DatabaseIntegrityCheckLevel.Full);

    Assert.False(full.IsHealthy);
    Assert.Contains(full.Issues, static issue => issue.Contains("malformed JSON", StringComparison.Ordinal));
    await using TreadmillRunnerDbContext verification = await factory.CreateDbContextAsync();
    Assert.Equal("{not-json", await verification.OperationReceipts.Select(static receipt => receipt.OutcomeJson).SingleAsync());
  }

  [Fact]
  public async Task Verified_backup_is_full_checked_hashed_and_promoted_atomically()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = await CreateMigratedFactoryAsync();
    string root = Path.Combine(_directory, "backups");
    var service = new VerifiedDatabaseBackupService(factory, TimeProvider.System);

    VerifiedDatabaseBackup backup = await service.CreateAsync(root, retentionCount: 3);

    string path = Path.Combine(root, backup.FileName);
    Assert.True(File.Exists(path));
    Assert.Equal(64, backup.Sha256.Length);
    Assert.True(backup.SizeBytes > 16);
    Assert.Empty(Directory.EnumerateFiles(root, "integrity-backup-*.tmp"));
    var backupFactory = TreadmillRunnerDatabase.CreateFactory(path, pooling: false);
    DatabaseIntegrityCheckResult check = await new DatabaseIntegrityChecker(backupFactory, TimeProvider.System)
      .CheckAsync(DatabaseIntegrityCheckLevel.Full);
    Assert.True(check.IsHealthy);
  }

  private async Task<IDbContextFactory<TreadmillRunnerDbContext>> CreateMigratedFactoryAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    return factory;
  }
}
