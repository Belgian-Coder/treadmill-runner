namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminWatchHeartRateSample(double ElapsedSeconds, ushort Bpm);

public sealed record GarminActivityMatchReference(
  DateTimeOffset StartedAtUtc,
  double DurationSeconds,
  double DistanceKilometers,
  double? AverageHeartRate,
  ushort? MaximumHeartRate,
  IReadOnlyList<GarminWatchHeartRateSample> HeartRateSamples);

public sealed record GarminWatchActivityCandidate(
  string RemoteId,
  string ActivityType,
  DateTimeOffset StartedAtUtc,
  double DurationSeconds,
  double DistanceKilometers,
  double? AverageHeartRate,
  ushort? MaximumHeartRate,
  IReadOnlyList<GarminWatchHeartRateSample> HeartRateSamples);

public enum GarminWatchActivityMatchDisposition
{
  None,
  Single,
  Multiple,
  ReviewRequired,
}

public sealed record GarminWatchActivityMatch(
  GarminWatchActivityMatchDisposition Disposition,
  GarminWatchActivityCandidate? Candidate,
  string Evidence);

public static class GarminWatchActivityMatcher
{
  private static readonly TimeSpan MaximumStartDifference = TimeSpan.FromMinutes(10);

  public static GarminWatchActivityMatch Match(
    GarminActivityMatchReference local,
    IReadOnlyList<GarminWatchActivityCandidate> candidates)
  {
    ArgumentNullException.ThrowIfNull(local);
    ArgumentNullException.ThrowIfNull(candidates);

    GarminWatchActivityCandidate[] plausible = candidates
      .Where(candidate => IsPlausibleShape(local, candidate))
      .ToArray();
    if (plausible.Length == 0)
      return new(GarminWatchActivityMatchDisposition.None, null, "No Garmin treadmill activity has a close start, duration, and distance.");
    if (plausible.Length > 1)
      return new(GarminWatchActivityMatchDisposition.Multiple, null, $"{plausible.Length} Garmin treadmill activities are plausible; normal upload remains enabled.");

    GarminWatchActivityCandidate candidate = plausible[0];
    double startDelta = Math.Abs((candidate.StartedAtUtc - local.StartedAtUtc).TotalSeconds);
    double durationDelta = Math.Abs(candidate.DurationSeconds - local.DurationSeconds);
    double distanceDelta = Math.Abs(candidate.DistanceKilometers - local.DistanceKilometers);
    string evidence = FormattableString.Invariant(
      $"One watch activity matched by treadmill shape: start {startDelta:F0}s, duration {durationDelta:F0}s, distance {distanceDelta:F2}km; local Polar heart rate remains authoritative");
    return new(GarminWatchActivityMatchDisposition.Single, candidate, evidence);
  }

  private static bool IsPlausibleShape(
    GarminActivityMatchReference local,
    GarminWatchActivityCandidate candidate)
  {
    if (!string.Equals(candidate.ActivityType, "treadmill_running", StringComparison.OrdinalIgnoreCase))
      return false;
    if ((candidate.StartedAtUtc - local.StartedAtUtc).Duration() > MaximumStartDifference)
      return false;

    double maximumDurationDifference = Math.Max(180, local.DurationSeconds * 0.15);
    if (Math.Abs(candidate.DurationSeconds - local.DurationSeconds) > maximumDurationDifference)
      return false;

    double maximumDistanceDifference = Math.Max(0.25, local.DistanceKilometers * 0.10);
    return Math.Abs(candidate.DistanceKilometers - local.DistanceKilometers) <= maximumDistanceDifference;
  }

}
