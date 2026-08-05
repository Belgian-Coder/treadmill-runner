using TreadmillRunner.Web.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class BoundedLiveSeriesTests
{
  [Fact]
  public void Twelve_hour_one_second_browser_series_retains_the_complete_supported_workout_window()
  {
    const int twelveHours = 12 * 60 * 60;
    var series = new BoundedLiveSeries<int>(twelveHours);
    for (var second = 0; second < twelveHours; second++)
    {
      series.AppendOrReplace(second, static (previous, current) => previous == current);
    }

    IReadOnlyList<int> snapshot = series.Snapshot();
    Assert.Equal(twelveHours, series.Count);
    Assert.Equal(twelveHours, series.Capacity);
    Assert.Equal(0, snapshot[0]);
    Assert.Equal(twelveHours - 1, snapshot[^1]);
  }

  [Fact]
  public void Snapshot_is_reused_until_the_series_changes()
  {
    var series = new BoundedLiveSeries<int>(3);
    series.AppendOrReplace(1, static (previous, current) => previous == current);

    IReadOnlyList<int> first = series.Snapshot();
    IReadOnlyList<int> second = series.Snapshot();

    Assert.Same(first, second);
    Assert.Equal(1, series.Version);

    series.AppendOrReplace(2, static (previous, current) => previous == current);

    IReadOnlyList<int> changed = series.Snapshot();
    Assert.NotSame(first, changed);
    Assert.Equal([1, 2], changed);
    Assert.Equal(2, series.Version);
  }

  [Fact]
  public void Identical_replacement_does_not_invalidate_the_cached_snapshot()
  {
    var series = new BoundedLiveSeries<int>(2);
    const int point = 1;
    series.AppendOrReplace(point, static (previous, current) => previous == current);
    IReadOnlyList<int> snapshot = series.Snapshot();
    long version = series.Version;

    series.AppendOrReplace(point, static (previous, current) => previous == current);

    Assert.Equal(version, series.Version);
    Assert.Same(snapshot, series.Snapshot());
  }
}
