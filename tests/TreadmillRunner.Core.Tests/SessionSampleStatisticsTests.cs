using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class SessionSampleStatisticsTests
{
  [Fact]
  public void Statistics_are_time_weighted_and_ignore_missing_heart_rate_intervals()
  {
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-21T18:43:15Z");
    SessionSample[] samples =
    [
      Sample(sessionId, 0, started, 0, speed: 0, incline: 0, heartRate: 100),
      Sample(sessionId, 1, started.AddSeconds(1), 1, speed: 4, incline: 1, heartRate: 120),
      Sample(sessionId, 2, started.AddSeconds(4), 4, speed: 8, incline: 3, heartRate: 150),
      Sample(sessionId, 3, started.AddSeconds(6), 6, speed: 0, incline: -2, heartRate: null),
    ];

    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(samples);

    Assert.Equal(142.5, statistics.AverageHeartRateBpm);
    Assert.Equal((ushort)100, statistics.MinimumHeartRateBpm);
    Assert.Equal((ushort)150, statistics.MaximumHeartRateBpm);
    Assert.Equal(TimeSpan.FromSeconds(4), statistics.MovingTime);
    Assert.Equal(8, statistics.MaximumSpeedKph);
    Assert.Equal(1, statistics.AverageInclinePercent);
    Assert.Equal(-2, statistics.MinimumInclinePercent);
    Assert.Equal(3, statistics.MaximumInclinePercent);
  }

  [Fact]
  public void Statistics_fall_back_to_readings_when_no_positive_interval_exists()
  {
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset captured = DateTimeOffset.Parse("2026-08-21T18:43:15Z");

    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(
      [Sample(sessionId, 0, captured, 0, speed: 5, incline: 2, heartRate: 137)]);

    Assert.Equal(137, statistics.AverageHeartRateBpm);
    Assert.Equal((ushort)137, statistics.MinimumHeartRateBpm);
    Assert.Equal((ushort)137, statistics.MaximumHeartRateBpm);
    Assert.Null(statistics.MovingTime);
    Assert.Equal(2, statistics.AverageInclinePercent);
  }

  [Fact]
  public void Elevation_uses_measured_incline_and_belt_distance_for_ascent_and_descent()
  {
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-22T08:00:00Z");
    SessionSample[] samples =
    [
      Sample(sessionId, 0, started, 0, speed: 6, incline: 0, heartRate: 120, distanceKilometers: 0),
      Sample(sessionId, 1, started.AddMinutes(1), 60, speed: 6, incline: 10, heartRate: 130, distanceKilometers: 0.1),
      Sample(sessionId, 2, started.AddSeconds(90), 90, speed: 6, incline: -5, heartRate: 125, distanceKilometers: 0.15),
    ];

    SessionSampleStatistics statistics = SessionSampleStatisticsCalculator.Calculate(samples);

    Assert.InRange(statistics.TotalAscentMeters, 9.95, 9.951);
    Assert.InRange(statistics.TotalDescentMeters, 2.496, 2.497);
    Assert.InRange(statistics.NetElevationMeters, 7.453, 7.454);
  }

  private static SessionSample Sample(
    Guid sessionId,
    long sequence,
    DateTimeOffset capturedAt,
    double elapsedSeconds,
    double speed,
    double incline,
    ushort? heartRate,
    double distanceKilometers = 0) => new(
      sessionId,
      sequence,
      capturedAt,
      TimeSpan.FromSeconds(elapsedSeconds),
      plannedSpeedKph: speed,
      requestedSpeedKph: speed,
      measuredSpeedKph: speed,
      plannedInclinePercent: incline,
      requestedInclinePercent: incline,
      measuredInclinePercent: incline,
      heartRate,
      distanceKilometers,
      estimatedKilocalories: 0,
      telemetryAge: TimeSpan.Zero,
      SessionMetricAlgorithms.EstimatedCaloriesV1);
}
