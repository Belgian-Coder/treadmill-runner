using TreadmillRunner.Core.Control;

namespace TreadmillRunner.Core.Tests;

public sealed class ControlLeaseManagerTests
{
  [Fact]
  public void Allows_only_one_live_lease()
  {
    var manager = new ControlLeaseManager(new TestTimeProvider());

    var first = manager.TryAcquire("browser-a");
    var second = manager.TryAcquire("browser-b");

    Assert.NotNull(first);
    Assert.Null(second);
    Assert.True(manager.IsValid(first!.Id, "browser-a"));
    Assert.False(manager.IsValid(first.Id, "browser-b"));
  }

  [Fact]
  public void Lease_expires_at_fifteen_seconds()
  {
    var time = new TestTimeProvider();
    var manager = new ControlLeaseManager(time);
    var lease = manager.TryAcquire("browser-a")!;

    time.Advance(TimeSpan.FromSeconds(15));

    Assert.False(manager.IsValid(lease.Id, "browser-a"));
    Assert.NotNull(manager.TryAcquire("browser-b"));
  }

  [Fact]
  public void Heartbeat_renews_from_current_time()
  {
    var time = new TestTimeProvider();
    var manager = new ControlLeaseManager(time);
    var lease = manager.TryAcquire("browser-a")!;
    time.Advance(TimeSpan.FromSeconds(10));

    var renewed = manager.Heartbeat(lease.Id, "browser-a");
    time.Advance(TimeSpan.FromSeconds(14));

    Assert.NotNull(renewed);
    Assert.True(manager.IsValid(lease.Id, "browser-a"));
    Assert.Equal(time.GetUtcNow().AddSeconds(1), renewed!.ExpiresAt);
  }

  [Fact]
  public void Expired_lease_cannot_be_resurrected_by_heartbeat()
  {
    var time = new TestTimeProvider();
    var manager = new ControlLeaseManager(time);
    var lease = manager.TryAcquire("browser-a")!;
    time.Advance(TimeSpan.FromSeconds(16));

    Assert.Null(manager.Heartbeat(lease.Id, "browser-a"));
  }

  [Fact]
  public void Wall_clock_adjustment_does_not_expire_monotonic_lease()
  {
    var time = new TestTimeProvider();
    var manager = new ControlLeaseManager(time);
    var lease = manager.TryAcquire("browser-a")!;

    time.AdjustWallClock(TimeSpan.FromDays(1));

    Assert.True(manager.IsValid(lease.Id, "browser-a"));
  }
}
