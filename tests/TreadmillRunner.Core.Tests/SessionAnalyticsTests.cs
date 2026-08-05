using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class SessionAnalyticsTests
{
  [Fact]
  public void Analytics_calculates_zone_time_adherence_and_event_counts()
  {
    var sessionId = Guid.NewGuid();
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var samples = new[]
    {
      Sample(sessionId, 0, start, 110, 8, 8, 1, 1),
      Sample(sessionId, 1, start.AddSeconds(1), 125, 8, 8.2, 1, 1.1),
      Sample(sessionId, 2, start.AddSeconds(2), 155, 8, 9, 1, 2),
    };
    SessionEvent[] events =
    [
      new ManualSpeedOverrideEvent(8, 8.2, start.AddSeconds(1)),
      new ManualInclineOverrideEvent(1, 1.5, start.AddSeconds(1)),
      new SessionPausedEvent(SessionPauseReason.WebControl, start.AddSeconds(1)),
      new DeviceDisconnectedEvent(SessionDeviceRole.Treadmill, null, start.AddSeconds(1)),
      new SessionWarningEvent("warning", "Warning", start.AddSeconds(2))
    ];
    HeartRateZone[] zones =
    [
      new HeartRateZone(1, "Easy", 100, 130),
      new HeartRateZone(2, "Tempo", 131, 160)
    ];

    var analytics = SessionAnalyticsCalculator.Calculate(sessionId, samples, events, zones);

    Assert.Equal(66.67, analytics.AdherencePercentage, 2);
    Assert.Equal(TimeSpan.FromSeconds(1), analytics.HeartRateZones.Single(zone => zone.ZoneNumber == 1).Duration);
    Assert.Equal(TimeSpan.FromSeconds(1), analytics.HeartRateZones.Single(zone => zone.ZoneNumber == 2).Duration);
    Assert.Equal(1, analytics.EventCounts.ManualSpeedOverrides);
    Assert.Equal(1, analytics.EventCounts.ManualInclineOverrides);
    Assert.Equal(1, analytics.EventCounts.Pauses);
    Assert.Equal(1, analytics.EventCounts.Disconnects);
    Assert.Equal(1, analytics.EventCounts.Warnings);
    Assert.Equal(SessionMetricAlgorithms.AdherenceV1, analytics.AdherenceAlgorithmVersion);
  }

  [Fact]
  public void Weekly_totals_include_only_completed_sessions_in_half_open_range()
  {
    var week = DateTimeOffset.Parse("2026-07-27T00:00:00Z");
    var summaries = new[]
    {
      Summary(SessionState.Completed, week, TimeSpan.FromMinutes(30), 5),
      Summary(SessionState.Stopped, week.AddDays(1), TimeSpan.FromMinutes(10), 1),
      Summary(SessionState.Completed, week.AddDays(7), TimeSpan.FromMinutes(20), 3)
    };

    var totals = SessionAnalyticsCalculator.CalculateWeeklyTotals(summaries, week, week.AddDays(7));

    Assert.Equal(1, totals.CompletedSessionCount);
    Assert.Equal(TimeSpan.FromMinutes(30), totals.Duration);
    Assert.Equal(5, totals.DistanceKilometers);
  }

  [Fact]
  public void Summary_requires_terminal_status_and_ordered_times()
  {
    var start = DateTimeOffset.Parse("2026-08-02T10:00:00Z");

    Assert.Throws<ArgumentException>(() => Summary(SessionState.Running, start, TimeSpan.FromMinutes(1), 1));
    Assert.Throws<ArgumentOutOfRangeException>(() => new SessionSummary(
        Guid.NewGuid(),
        Guid.NewGuid(),
        "Runner",
        Guid.NewGuid(),
        "Workout",
        SessionState.Completed,
        start,
        start.AddSeconds(-1),
        TimeSpan.Zero,
        0,
        0,
        null,
        null,
        0,
        0));
  }

  private static SessionSample Sample(
      Guid sessionId,
      long sequence,
      DateTimeOffset capturedAt,
      ushort heartRate,
      double plannedSpeed,
      double measuredSpeed,
      double plannedIncline,
      double measuredIncline) => new(
          sessionId,
          sequence,
          capturedAt,
          TimeSpan.FromSeconds(sequence),
          plannedSpeed,
          plannedSpeed,
          measuredSpeed,
          plannedIncline,
          plannedIncline,
          measuredIncline,
          heartRate,
          sequence * 0.002,
          sequence,
          TimeSpan.Zero,
          SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static SessionSummary Summary(
      SessionState state,
      DateTimeOffset startedAt,
      TimeSpan duration,
      double distance) => new(
          Guid.NewGuid(),
          Guid.NewGuid(),
          "Runner",
          Guid.NewGuid(),
          "Workout",
          state,
          startedAt,
          startedAt + duration,
          duration,
          distance,
          100,
          140,
          160,
          8,
          1);
}
