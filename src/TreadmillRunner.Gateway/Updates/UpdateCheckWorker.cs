namespace TreadmillRunner.Gateway.Updates;

public sealed class UpdateCheckWorker(
  UpdateManager manager,
  TimeProvider timeProvider,
  ILogger<UpdateCheckWorker> logger) : BackgroundService
{
  public static readonly TimeSpan CheckInterval = TimeSpan.FromHours(6);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      await CheckOnceAsync(stoppingToken);
      try
      {
        await Task.Delay(CheckInterval, timeProvider, stoppingToken);
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
        break;
      }
    }
  }

  internal async Task CheckOnceAsync(CancellationToken cancellationToken)
  {
    try
    {
      await manager.CheckAsync(cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception)
    {
      const string reason = "The update feed is unavailable or its configuration could not be validated.";
      manager.RecordUnavailable(reason);
      logger.LogWarning(exception, "Automatic update check failed without affecting the running gateway.");
    }
  }
}
