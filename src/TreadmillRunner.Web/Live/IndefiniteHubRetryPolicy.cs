using Microsoft.AspNetCore.SignalR.Client;

namespace TreadmillRunner.Web.Live;

public sealed class IndefiniteHubRetryPolicy : IRetryPolicy
{
  private static readonly TimeSpan[] Delays =
  [
    TimeSpan.Zero,
    TimeSpan.FromSeconds(1),
    TimeSpan.FromSeconds(2),
    TimeSpan.FromSeconds(5),
    TimeSpan.FromSeconds(10),
  ];

  public TimeSpan? NextRetryDelay(RetryContext retryContext)
  {
    int index = (int)Math.Min(retryContext.PreviousRetryCount, Delays.Length - 1);
    TimeSpan delay = Delays[index];
    if (delay == TimeSpan.Zero) return delay;
    int jitterMilliseconds = (int)(retryContext.PreviousRetryCount % 5) * 137;
    return delay + TimeSpan.FromMilliseconds(jitterMilliseconds);
  }
}
