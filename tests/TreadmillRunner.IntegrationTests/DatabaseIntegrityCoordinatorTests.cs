using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class DatabaseIntegrityCoordinatorTests
{
  [Fact]
  public async Task Healthy_pass_runs_bounded_maintenance_and_retains_verified_backup()
  {
    var checker = new FakeChecker(healthy: true);
    var backups = new FakeBackups();
    var statuses = new MemoryStatusStore();
    var lease = new FakeLease(available: true);
    DatabaseIntegrityCoordinator coordinator = Create(checker, backups, statuses, lease);

    DatabaseIntegrityStatus result = await coordinator.CheckNowAsync();

    Assert.Equal(DatabaseIntegrityState.Healthy, result.State);
    Assert.False(result.RecoveryRequired);
    Assert.Equal(3, checker.CheckCount);
    Assert.Equal(1, checker.MaintenanceCount);
    Assert.Equal(1, backups.CreateCount);
    Assert.Equal(1, lease.BeginCount);
    Assert.Equal(1, lease.EndCount);
    Assert.Equal(new string('A', 64), result.LastBackupSha256);
  }

  [Fact]
  public async Task Unresolved_integrity_failure_is_visible_and_never_promotes_backup()
  {
    var checker = new FakeChecker(healthy: false);
    var backups = new FakeBackups();
    var statuses = new MemoryStatusStore();
    var lease = new FakeLease(available: true);
    DatabaseIntegrityCoordinator coordinator = Create(checker, backups, statuses, lease);

    DatabaseIntegrityStatus result = await coordinator.CheckNowAsync();

    Assert.Equal(DatabaseIntegrityState.Unhealthy, result.State);
    Assert.True(result.RecoveryRequired);
    Assert.Contains("No destructive repair", result.Message, StringComparison.Ordinal);
    Assert.Equal(0, backups.CreateCount);
    Assert.Equal(1, lease.EndCount);
  }

  [Fact]
  public async Task Active_application_defers_check_without_touching_database()
  {
    var checker = new FakeChecker(healthy: true);
    var backups = new FakeBackups();
    var statuses = new MemoryStatusStore();
    var lease = new FakeLease(available: false);
    DatabaseIntegrityCoordinator coordinator = Create(checker, backups, statuses, lease);

    DatabaseIntegrityStatus result = await coordinator.CheckNowAsync();

    Assert.Equal(DatabaseIntegrityState.Deferred, result.State);
    Assert.Equal(0, checker.CheckCount);
    Assert.Equal(0, checker.MaintenanceCount);
    Assert.Equal(0, backups.CreateCount);
    Assert.Equal(0, lease.EndCount);
  }

  [Fact]
  public async Task Active_database_mutation_defers_check_and_schedules_short_retry()
  {
    DateTimeOffset now = new(2026, 8, 5, 12, 0, 0, TimeSpan.Zero);
    var checker = new FakeChecker(healthy: true);
    var backups = new FakeBackups();
    var statuses = new MemoryStatusStore();
    var applicationMaintenance = new ApplicationMaintenanceState();
    Assert.True(applicationMaintenance.TryBeginMutation());
    var lease = new ApplicationBarrierLease(applicationMaintenance);
    DatabaseIntegrityCoordinator coordinator = Create(
      checker,
      backups,
      statuses,
      lease,
      new FixedTimeProvider(now));

    try
    {
      DatabaseIntegrityStatus result = await coordinator.CheckNowAsync();

      Assert.Equal(DatabaseIntegrityState.Deferred, result.State);
      Assert.Equal(now.AddMinutes(15), result.NextCheckAtUtc);
      Assert.Equal(TimeSpan.FromMinutes(15), coordinator.GetScheduledDelay(now));
      Assert.Equal(0, checker.CheckCount);
      Assert.Equal(0, backups.CreateCount);
      Assert.Equal(1, lease.BeginCount);
      Assert.Equal(0, lease.EndCount);
    }
    finally
    {
      applicationMaintenance.EndMutation();
    }
  }

  [Fact]
  public async Task Sidecar_store_round_trips_status_without_database_access()
  {
    string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    try
    {
      string path = Path.Combine(directory, "health.json");
      IConfiguration configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = Path.Combine(directory, "missing.db"),
        ["Persistence:IntegrityStatusPath"] = path,
      }).Build();
      var first = new DatabaseIntegrityStatusStore(
        configuration,
        TimeProvider.System,
        NullLogger<DatabaseIntegrityStatusStore>.Instance);
      DatabaseIntegrityStatus expected = first.Current with
      {
        State = DatabaseIntegrityState.Unhealthy,
        Message = "Visible failure",
        RecoveryRequired = true,
        Issues = ["integrity error"],
      };

      await first.SaveAsync(expected);
      var reloaded = new DatabaseIntegrityStatusStore(
        configuration,
        TimeProvider.System,
        NullLogger<DatabaseIntegrityStatusStore>.Instance);

      Assert.Equal(expected.State, reloaded.Current.State);
      Assert.Equal(expected.Message, reloaded.Current.Message);
      Assert.Equal(expected.RecoveryRequired, reloaded.Current.RecoveryRequired);
      Assert.Equal(expected.Issues, reloaded.Current.Issues);
      Assert.True(File.Exists(path));
      Assert.Empty(Directory.EnumerateFiles(directory, ".database-integrity-*.tmp"));
    }
    finally
    {
      Directory.Delete(directory, recursive: true);
    }
  }

  private static DatabaseIntegrityCoordinator Create(
    IDatabaseIntegrityChecker checker,
    IVerifiedDatabaseBackupService backups,
    IDatabaseIntegrityStatusStore statuses,
    IDatabaseMaintenanceLeaseProvider lease,
    TimeProvider? timeProvider = null)
  {
    IConfiguration configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
    {
      ["Persistence:DatabasePath"] = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", "coordinator.db"),
      ["Persistence:IntegrityCheckIntervalMinutes"] = "1440",
    }).Build();
    return new DatabaseIntegrityCoordinator(
      checker,
      backups,
      statuses,
      lease,
      configuration,
      timeProvider ?? TimeProvider.System,
      NullLogger<DatabaseIntegrityCoordinator>.Instance);
  }

  private sealed class FixedTimeProvider(DateTimeOffset nowUtc) : TimeProvider
  {
    public override DateTimeOffset GetUtcNow() => nowUtc;
  }

  private sealed class ApplicationBarrierLease(IApplicationMaintenanceState maintenance) :
    IDatabaseMaintenanceLeaseProvider
  {
    public int BeginCount { get; private set; }
    public int EndCount { get; private set; }

    public Task<bool> TryBeginAsync(CancellationToken cancellationToken = default)
    {
      BeginCount++;
      return Task.FromResult(maintenance.TryBegin());
    }

    public Task EndAsync(CancellationToken cancellationToken = default)
    {
      EndCount++;
      maintenance.End();
      return Task.CompletedTask;
    }
  }

  private sealed class FakeChecker(bool healthy) : IDatabaseIntegrityChecker
  {
    public int CheckCount { get; private set; }
    public int MaintenanceCount { get; private set; }

    public Task<DatabaseIntegrityCheckResult> CheckAsync(
      DatabaseIntegrityCheckLevel level,
      CancellationToken cancellationToken = default)
    {
      CheckCount++;
      DateTimeOffset now = DateTimeOffset.UtcNow;
      return Task.FromResult(new DatabaseIntegrityCheckResult(
        level,
        healthy,
        now,
        now,
        healthy ? [] : ["integrity error"]));
    }

    public Task<DatabaseMaintenanceResult> RunSafeMaintenanceAsync(
      CancellationToken cancellationToken = default)
    {
      MaintenanceCount++;
      return Task.FromResult(new DatabaseMaintenanceResult(
        DateTimeOffset.UtcNow,
        ["WAL checkpoint", "SQLite optimize"],
        []));
    }
  }

  private sealed class FakeBackups : IVerifiedDatabaseBackupService
  {
    public int CreateCount { get; private set; }

    public Task<int> CleanupStaleTemporaryFilesAsync(
      string backupRoot,
      TimeSpan minimumAge,
      CancellationToken cancellationToken = default) => Task.FromResult(0);

    public Task<VerifiedDatabaseBackup> CreateAsync(
      string backupRoot,
      int retentionCount,
      CancellationToken cancellationToken = default)
    {
      CreateCount++;
      return Task.FromResult(new VerifiedDatabaseBackup(
        "integrity-last-known-good.db",
        1024,
        new string('A', 64),
        DateTimeOffset.UtcNow));
    }
  }

  private sealed class MemoryStatusStore : IDatabaseIntegrityStatusStore
  {
    public DatabaseIntegrityStatus Current { get; private set; } =
      DatabaseIntegrityStatus.Initial(DateTimeOffset.UtcNow);

    public Task SaveAsync(
      DatabaseIntegrityStatus status,
      CancellationToken cancellationToken = default)
    {
      Current = status;
      return Task.CompletedTask;
    }
  }

  private sealed class FakeLease(bool available) : IDatabaseMaintenanceLeaseProvider
  {
    public int BeginCount { get; private set; }
    public int EndCount { get; private set; }

    public Task<bool> TryBeginAsync(CancellationToken cancellationToken = default)
    {
      BeginCount++;
      return Task.FromResult(available);
    }

    public Task EndAsync(CancellationToken cancellationToken = default)
    {
      EndCount++;
      return Task.CompletedTask;
    }
  }
}
