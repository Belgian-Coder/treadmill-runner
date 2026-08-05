using TreadmillRunner.Core.Live;

namespace TreadmillRunner.Web.Live;

public static class LiveChartTimeline
{
  private static readonly TimeSpan MinimumOpenEndedWindow = TimeSpan.FromMinutes(5);
  private static readonly TimeSpan OpenEndedLeadTime = TimeSpan.FromMinutes(1);

  public static TimeSpan ResolveDuration(ActiveSessionSnapshot? session, TimeSpan latestElapsed)
  {
    TimeSpan elapsed = Max(session?.Live.Elapsed ?? TimeSpan.Zero, latestElapsed);
    TimeSpan plannedDuration = session?.WorkoutPlan.Count > 0
      ? session.WorkoutPlan[^1].Elapsed
      : TimeSpan.Zero;
    if (plannedDuration > TimeSpan.Zero)
    {
      return Max(plannedDuration, elapsed, TimeSpan.FromSeconds(1));
    }

    if (session?.Remaining is { } remaining)
    {
      return Max(elapsed + remaining, elapsed, TimeSpan.FromSeconds(1));
    }

    double fiveMinuteWindows = Math.Ceiling(
      (elapsed + OpenEndedLeadTime).TotalSeconds / MinimumOpenEndedWindow.TotalSeconds);
    return TimeSpan.FromTicks(checked((long)Math.Max(1, fiveMinuteWindows) * MinimumOpenEndedWindow.Ticks));
  }

  public static double PositionX(TimeSpan elapsed, TimeSpan duration)
  {
    double totalSeconds = Math.Max(1, duration.TotalSeconds);
    return 10 + (Math.Clamp(elapsed.TotalSeconds / totalSeconds, 0, 1) * 700);
  }

  private static TimeSpan Max(params TimeSpan[] values) => values.Max();
}
