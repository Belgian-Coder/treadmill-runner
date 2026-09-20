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

  [Fact]
  public void Irregular_scheduler_ticks_remain_phase_locked_to_one_hertz()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);
    DateTimeOffset now = start;
    int sampleCount = 0;

    for (var tick = 0; tick < 4 * 60 * 60 * 4; tick++)
    {
      now += TimeSpan.FromMilliseconds(tick % 2 == 0 ? 249 : 251);
      if (cadence.TryAdvance(now)) sampleCount++;
    }

    Assert.Equal(start.AddHours(4), now);
    Assert.Equal(14_400, sampleCount);
  }

  [Fact]
  public void Delayed_tick_skips_missed_deadlines_without_backfill_burst()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);

    Assert.True(cadence.TryAdvance(start.AddMilliseconds(3_400)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(3_400)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(3_900)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(4_100)));
    Assert.True(cadence.TryAdvance(start.AddMilliseconds(4_400)));
  }

  [Fact]
  public void Stall_overshoot_does_not_emit_again_on_the_next_prompt_tick()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);

    Assert.True(cadence.TryAdvance(start.AddMilliseconds(1_990)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(2_240)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(2_990)));
    Assert.True(cadence.TryAdvance(start.AddMilliseconds(3_010)));

    var roundedForward = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);
    Assert.True(roundedForward.TryAdvance(start.AddMilliseconds(2_600)));
    Assert.False(roundedForward.TryAdvance(start.AddMilliseconds(2_850)));
    Assert.False(roundedForward.TryAdvance(start.AddMilliseconds(3_750)));
    Assert.True(roundedForward.TryAdvance(start.AddMilliseconds(4_010)));
  }

  [Fact]
  public void Rounded_deadline_never_allows_two_actual_emissions_within_one_interval()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);

    Assert.True(cadence.TryAdvance(start.AddMilliseconds(1_490)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(2_240)));
    Assert.True(cadence.TryAdvance(start.AddMilliseconds(2_500)));
  }

  [Fact]
  public void Large_backward_clock_step_never_emits_a_timestamp_before_the_last_sample()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var cadence = new FixedIntervalCadence(TimeSpan.FromSeconds(1), start);

    Assert.True(cadence.TryAdvance(start.AddSeconds(10)));
    Assert.False(cadence.TryAdvance(start.AddSeconds(-50)));
    Assert.False(cadence.TryAdvance(start.AddSeconds(-49)));
    Assert.False(cadence.TryAdvance(start.AddMilliseconds(10_999)));
    Assert.True(cadence.TryAdvance(start.AddSeconds(11)));
  }
}
