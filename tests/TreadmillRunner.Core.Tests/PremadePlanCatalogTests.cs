using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class PremadePlanCatalogTests
{
  [Fact]
  public void Contains_the_complete_versioned_catalog()
  {
    Assert.Equal(16, PremadePlanCatalog.All.Count);
    Assert.Equal(16, PremadePlanCatalog.All.Select(static template => template.Id).Distinct().Count());
    Assert.All(PremadePlanCatalog.All, template =>
    {
      Assert.Equal(PremadePlanCatalog.CurrentVersion, template.Version);
      Assert.Equal(template.Weeks * template.SessionsPerWeek, template.SessionCount);
      Assert.Equal(Enumerable.Range(1, template.SessionCount), template.Sessions.Select(static session => session.Position));
      Assert.DoesNotContain("rehab", template.Description, StringComparison.OrdinalIgnoreCase);
    });
  }

  [Fact]
  public void Distance_first_plan_has_58_weeks_and_174_positions()
  {
    PremadePlanTemplate template = PremadePlanCatalog.Find("5k-to-10k-distance-first-58");

    Assert.Equal(58, template.Weeks);
    Assert.Equal(174, template.SessionCount);
    Assert.Equal(58, template.Sessions.Select(static session => session.WeekNumber).Distinct().Count());
    Assert.Equal(4, template.Sessions.Select(static session => session.Phase).Distinct().Count());
  }

  [Fact]
  public void Heart_rate_templates_use_zone_references_without_personal_bpm()
  {
    PremadePlanTemplate[] heartRate = PremadePlanCatalog.All.Where(static template => template.RequiresHeartRate).ToArray();

    Assert.Equal(2, heartRate.Length);
    foreach (PremadePlanSessionTemplate session in heartRate.SelectMany(static template => template.Sessions))
    {
      WorkoutDefinition definition = PremadePlanCatalog.BuildWorkout(session);
      HeartRateZoneSpeed directive = Assert.IsType<HeartRateZoneSpeed>(Assert.IsType<WorkoutStep>(definition.Blocks[1]).Speed);
      Assert.InRange(directive.ZoneNumber, 1, 5);
    }
  }

  [Fact]
  public void Program_limit_accepts_long_catalog_plan_but_remains_bounded()
  {
    PremadePlanTemplate template = PremadePlanCatalog.Find("5k-to-10k-distance-first-58");
    WorkoutProgramItem[] items = template.Sessions.Select(session => new WorkoutProgramItem(
      Guid.NewGuid(), Guid.NewGuid(), session.Position, session.WeekNumber, session.SessionNumber, session.Phase)).ToArray();

    var revision = new WorkoutProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, template.Name, template.Description, template.Goal, items,
      template.Id, template.Version, Guid.NewGuid());

    Assert.Equal(174, revision.Items.Count);
    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, "Too long", null, "Test",
      Enumerable.Range(1, WorkoutProgramLimits.MaximumItems + 1)
        .Select(position => new WorkoutProgramItem(Guid.NewGuid(), Guid.NewGuid(), position)).ToArray()));
  }

  [Fact]
  public void Content_hash_is_deterministic()
  {
    PremadePlanTemplate template = PremadePlanCatalog.Find("first-5k");

    Assert.Equal(template.ContentSha256, PremadePlanCatalog.Find("first-5k").ContentSha256);
    Assert.Equal(64, template.ContentSha256.Length);
  }
}
