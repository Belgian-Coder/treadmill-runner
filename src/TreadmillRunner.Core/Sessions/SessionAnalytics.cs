using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Core.Sessions;

public sealed record HeartRateZoneDuration(int ZoneNumber, string Name, TimeSpan Duration);

public sealed record SessionEventCounts(
    int ManualSpeedOverrides,
    int ManualInclineOverrides,
    int Pauses,
    int Disconnects,
    int Warnings);

public sealed record WeeklySessionTotals(
    DateTimeOffset From,
    DateTimeOffset ThroughExclusive,
    int CompletedSessionCount,
    TimeSpan Duration,
    double DistanceKilometers);

public sealed record SessionAnalytics(
    Guid SessionId,
    IReadOnlyList<HeartRateZoneDuration> HeartRateZones,
    double AdherencePercentage,
    string AdherenceAlgorithmVersion,
    SessionEventCounts EventCounts);

public static class SessionAnalyticsCalculator
{
  public static SessionAnalytics Calculate(
      Guid sessionId,
      IReadOnlyList<SessionSample> samples,
      IReadOnlyList<SessionEvent> events,
      IReadOnlyList<HeartRateZone> heartRateZones)
  {
    SessionContractValidation.RequireId(sessionId, nameof(sessionId));
    ArgumentNullException.ThrowIfNull(samples);
    ArgumentNullException.ThrowIfNull(events);
    ArgumentNullException.ThrowIfNull(heartRateZones);
    if (samples.Any(sample => sample.SessionId != sessionId))
    {
      throw new ArgumentException("Every sample must belong to the requested session.", nameof(samples));
    }

    ValidateSamples(samples);
    var zoneTicks = heartRateZones.ToDictionary(static zone => zone.Number, static _ => 0L);
    for (var index = 1; index < samples.Count; index++)
    {
      var sample = samples[index];
      if (sample.HeartRateBpm is not { } heartRate)
      {
        continue;
      }

      var zone = heartRateZones.SingleOrDefault(candidate =>
          heartRate >= candidate.MinimumBpm && heartRate <= candidate.MaximumBpm);
      if (zone is not null)
      {
        zoneTicks[zone.Number] = checked(zoneTicks[zone.Number] + (sample.Elapsed - samples[index - 1].Elapsed).Ticks);
      }
    }

    var eligible = 0;
    var adherent = 0;
    foreach (var sample in samples)
    {
      if (sample.PlannedSpeedKph is null && sample.PlannedInclinePercent is null)
      {
        continue;
      }

      eligible++;
      var speedMatches = sample.PlannedSpeedKph is not { } plannedSpeed ||
          Math.Abs(sample.MeasuredSpeedKph - plannedSpeed) <= SessionMetricAlgorithms.SpeedAdherenceToleranceKph;
      var inclineMatches = sample.PlannedInclinePercent is not { } plannedIncline ||
          Math.Abs(sample.MeasuredInclinePercent - plannedIncline) <= SessionMetricAlgorithms.InclineAdherenceTolerancePercent;
      if (speedMatches && inclineMatches)
      {
        adherent++;
      }
    }

    var zoneDurations = heartRateZones
        .OrderBy(static zone => zone.Number)
        .Select(zone => new HeartRateZoneDuration(zone.Number, zone.Name, TimeSpan.FromTicks(zoneTicks[zone.Number])))
        .ToArray();
    var counts = new SessionEventCounts(
        events.Count(static item => item is ManualSpeedOverrideEvent),
        events.Count(static item => item is ManualInclineOverrideEvent),
        events.Count(static item => item is SessionPausedEvent),
        events.Count(static item => item is DeviceDisconnectedEvent),
        events.Count(static item => item is SessionWarningEvent));
    var adherence = eligible == 0 ? 100 : (double)adherent / eligible * 100;

    return new SessionAnalytics(
        sessionId,
        Array.AsReadOnly(zoneDurations),
        adherence,
        SessionMetricAlgorithms.AdherenceV1,
        counts);
  }

  public static WeeklySessionTotals CalculateWeeklyTotals(
      IEnumerable<SessionSummary> summaries,
      DateTimeOffset from,
      DateTimeOffset throughExclusive)
  {
    ArgumentNullException.ThrowIfNull(summaries);
    SessionContractValidation.RequireUtc(from, nameof(from));
    SessionContractValidation.RequireUtc(throughExclusive, nameof(throughExclusive));
    if (throughExclusive <= from)
    {
      throw new ArgumentOutOfRangeException(nameof(throughExclusive));
    }

    var completed = summaries
        .Where(summary => summary.Status == SessionState.Completed &&
            summary.StartedAt >= from && summary.StartedAt < throughExclusive)
        .ToArray();
    return new WeeklySessionTotals(
        from,
        throughExclusive,
        completed.Length,
        TimeSpan.FromTicks(completed.Sum(static summary => summary.Duration.Ticks)),
        completed.Sum(static summary => summary.DistanceKilometers));
  }

  private static void ValidateSamples(IReadOnlyList<SessionSample> samples)
  {
    for (var index = 1; index < samples.Count; index++)
    {
      if (samples[index].Sequence <= samples[index - 1].Sequence ||
          samples[index].CapturedAt < samples[index - 1].CapturedAt ||
          samples[index].Elapsed < samples[index - 1].Elapsed)
      {
        throw new ArgumentException("Samples must have increasing sequence, capture time, and elapsed time.", nameof(samples));
      }
    }
  }
}
