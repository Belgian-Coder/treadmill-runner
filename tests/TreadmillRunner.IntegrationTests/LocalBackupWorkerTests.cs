using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Household;
using TreadmillRunner.Gateway.Household;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class LocalBackupWorkerTests
{
  [Fact]
  public async Task Sqlite_backup_failure_is_recorded_before_the_failure_is_rethrown()
  {
    SqliteException failure = await CreateSqliteExceptionAsync();
    var store = new RecordingStore(EnabledPolicy());
    var backup = new FailingBackupService(failure);
    var maintenance = new RecordingMaintenanceLease();

    using ServiceProvider services = CreateServices(store);
    var worker = CreateWorker(services, backup, maintenance);

    await Assert.ThrowsAsync<SqliteException>(() => worker.VerifyNowAsync());

    StoredBackupVerification failed = Assert.Single(store.Verifications);
    Assert.Equal("Failed", failed.Status);
    Assert.Equal(0, failed.BackupBytes);
    Assert.Same(failure, backup.ObservedFailure);
    Assert.Equal(1, maintenance.BeginCount);
    Assert.Equal(1, maintenance.EndCount);
  }

  [Fact]
  public async Task Entity_framework_backup_failure_is_recorded_before_the_failure_is_rethrown()
  {
    var failure = new DbUpdateException("simulated storage failure");
    var store = new RecordingStore(EnabledPolicy());
    var backup = new FailingBackupService(failure);
    var maintenance = new RecordingMaintenanceLease();

    using ServiceProvider services = CreateServices(store);
    var worker = CreateWorker(services, backup, maintenance);

    await Assert.ThrowsAsync<DbUpdateException>(() => worker.VerifyNowAsync());

    StoredBackupVerification failed = Assert.Single(store.Verifications);
    Assert.Equal("Failed", failed.Status);
    Assert.Equal(failure.Message, failed.Detail);
    Assert.Equal(1, maintenance.EndCount);
  }

  [Fact]
  public async Task Cancellation_does_not_turn_an_aborted_backup_into_a_failed_verification()
  {
    using var cancellation = new CancellationTokenSource();
    var store = new RecordingStore(EnabledPolicy());
    var backup = new FailingBackupService(new OperationCanceledException(cancellation.Token),
      beforeThrow: cancellation.Cancel);
    var maintenance = new RecordingMaintenanceLease();

    using ServiceProvider services = CreateServices(store);
    var worker = CreateWorker(services, backup, maintenance);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => worker.VerifyNowAsync(cancellation.Token));

    Assert.Empty(store.Verifications);
    Assert.Equal(1, maintenance.EndCount);
  }

  private static LocalBackupWorker CreateWorker(
    ServiceProvider services,
    IVerifiedDatabaseBackupService backup,
    IDatabaseMaintenanceLeaseProvider maintenance) =>
    new(
      services.GetRequiredService<IServiceScopeFactory>(),
      backup,
      maintenance,
      TimeProvider.System,
      NullLogger<LocalBackupWorker>.Instance);

  private static ServiceProvider CreateServices(RecordingStore store) =>
    new ServiceCollection()
      .AddScoped<ILocalFirstExperienceStore>(_ => store)
      .BuildServiceProvider();

  private static VersionedLocalBackupPolicy EnabledPolicy() => new(
    Guid.NewGuid(),
    new LocalBackupPolicy(Path.GetFullPath(Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"))), 24, 3, true),
    1,
    DateTimeOffset.UtcNow);

  private static async Task<SqliteException> CreateSqliteExceptionAsync()
  {
    await using var connection = new SqliteConnection("Data Source=:memory:");
    await connection.OpenAsync();
    await using var command = connection.CreateCommand();
    command.CommandText = "SELECT * FROM table_that_does_not_exist;";
    try
    {
      await command.ExecuteNonQueryAsync();
    }
    catch (SqliteException exception)
    {
      return exception;
    }

    throw new InvalidOperationException("The simulated SQLite command unexpectedly succeeded.");
  }

  private sealed class FailingBackupService(
    Exception failure,
    Action? beforeThrow = null) : IVerifiedDatabaseBackupService
  {
    public Exception? ObservedFailure { get; private set; }

    public Task<int> CleanupStaleTemporaryFilesAsync(
      string backupRoot,
      TimeSpan minimumAge,
      CancellationToken cancellationToken = default) => Task.FromResult(0);

    public Task<VerifiedDatabaseBackup> CreateAsync(
      string backupRoot,
      int retentionCount,
      CancellationToken cancellationToken = default)
    {
      beforeThrow?.Invoke();
      ObservedFailure = failure;
      return Task.FromException<VerifiedDatabaseBackup>(failure);
    }
  }

  private sealed class RecordingMaintenanceLease : IDatabaseMaintenanceLeaseProvider
  {
    public int BeginCount { get; private set; }
    public int EndCount { get; private set; }

    public Task<bool> TryBeginAsync(CancellationToken cancellationToken = default)
    {
      BeginCount++;
      return Task.FromResult(true);
    }

    public Task EndAsync(CancellationToken cancellationToken = default)
    {
      EndCount++;
      return Task.CompletedTask;
    }
  }

  private sealed class RecordingStore(VersionedLocalBackupPolicy? policy) : ILocalFirstExperienceStore
  {
    public List<StoredBackupVerification> Verifications { get; } = [];

    public Task<VersionedRunnerExperiencePreferences> GetPreferencesAsync(Guid profileId, CancellationToken cancellationToken = default) => Unsupported<VersionedRunnerExperiencePreferences>();
    public Task<VersionedRunnerExperiencePreferences> SavePreferencesAsync(Guid profileId, RunnerExperiencePreferences preferences, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => Unsupported<VersionedRunnerExperiencePreferences>();
    public Task<IReadOnlyList<LocalGoalDefinition>> ListGoalsAsync(Guid profileId, CancellationToken cancellationToken = default) => Task.FromResult<IReadOnlyList<LocalGoalDefinition>>([]);
    public Task<LocalGoalDefinition> SaveGoalAsync(Guid profileId, Guid? goalId, string kind, string period, double targetValue, bool enabled, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => Unsupported<LocalGoalDefinition>();
    public Task<StoredProgressionRecommendation> SaveRecommendationAsync(Guid operationId, Guid profileId, Guid sessionId, ProgressionEvidence evidence, ProgressionRecommendation recommendation, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => Unsupported<StoredProgressionRecommendation>();
    public Task<StoredProgressionRecommendation> DecideRecommendationAsync(Guid id, Guid profileId, bool accepted, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => Unsupported<StoredProgressionRecommendation>();
    public Task<IReadOnlyList<StoredProgressionRecommendation>> ListRecommendationsAsync(Guid profileId, CancellationToken cancellationToken = default) => Task.FromResult<IReadOnlyList<StoredProgressionRecommendation>>([]);
    public Task<VersionedLocalBackupPolicy?> GetBackupPolicyAsync(CancellationToken cancellationToken = default) => Task.FromResult(policy);
    public Task<VersionedLocalBackupPolicy> SaveBackupPolicyAsync(Guid? id, LocalBackupPolicy policy, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => Unsupported<VersionedLocalBackupPolicy>();

    public Task RecordBackupVerificationAsync(StoredBackupVerification result, CancellationToken cancellationToken = default)
    {
      Verifications.Add(result);
      return Task.CompletedTask;
    }

    public Task<IReadOnlyList<StoredBackupVerification>> ListBackupVerificationsAsync(int take, CancellationToken cancellationToken = default) =>
      Task.FromResult<IReadOnlyList<StoredBackupVerification>>(Verifications.Take(take).ToArray());

    private static Task<T> Unsupported<T>() => Task.FromException<T>(new NotSupportedException());
  }
}
