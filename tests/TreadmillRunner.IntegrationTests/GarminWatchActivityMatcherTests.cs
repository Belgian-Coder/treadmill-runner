using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminWatchActivityMatcherTests
{
  [Fact]
  public void Selects_one_treadmill_activity_with_close_shape()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    GarminActivityMatchReference local = Reference(started);
    GarminWatchActivityCandidate candidate = Candidate(
      "24064793770",
      started.AddMinutes(2),
      durationSeconds: 2091,
      distanceKilometers: 3.10,
      averageHeartRate: 136,
      maximumHeartRate: 161,
      heartRateOffsetSeconds: 15);

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(local, [candidate]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Single, result.Disposition);
    Assert.Equal(candidate.RemoteId, result.Candidate?.RemoteId);
    Assert.Contains("Polar heart rate remains authoritative", result.Evidence, StringComparison.Ordinal);
  }

  [Fact]
  public void More_than_one_plausible_activity_is_left_for_normal_upload()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    GarminActivityMatchReference local = Reference(started);

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(
      local,
      [
        Candidate("one", started.AddMinutes(1), 2050, 3.08, 135, 159, 0),
        Candidate("two", started.AddMinutes(3), 2100, 3.12, 137, 162, 0),
      ]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Multiple, result.Disposition);
    Assert.Null(result.Candidate);
  }

  [Fact]
  public void Rejects_a_candidate_outside_the_ten_minute_start_window()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(
      Reference(started),
      [Candidate("late", started.AddMinutes(11), 2091, 3.10, 136, 161, 0)]);

    Assert.Equal(GarminWatchActivityMatchDisposition.None, result.Disposition);
  }

  [Fact]
  public void Unique_treadmill_shape_matches_without_local_heart_rate()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    GarminActivityMatchReference local = new(
      started,
      2010,
      3.10,
      null,
      null,
      []);
    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(
      local,
      [Candidate("review", started.AddMinutes(2), 2091, 3.10, 136, 161, 15)]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Single, result.Disposition);
    Assert.Equal("review", result.Candidate?.RemoteId);
    Assert.Contains("treadmill shape", result.Evidence, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Unique_treadmill_shape_matches_without_watch_heart_rate()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    GarminWatchActivityCandidate candidate = new(
      "watch-no-hr",
      "treadmill_running",
      started,
      2010,
      3.10,
      null,
      null,
      []);

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(Reference(started), [candidate]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Single, result.Disposition);
    Assert.Equal("watch-no-hr", result.Candidate?.RemoteId);
  }

  [Fact]
  public void Unique_treadmill_shape_matches_despite_large_watch_heart_rate_deviation()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    GarminWatchActivityCandidate candidate = Candidate(
      "watch-different-hr",
      started.AddMinutes(3),
      1830,
      3.08,
      95,
      110,
      0);

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(Reference(started), [candidate]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Single, result.Disposition);
    Assert.Equal(candidate.RemoteId, result.Candidate?.RemoteId);
    Assert.Contains("Polar heart rate remains authoritative", result.Evidence, StringComparison.Ordinal);
  }

  [Fact]
  public void Late_watch_start_matches_even_when_indoor_watch_distance_deviates()
  {
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-26T18:13:10Z");
    GarminActivityMatchReference local = new(started, 1712, 2.6706, 124, 153, []);
    GarminWatchActivityCandidate watch = Candidate(
      "24127800106",
      started.AddSeconds(144),
      1576.743,
      3.03929,
      126,
      153,
      0);

    GarminWatchActivityMatch result = GarminWatchActivityMatcher.Match(local, [watch]);

    Assert.Equal(GarminWatchActivityMatchDisposition.Single, result.Disposition);
    Assert.Equal(watch.RemoteId, result.Candidate?.RemoteId);
    Assert.Contains("distance 0.37km", result.Evidence, StringComparison.Ordinal);
  }

  private static GarminActivityMatchReference Reference(DateTimeOffset started) => new(
    started,
    2010,
    3.10,
    136,
    161,
    HeartRates(0));

  private static GarminWatchActivityCandidate Candidate(
    string id,
    DateTimeOffset started,
    double durationSeconds,
    double distanceKilometers,
    double averageHeartRate,
    ushort maximumHeartRate,
    double heartRateOffsetSeconds) => new(
      id,
      "treadmill_running",
      started,
      durationSeconds,
      distanceKilometers,
      averageHeartRate,
      maximumHeartRate,
      HeartRates(heartRateOffsetSeconds));

  private static IReadOnlyList<GarminWatchHeartRateSample> HeartRates(double offsetSeconds) =>
    Enumerable.Range(0, 25)
      .Select(index => new GarminWatchHeartRateSample(
        offsetSeconds + (index * 10),
        (ushort)(110 + (index * 2) + ((index % 4) * 3))))
      .ToArray();
}
