using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Household;

public interface ILocalBackupCoordinator
{
  Task<StoredBackupVerification> VerifyNowAsync(CancellationToken cancellationToken = default);
}

public sealed class LocalBackupWorker(
  IServiceScopeFactory scopeFactory,
  IVerifiedDatabaseBackupService backups,
  IDatabaseMaintenanceLeaseProvider maintenance,
  TimeProvider timeProvider,
  ILogger<LocalBackupWorker> logger) : BackgroundService, ILocalBackupCoordinator
{
  private readonly SemaphoreSlim _gate = new(1, 1);

  public async Task<StoredBackupVerification> VerifyNowAsync(CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      using IServiceScope scope = scopeFactory.CreateScope();
      ILocalFirstExperienceStore store = scope.ServiceProvider.GetRequiredService<ILocalFirstExperienceStore>();
      VersionedLocalBackupPolicy policy = await store.GetBackupPolicyAsync(cancellationToken)
        ?? throw new InvalidOperationException("Configure a local backup policy first.");
      if (!policy.Policy.Enabled) throw new InvalidOperationException("The local backup policy is disabled.");
      if (!await maintenance.TryBeginAsync(cancellationToken))
        throw new InvalidOperationException("Backup verification waits until the active workout is idle.");

      DateTimeOffset started = timeProvider.GetUtcNow();
      try
      {
        VerifiedDatabaseBackup created = await backups.CreateAsync(
          policy.Policy.DestinationPath, policy.Policy.RetentionCount, cancellationToken);
        var result = new StoredBackupVerification(
          Guid.NewGuid(), policy.Id, Path.Combine(Path.GetFullPath(policy.Policy.DestinationPath), created.FileName),
          "Verified", $"Isolated full SQLite integrity check passed. SHA-256 {created.Sha256}.",
          created.SizeBytes, started, timeProvider.GetUtcNow());
        await store.RecordBackupVerificationAsync(result, cancellationToken);
        return result;
      }
      catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or InvalidDataException)
      {
        var failed = new StoredBackupVerification(
          Guid.NewGuid(), policy.Id, Path.GetFullPath(policy.Policy.DestinationPath), "Failed",
          exception.Message, 0, started, timeProvider.GetUtcNow());
        await store.RecordBackupVerificationAsync(failed, cancellationToken);
        throw;
      }
      finally
      {
        await maintenance.EndAsync(CancellationToken.None);
      }
    }
    finally { _gate.Release(); }
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    using var timer = new PeriodicTimer(TimeSpan.FromMinutes(15), timeProvider);
    while (!stoppingToken.IsCancellationRequested)
    {
      try
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        ILocalFirstExperienceStore store = scope.ServiceProvider.GetRequiredService<ILocalFirstExperienceStore>();
        VersionedLocalBackupPolicy? policy = await store.GetBackupPolicyAsync(stoppingToken);
        if (policy?.Policy.Enabled == true)
        {
          StoredBackupVerification? latest = (await store.ListBackupVerificationsAsync(1, stoppingToken)).FirstOrDefault();
          if (latest is null || timeProvider.GetUtcNow() - latest.CompletedAtUtc >= TimeSpan.FromHours(policy.Policy.IntervalHours))
          {
            try { await VerifyNowAsync(stoppingToken); }
            catch (InvalidOperationException) { /* Active sessions and disabled policies are retried safely. */ }
          }
        }
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
      catch (Exception exception) { logger.LogError(exception, "Scheduled local backup verification failed."); }

      if (!await timer.WaitForNextTickAsync(stoppingToken)) break;
    }
  }
}
