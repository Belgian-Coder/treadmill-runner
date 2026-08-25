using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class BleReconnectPolicyTests
{
  [Fact]
  public void Delay_is_bounded_exponential_and_deterministically_staggered()
  {
    var policy = new BleReconnectPolicy();
    Guid first = Guid.Parse("00112233-4455-6677-8899-aabbccddeeff");
    Guid second = Guid.Parse("fedcba98-7654-3210-fedc-ba9876543210");

    TimeSpan[] delays = Enumerable.Range(1, 7).Select(attempt => policy.GetDelay(first, attempt)).ToArray();

    Assert.InRange(delays[0], TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1.5));
    Assert.InRange(delays[1], TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2.5));
    Assert.InRange(delays[2], TimeSpan.FromSeconds(4), TimeSpan.FromSeconds(4.5));
    Assert.InRange(delays[3], TimeSpan.FromSeconds(8), TimeSpan.FromSeconds(8.5));
    Assert.InRange(delays[4], TimeSpan.FromSeconds(10), TimeSpan.FromSeconds(10.5));
    Assert.Equal(delays[4], delays[5]);
    Assert.Equal(delays[5], delays[6]);
    Assert.NotEqual(policy.GetDelay(first, 1), policy.GetDelay(second, 1));
  }

  [Fact]
  public void Rejects_invalid_inputs()
  {
    var policy = new BleReconnectPolicy();

    Assert.Throws<ArgumentException>(() => policy.GetDelay(Guid.Empty, 1));
    Assert.Throws<ArgumentOutOfRangeException>(() => policy.GetDelay(Guid.NewGuid(), 0));
  }

  [Fact]
  public void Idle_reconnect_is_bounded_to_five_minutes()
  {
    var policy = new BleReconnectPolicy();
    TimeSpan delay = policy.GetDelay(Guid.Parse("00112233-4455-6677-8899-aabbccddeeff"), 20, active: false);

    Assert.InRange(delay, TimeSpan.FromMinutes(5), TimeSpan.FromMinutes(5));
  }

  [Fact]
  public void Idle_reconnect_keeps_exponential_backoff_before_the_five_minute_cap()
  {
    var policy = new BleReconnectPolicy();
    Guid enrollmentId = Guid.Parse("00112233-4455-6677-8899-aabbccddeeff");

    TimeSpan[] delays = Enumerable.Range(1, 6)
      .Select(attempt => policy.GetDelay(enrollmentId, attempt, active: false))
      .ToArray();

    Assert.InRange(delays[0], TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1.5));
    Assert.InRange(delays[1], TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2.5));
    Assert.InRange(delays[2], TimeSpan.FromSeconds(4), TimeSpan.FromSeconds(4.5));
    Assert.InRange(delays[3], TimeSpan.FromSeconds(8), TimeSpan.FromSeconds(8.5));
    Assert.InRange(delays[4], TimeSpan.FromSeconds(16), TimeSpan.FromSeconds(16.5));
    Assert.InRange(delays[5], TimeSpan.FromSeconds(32), TimeSpan.FromSeconds(32.5));
  }
}
