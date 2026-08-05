namespace TreadmillRunner.Core.Tests;

internal sealed class TestTimeProvider(DateTimeOffset? initial = null) : TimeProvider
{
  private DateTimeOffset _utcNow = initial ?? DateTimeOffset.UnixEpoch;
  private long _timestamp;

  public override DateTimeOffset GetUtcNow() => _utcNow;

  public override long GetTimestamp() => _timestamp;

  public override long TimestampFrequency => TimeSpan.TicksPerSecond;

  public void Advance(TimeSpan duration)
  {
    _utcNow += duration;
    _timestamp += duration.Ticks;
  }

  public void AdjustWallClock(TimeSpan duration) => _utcNow += duration;
}
