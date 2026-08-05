using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Operations;

public interface IDatabaseMaintenanceLeaseProvider
{
  Task<bool> TryBeginAsync(CancellationToken cancellationToken = default);
  Task EndAsync(CancellationToken cancellationToken = default);
}

public sealed class LiveSessionDatabaseMaintenanceLeaseProvider(
  ILiveSessionCoordinator live) : IDatabaseMaintenanceLeaseProvider
{
  public Task<bool> TryBeginAsync(CancellationToken cancellationToken = default) =>
    live.TryBeginMaintenanceAsync(cancellationToken);

  public Task EndAsync(CancellationToken cancellationToken = default) =>
    live.CancelMaintenanceAsync(cancellationToken);
}

public interface IDatabaseIntegrityCoordinator
{
  DatabaseIntegrityStatus Current { get; }
  Task<DatabaseIntegrityStatus> CheckNowAsync(CancellationToken cancellationToken = default);
}

public sealed class DatabaseIntegrityCoordinator(
  IDatabaseIntegrityChecker checker,
  IVerifiedDatabaseBackupService backups,
  IDatabaseIntegrityStatusStore statuses,
  IDatabaseMaintenanceLeaseProvider maintenance,
  IConfiguration configuration,
  TimeProvider timeProvider,
  ILogger<DatabaseIntegrityCoordinator> logger) : BackgroundService, IDatabaseIntegrityCoordinator
{
  private static readonly TimeSpan StaleTemporaryFileAge = TimeSpan.FromHours(24);
  private static readonly TimeSpan DeferredRetryDelay = TimeSpan.FromMinutes(15);
  private readonly SemaphoreSlim _gate = new(1, 1);
  private readonly SemaphoreSlim _scheduleChanged = new(0, 1);
  private readonly TimeSpan _interval = TimeSpan.FromMinutes(Math.Clamp(
    configuration.GetValue("Persistence:IntegrityCheckIntervalMinutes", 1440),
    15,
    7 * 24 * 60));
  private readonly int _retentionCount = Math.Clamp(
    configuration.GetValue("Persistence:IntegrityBackupRetention", 3),
    2,
    10);
  private readonly string _backupRoot = ResolveBackupRoot(configuration);

  public DatabaseIntegrityStatus Current => statuses.Current;

  public override async Task StartAsync(CancellationToken cancellationToken)
  {
    await CheckNowAsync(cancellationToken);
    await base.StartAsync(cancellationToken);
  }

  public async Task<DatabaseIntegrityStatus> CheckNowAsync(
    CancellationToken cancellationToken = default)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      DateTimeOffset now = timeProvider.GetUtcNow();
      DatabaseIntegrityStatus previous = statuses.Current;
      await statuses.SaveAsync(previous with
      {
        State = DatabaseIntegrityState.Checking,
        Message = "Database integrity is being checked while the application is idle.",
        UpdatedAtUtc = now,
        NextCheckAtUtc = now + _interval,
        Issues = [],
      }, cancellationToken);

      if (!await maintenance.TryBeginAsync(cancellationToken))
      {
        return await SaveDeferredAsync(previous, cancellationToken);
      }

      try
      {
        try
        {
          return await RunUnderMaintenanceAsync(previous, cancellationToken);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
          await statuses.SaveAsync(previous with
          {
            State = DatabaseIntegrityState.Deferred,
            Message = "Database integrity checking was canceled before completion and will be retried.",
            UpdatedAtUtc = timeProvider.GetUtcNow(),
            NextCheckAtUtc = timeProvider.GetUtcNow() + DeferredRetryDelay,
          }, CancellationToken.None);
          throw;
        }
      }
      finally
      {
        await maintenance.EndAsync(CancellationToken.None);
      }
    }
    finally
    {
      _gate.Release();
      SignalScheduleChanged();
    }
  }

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      try
      {
        TimeSpan delay = GetScheduledDelay(timeProvider.GetUtcNow());
        if (await _scheduleChanged.WaitAsync(delay, stoppingToken)) continue;
        await CheckNowAsync(stoppingToken);
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        logger.LogError(exception, "The scheduled database integrity pass failed.");
      }
    }
  }

  internal TimeSpan GetScheduledDelay(DateTimeOffset nowUtc)
  {
    DateTimeOffset nextCheck = statuses.Current.NextCheckAtUtc ?? nowUtc + _interval;
    TimeSpan delay = nextCheck - nowUtc;
    return delay <= TimeSpan.Zero ? TimeSpan.Zero : delay;
  }

  private async Task<DatabaseIntegrityStatus> SaveDeferredAsync(
    DatabaseIntegrityStatus previous,
    CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    DatabaseIntegrityStatus deferred = previous with
    {
      State = DatabaseIntegrityState.Deferred,
      Message = "Database integrity checking is waiting for an idle application.",
      UpdatedAtUtc = now,
      NextCheckAtUtc = now + DeferredRetryDelay,
    };
    await statuses.SaveAsync(deferred, cancellationToken);
    return deferred;
  }

  private void SignalScheduleChanged()
  {
    try { _scheduleChanged.Release(); }
    catch (SemaphoreFullException)
    {
      // A queued signal already causes the scheduler to recompute from persisted state.
    }
  }

  private async Task<DatabaseIntegrityStatus> RunUnderMaintenanceAsync(
    DatabaseIntegrityStatus previous,
    CancellationToken cancellationToken)
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    var maintenanceIssues = new List<string>();
    int staleFiles = await backups.CleanupStaleTemporaryFilesAsync(
      _backupRoot,
      StaleTemporaryFileAge,
      cancellationToken);
    DatabaseIntegrityCheckResult initialQuick = await checker.CheckAsync(
      DatabaseIntegrityCheckLevel.Quick,
      cancellationToken);
    DatabaseMaintenanceResult maintenanceResult = await checker.RunSafeMaintenanceAsync(cancellationToken);
    maintenanceIssues.AddRange(maintenanceResult.Issues);
    DatabaseIntegrityCheckResult quick = await checker.CheckAsync(
      DatabaseIntegrityCheckLevel.Quick,
      cancellationToken);
    if (!quick.IsHealthy)
    {
      DatabaseIntegrityCheckResult fullFailure = await checker.CheckAsync(
        DatabaseIntegrityCheckLevel.Full,
        cancellationToken);
      string[] issues = initialQuick.Issues
        .Concat(quick.Issues)
        .Concat(fullFailure.Issues)
        .Distinct(StringComparer.Ordinal)
        .Take(100)
        .ToArray();
      DatabaseIntegrityStatus unhealthy = previous with
      {
        State = DatabaseIntegrityState.Unhealthy,
        Message = "The database remains unhealthy after bounded safe maintenance. No destructive repair was attempted.",
        UpdatedAtUtc = timeProvider.GetUtcNow(),
        LastQuickCheckAtUtc = quick.CompletedAtUtc,
        LastFullCheckAtUtc = fullFailure.CompletedAtUtc,
        LastMaintenanceAtUtc = maintenanceResult.CompletedAtUtc,
        NextCheckAtUtc = timeProvider.GetUtcNow() + _interval,
        RecoveryRequired = true,
        Issues = issues,
      };
      await statuses.SaveAsync(unhealthy, cancellationToken);
      return unhealthy;
    }

    DatabaseIntegrityCheckResult full = await checker.CheckAsync(
      DatabaseIntegrityCheckLevel.Full,
      cancellationToken);
    if (!full.IsHealthy)
    {
      DatabaseIntegrityStatus unhealthy = previous with
      {
        State = DatabaseIntegrityState.Unhealthy,
        Message = "The database failed full integrity validation. No destructive repair was attempted.",
        UpdatedAtUtc = full.CompletedAtUtc,
        LastQuickCheckAtUtc = quick.CompletedAtUtc,
        LastFullCheckAtUtc = full.CompletedAtUtc,
        LastMaintenanceAtUtc = maintenanceResult.CompletedAtUtc,
        NextCheckAtUtc = full.CompletedAtUtc + _interval,
        RecoveryRequired = true,
        Issues = full.Issues,
      };
      await statuses.SaveAsync(unhealthy, cancellationToken);
      return unhealthy;
    }

    try
    {
      VerifiedDatabaseBackup backup = await backups.CreateAsync(
        _backupRoot,
        _retentionCount,
        cancellationToken);
      DatabaseIntegrityStatus healthy = previous with
      {
        State = DatabaseIntegrityState.Healthy,
        Message = initialQuick.IsHealthy
          ? staleFiles == 0
            ? "The database passed quick and full validation, and a verified last-known-good backup was retained."
            : $"The database passed validation; {staleFiles} stale temporary file(s) were removed and a verified backup was retained."
          : "Bounded safe maintenance restored a clean integrity result, and a verified last-known-good backup was retained.",
        UpdatedAtUtc = timeProvider.GetUtcNow(),
        LastQuickCheckAtUtc = quick.CompletedAtUtc,
        LastFullCheckAtUtc = full.CompletedAtUtc,
        LastHealthyAtUtc = full.CompletedAtUtc,
        LastMaintenanceAtUtc = maintenanceResult.CompletedAtUtc,
        LastBackupAtUtc = backup.CreatedAtUtc,
        LastBackupFileName = backup.FileName,
        LastBackupSha256 = backup.Sha256,
        NextCheckAtUtc = timeProvider.GetUtcNow() + _interval,
        RecoveryRequired = false,
        Issues = maintenanceIssues,
      };
      await statuses.SaveAsync(healthy, cancellationToken);
      return healthy;
    }
    catch (Exception exception) when (exception is InvalidDataException or IOException or UnauthorizedAccessException)
    {
      logger.LogWarning(exception, "The healthy database could not promote a verified automatic backup.");
      DatabaseIntegrityStatus warning = previous with
      {
        State = DatabaseIntegrityState.HealthyWithBackupWarning,
        Message = "The database is healthy, but its verified automatic backup could not be promoted.",
        UpdatedAtUtc = timeProvider.GetUtcNow(),
        LastQuickCheckAtUtc = quick.CompletedAtUtc,
        LastFullCheckAtUtc = full.CompletedAtUtc,
        LastHealthyAtUtc = full.CompletedAtUtc,
        LastMaintenanceAtUtc = maintenanceResult.CompletedAtUtc,
        NextCheckAtUtc = timeProvider.GetUtcNow() + _interval,
        RecoveryRequired = false,
        Issues = maintenanceIssues.Append("The verified automatic backup could not be promoted.").ToArray(),
      };
      await statuses.SaveAsync(warning, cancellationToken);
      return warning;
    }
  }

  private static string ResolveBackupRoot(IConfiguration configuration)
  {
    string databasePath = configuration["Persistence:DatabasePath"]
      ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db");
    return Path.GetFullPath(configuration["Persistence:IntegrityBackupRoot"]
      ?? Path.Combine(Path.GetDirectoryName(Path.GetFullPath(databasePath))!, "backups", "integrity"));
  }
}
