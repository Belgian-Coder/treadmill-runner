using Microsoft.AspNetCore.SignalR.Client;

namespace TreadmillRunner.Web.SignalR;

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
    return delay == TimeSpan.Zero
      ? delay
      : delay + TimeSpan.FromMilliseconds((retryContext.PreviousRetryCount % 5) * 137);
  }
}
