using System.Text.Json;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Web.Live;

public sealed record StoredWorkoutSessionView(
  NewWorkoutSession Definition,
  SessionState State,
  DateTimeOffset? StartedAt,
  DateTimeOffset? EndedAt,
  TimeSpan Duration,
  double DistanceKilometers,
  double EstimatedKilocalories,
  double? AverageHeartRateBpm,
  ushort? MaximumHeartRateBpm,
  double AverageSpeedKph,
  double AverageInclinePercent,
  double TotalAscentMeters,
  double TotalDescentMeters,
  double NetElevationMeters,
  SessionDebrief? Debrief,
  IReadOnlyList<SessionSample> Samples,
  IReadOnlyList<JsonElement> Events,
  SessionAnalytics Analytics,
  int TotalSampleCount = 0,
  IReadOnlyList<SessionHeartRateZoneSnapshot>? HeartRateZones = null)
{
  public int PersistedSampleCount => Math.Max(TotalSampleCount, Samples.Count);
  public bool SamplesAreDownsampled => PersistedSampleCount > Samples.Count;
}
