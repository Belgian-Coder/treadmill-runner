using TreadmillRunner.Core.Live;

namespace TreadmillRunner.Core.Tests;

public sealed class FourHourSimulationTests
{
  [Fact]
  public void Four_hour_four_hertz_run_has_bounded_live_points_and_one_hertz_samples()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);
    const int liveCapacity = 720;
    var livePoints = new Queue<TimeSpan>(liveCapacity);
    int sampleCount = 0;

    for (var tick = 1; tick <= 4 * 60 * 60 * 4; tick++)
    {
      TimeSpan elapsed = TimeSpan.FromMilliseconds(tick * 250L);
      if (livePoints.Count == liveCapacity)
      {
        livePoints.Dequeue();
      }

      livePoints.Enqueue(elapsed);
      if (cadence.TryAdvance(start + elapsed))
      {
        sampleCount++;
      }
    }

    Assert.Equal(liveCapacity, livePoints.Count);
    Assert.Equal(TimeSpan.FromHours(4), livePoints.Last());
    Assert.Equal(14_400, sampleCount);
  }
}
