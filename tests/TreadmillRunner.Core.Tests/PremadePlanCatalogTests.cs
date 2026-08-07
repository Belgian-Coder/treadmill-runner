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
      Assert.True(Version.TryParse(template.Version, out _));
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
    Assert.Equal("2.0.0", template.Version);
    Assert.Equal(260, template.VariantCount);
    Assert.Equal(86, template.Sessions.Count(static session => session.AlternativeVariants.Count == 1));
    Assert.Equal(65, template.Sessions.SelectMany(static session => session.AlternativeVariants).Count(static variant => variant.Variant == "hr-alternative"));
    Assert.Equal(21, template.Sessions.SelectMany(static session => session.AlternativeVariants).Count(static variant => variant.Variant == "fixed-fallback"));
  }

  [Fact]
  public void Distance_first_plan_uses_exact_training_rows_without_legacy_stop_tail()
  {
    PremadePlanTemplate template = PremadePlanCatalog.Find("5k-to-10k-distance-first-58");
    PremadePlanSessionTemplate first = template.Sessions[0];
    WorkoutDefinition definition = PremadePlanCatalog.BuildWorkout(first);

    Assert.Equal("W01D1 · Long easy: 10 x 1 min at 8.0 km/h", definition.Title);
    Assert.Equal(21, definition.Blocks.Count);
    WorkoutStep warmup = Assert.IsType<WorkoutStep>(definition.Blocks[0]);
    Assert.Equal(TimeSpan.FromMinutes(5), Assert.IsType<TimeGoal>(warmup.Goal).Duration);
    Assert.Equal(4.5, Assert.IsType<FixedSpeed>(warmup.Speed).KilometersPerHour);
    WorkoutStep cooldown = Assert.IsType<WorkoutStep>(definition.Blocks[^1]);
    Assert.Equal(TimeSpan.FromMinutes(5), Assert.IsType<TimeGoal>(cooldown.Goal).Duration);
    Assert.Equal(4.5, Assert.IsType<FixedSpeed>(cooldown.Speed).KilometersPerHour);
    Assert.DoesNotContain(definition.Blocks.OfType<WorkoutStep>(), static step =>
      step.Speed is FixedSpeed { KilometersPerHour: 0 });
  }

  [Fact]
  public void Distance_first_hr_alternative_retains_bounded_zone_control()
  {
    PremadePlanTemplate template = PremadePlanCatalog.Find("5k-to-10k-distance-first-58");
    PremadePlanVariantTemplate alternative = template.Sessions.Single(static session => session.WeekNumber == 11 && session.SessionNumber == 1)
      .AlternativeVariants.Single();

    HeartRateZoneSpeed directive = Assert.IsType<HeartRateZoneSpeed>(Assert.IsType<WorkoutStep>(alternative.Definition.Blocks[1]).Speed);
    Assert.Equal(2, directive.ZoneNumber);
    Assert.Equal(7.5, directive.InitialKilometersPerHour);
    Assert.Equal(4, directive.MinimumKilometersPerHour);
    Assert.Equal(10, directive.MaximumKilometersPerHour);
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
