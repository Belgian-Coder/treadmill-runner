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
    HeartRateComparison comparison = CompareHeartRate(local, candidate);
    if (!comparison.IsMatch)
      return new(GarminWatchActivityMatchDisposition.None, null, comparison.Evidence);

    double startDelta = Math.Abs((candidate.StartedAtUtc - local.StartedAtUtc).TotalSeconds);
    double durationDelta = Math.Abs(candidate.DurationSeconds - local.DurationSeconds);
    double distanceDelta = Math.Abs(candidate.DistanceKilometers - local.DistanceKilometers);
    string evidence = FormattableString.Invariant(
      $"One watch activity matched: start {startDelta:F0}s, duration {durationDelta:F0}s, distance {distanceDelta:F2}km; {comparison.Evidence}");
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

  private static HeartRateComparison CompareHeartRate(
    GarminActivityMatchReference local,
    GarminWatchActivityCandidate candidate)
  {
    bool averageClose = local.AverageHeartRate is { } localAverage && candidate.AverageHeartRate is { } remoteAverage &&
      Math.Abs(localAverage - remoteAverage) <= 8;
    bool maximumClose = local.MaximumHeartRate is { } localMaximum && candidate.MaximumHeartRate is { } remoteMaximum &&
      Math.Abs(localMaximum - remoteMaximum) <= 15;

    if (local.HeartRateSamples.Count < 12 || candidate.HeartRateSamples.Count < 12)
    {
      bool summaryMatch = averageClose && maximumClose;
      return new(
        summaryMatch,
        summaryMatch
          ? "heart-rate summaries corroborate the match"
          : "heart-rate evidence is missing or the summaries differ too much");
    }

    CurveScore? best = Enumerable.Range(-30, 61)
      .Select(lag => ScoreCurves(local.HeartRateSamples, candidate.HeartRateSamples, lag))
      .Where(score => score.PairCount >= 12)
      .OrderBy(score => score.MeanAbsoluteError)
      .ThenByDescending(score => score.Correlation)
      .FirstOrDefault();
    if (best is null)
      return new(false, "heart-rate curves do not contain enough aligned samples");
    bool curveMatch = best.PairCount >= 12 && best.MeanAbsoluteError <= 10 && best.Correlation >= 0.70;
    bool isMatch = curveMatch && averageClose && maximumClose;
    return new(
      isMatch,
      FormattableString.Invariant(
        $"heart-rate curve pairs {best.PairCount}, lag {best.LagSeconds}s, MAE {best.MeanAbsoluteError:F1} bpm, correlation {best.Correlation:F2}"));
  }

  private static CurveScore ScoreCurves(
    IReadOnlyList<GarminWatchHeartRateSample> local,
    IReadOnlyList<GarminWatchHeartRateSample> remote,
    int lagSeconds)
  {
    GarminWatchHeartRateSample[] orderedLocal = local.OrderBy(sample => sample.ElapsedSeconds).ToArray();
    var pairs = new List<(double Local, double Remote)>();
    foreach (GarminWatchHeartRateSample remoteSample in remote)
    {
      double target = remoteSample.ElapsedSeconds + lagSeconds;
      GarminWatchHeartRateSample? nearest = orderedLocal
        .MinBy(sample => Math.Abs(sample.ElapsedSeconds - target));
      if (nearest is null || Math.Abs(nearest.ElapsedSeconds - target) > 3)
        continue;
      pairs.Add((nearest.Bpm, remoteSample.Bpm));
    }

    if (pairs.Count == 0)
      return new(lagSeconds, 0, double.PositiveInfinity, 0);
    double meanAbsoluteError = pairs.Average(pair => Math.Abs(pair.Local - pair.Remote));
    double localMean = pairs.Average(pair => pair.Local);
    double remoteMean = pairs.Average(pair => pair.Remote);
    double numerator = pairs.Sum(pair => (pair.Local - localMean) * (pair.Remote - remoteMean));
    double localSquares = pairs.Sum(pair => Math.Pow(pair.Local - localMean, 2));
    double remoteSquares = pairs.Sum(pair => Math.Pow(pair.Remote - remoteMean, 2));
    double denominator = Math.Sqrt(localSquares * remoteSquares);
    double correlation = denominator == 0 ? (meanAbsoluteError <= 5 ? 1 : 0) : numerator / denominator;
    return new(lagSeconds, pairs.Count, meanAbsoluteError, correlation);
  }

  private sealed record HeartRateComparison(bool IsMatch, string Evidence);
  private sealed record CurveScore(int LagSeconds, int PairCount, double MeanAbsoluteError, double Correlation);
}
