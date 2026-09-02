namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminLatestTwoReconciliationPlan(
  GarminWatchActivityCandidate Keep,
  GarminWatchActivityCandidate Delete,
  string Evidence);

public static class GarminLatestTwoReconciliationPlanner
{
  public static bool TryFindCanonicalOnly(
    GarminActivityMatchReference local,
    IReadOnlyList<GarminWatchActivityCandidate> candidates,
    out GarminWatchActivityCandidate? canonical)
  {
    ArgumentNullException.ThrowIfNull(local);
    ArgumentNullException.ThrowIfNull(candidates);
    canonical = candidates.Count == 1 && GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidates[0]) ? candidates[0] : null;
    return canonical is not null;
  }

  public static bool TryCreate(
    GarminActivityMatchReference local,
    IReadOnlyList<GarminWatchActivityCandidate> candidates,
    out GarminLatestTwoReconciliationPlan? plan,
    out string error)
  {
    ArgumentNullException.ThrowIfNull(local);
    ArgumentNullException.ThrowIfNull(candidates);
    plan = null;
    if (candidates.Count != 2 || candidates.Select(item => item.RemoteId).Distinct(StringComparer.Ordinal).Count() != 2)
    {
      error = "Exactly two distinct Garmin treadmill activities must exist in the local session window.";
      return false;
    }

    GarminWatchActivityCandidate[] canonical = candidates.Where(candidate => GarminWatchActivityMatcher.IsCanonicalLocalCopy(local, candidate)).ToArray();
    if (canonical.Length != 1)
    {
      error = "The complete locally uploaded Garmin activity could not be identified uniquely.";
      return false;
    }

    GarminWatchActivityCandidate keep = canonical[0];
    GarminWatchActivityCandidate partial = candidates.Single(candidate => candidate.RemoteId != keep.RemoteId);
    double startDelay = (partial.StartedAtUtc - local.StartedAtUtc).TotalSeconds;
    double missingDuration = local.DurationSeconds - partial.DurationSeconds;
    double distanceDelta = partial.DistanceKilometers - local.DistanceKilometers;
    bool isLatePartial = string.Equals(partial.ActivityType, "treadmill_running", StringComparison.OrdinalIgnoreCase) &&
      startDelay is > 45 and <= 600 &&
      missingDuration is > 45 and <= 600 &&
      Math.Abs(missingDuration - startDelay) <= 120 &&
      Math.Abs(distanceDelta) <= 0.75;
    if (!isLatePartial)
    {
      error = "The second Garmin activity is not a bounded late-start partial copy of the local run.";
      return false;
    }

    plan = new(
      keep,
      partial,
      FormattableString.Invariant(
        $"Keep complete activity {keep.RemoteId}; remove late partial activity {partial.RemoteId} (start +{startDelay:F0}s, duration -{missingDuration:F0}s, distance delta {distanceDelta:+0.00;-0.00;0.00}km)."));
    error = string.Empty;
    return true;
  }

}
