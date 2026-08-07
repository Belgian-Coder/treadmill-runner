using Microsoft.AspNetCore.SignalR.Client;
using TreadmillRunner.Web.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class GatewayClientConnectionTests
{
  [Fact]
  public void Retry_policy_never_gives_up_and_uses_a_capped_jittered_delay()
  {
    var policy = new IndefiniteHubRetryPolicy();

    TimeSpan? first = policy.NextRetryDelay(new RetryContext
    {
      PreviousRetryCount = 0,
      ElapsedTime = TimeSpan.Zero,
    });
    TimeSpan? prolonged = policy.NextRetryDelay(new RetryContext
    {
      PreviousRetryCount = 100,
      ElapsedTime = TimeSpan.FromHours(12),
    });

    Assert.Equal(TimeSpan.Zero, first);
    Assert.NotNull(prolonged);
    Assert.InRange(prolonged.Value, TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(11));
  }

  [Fact]
  public void Disconnected_snapshot_is_stale_and_cannot_claim_controller_readiness()
  {
    var state = new GatewayClientSnapshot(
      GatewayClientConnectionPhase.Reconnecting,
      7,
      null,
      null,
      DateTimeOffset.UtcNow.AddSeconds(-5),
      Guid.NewGuid(),
      "build",
      null,
      true,
      "Authoritative state is being reloaded.");

    Assert.True(state.IsStale);
    Assert.False(state.IsConnected);
    Assert.False(state.HasController);
  }
}
