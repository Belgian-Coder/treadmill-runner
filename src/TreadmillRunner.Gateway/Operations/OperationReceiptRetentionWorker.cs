using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Operations;

/// <summary>
/// Keeps durable idempotency receipts bounded while retaining the configured
/// replay window. This belongs to the host because it owns scheduling and DI.
/// </summary>
public sealed class OperationReceiptRetentionWorker(
  IServiceScopeFactory scopeFactory,
  IConfiguration configuration,
  TimeProvider timeProvider,
  ILogger<OperationReceiptRetentionWorker> logger) : BackgroundService
{
  private static readonly TimeSpan RunInterval = TimeSpan.FromHours(6);
  private readonly int retentionDays = Math.Clamp(
    configuration.GetValue("Persistence:OperationReceiptRetentionDays", 90),
    7,
    365);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    using var timer = new PeriodicTimer(RunInterval, timeProvider);
    do
    {
      try
      {
        using IServiceScope scope = scopeFactory.CreateScope();
        IOperationReceiptStore receipts = scope.ServiceProvider.GetRequiredService<IOperationReceiptStore>();
        int removed = await receipts.PruneAsync(
          timeProvider.GetUtcNow().Subtract(TimeSpan.FromDays(retentionDays)),
          stoppingToken);
        if (removed > 0)
          logger.LogInformation("Pruned {Count} operation receipts older than {RetentionDays} days.", removed, retentionDays);
      }
      catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        logger.LogWarning(exception, "Operation receipt retention pass failed; it will retry later.");
      }
    }
    while (await timer.WaitForNextTickAsync(stoppingToken));
  }
}
