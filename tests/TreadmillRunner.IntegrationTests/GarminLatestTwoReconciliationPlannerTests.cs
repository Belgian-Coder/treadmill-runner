using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminLatestTwoReconciliationPlannerTests
{
  [Fact]
  public void Keeps_the_complete_local_upload_and_selects_only_the_late_partial_watch_activity()
  {
    DateTimeOffset start = DateTimeOffset.Parse("2026-08-26T18:13:10Z");
    GarminActivityMatchReference local = Local(start);
    GarminWatchActivityCandidate complete = Candidate("complete", start.AddSeconds(2), 1710, 2.67);
    GarminWatchActivityCandidate partial = Candidate("partial", start.AddSeconds(135), 1577, 3.04);

    bool accepted = GarminLatestTwoReconciliationPlanner.TryCreate(local, [partial, complete], out GarminLatestTwoReconciliationPlan? plan, out string error);

    Assert.True(accepted, error);
    Assert.Equal("complete", plan?.Keep.RemoteId);
    Assert.Equal("partial", plan?.Delete.RemoteId);
  }

  [Fact]
  public void Rejects_any_window_containing_more_than_the_exact_two_authorized_activities()
  {
    DateTimeOffset start = DateTimeOffset.Parse("2026-08-26T18:13:10Z");

    bool accepted = GarminLatestTwoReconciliationPlanner.TryCreate(
      Local(start),
      [Candidate("complete", start, 1712, 2.67), Candidate("partial", start.AddMinutes(2), 1592, 2.48), Candidate("older", start.AddMinutes(-2), 1700, 2.60)],
      out GarminLatestTwoReconciliationPlan? plan,
      out string error);

    Assert.False(accepted);
    Assert.Null(plan);
    Assert.Contains("Exactly two", error, StringComparison.Ordinal);
  }

  [Fact]
  public void Rejects_deletion_when_the_second_activity_is_not_a_late_partial_copy()
  {
    DateTimeOffset start = DateTimeOffset.Parse("2026-08-26T18:13:10Z");

    bool accepted = GarminLatestTwoReconciliationPlanner.TryCreate(
      Local(start),
      [Candidate("complete", start, 1712, 2.67), Candidate("unrelated", start.AddMinutes(2), 900, 1.1)],
      out GarminLatestTwoReconciliationPlan? plan,
      out string error);

    Assert.False(accepted);
    Assert.Null(plan);
    Assert.Contains("not a bounded late-start partial", error, StringComparison.Ordinal);
  }

  private static GarminActivityMatchReference Local(DateTimeOffset start) => new(start, 1712, 2.67, 124, 153, []);

  private static GarminWatchActivityCandidate Candidate(string id, DateTimeOffset start, double duration, double distance) =>
    new(id, "treadmill_running", start, duration, distance, null, null, []);
}
