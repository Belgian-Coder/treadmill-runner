using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using TreadmillRunner.Core.Household;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class LocalFirstEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Run_experience_preferences_round_trip_and_reject_invalid_choices()
  {
    using HttpClient client = factory.CreateClient();
    Guid profileId = await CreateProfileAsync(client);

    VersionedRunnerExperiencePreferences defaults = Assert.IsType<VersionedRunnerExperiencePreferences>(
      await client.GetFromJsonAsync<VersionedRunnerExperiencePreferences>(
        $"/api/local-first/profiles/{profileId}/preferences"));
    Assert.Equal(0, defaults.Version);
    Assert.Equal(LiveDisplayStyle.Balanced, defaults.Preferences.DisplayStyle);
    Assert.Equal(3, defaults.Preferences.PrimaryMetrics.Count);

    var request = new
    {
      displayStyle = "LargeText",
      primaryMetrics = new[] { "Distance", "Incline" },
      cues = new
      {
        stepChanges = false,
        heartRateDeparture = true,
        halfway = false,
        connectionProblems = true,
        completion = true,
        volumePercent = 75,
      },
      expectedVersion = 0,
    };
    using HttpResponseMessage savedResponse = await client.PutAsJsonAsync(
      $"/api/local-first/profiles/{profileId}/preferences",
      request);
    Assert.Equal(HttpStatusCode.OK, savedResponse.StatusCode);
    VersionedRunnerExperiencePreferences saved = Assert.IsType<VersionedRunnerExperiencePreferences>(
      await savedResponse.Content.ReadFromJsonAsync<VersionedRunnerExperiencePreferences>());
    Assert.Equal(1, saved.Version);
    Assert.Equal(LiveDisplayStyle.LargeText, saved.Preferences.DisplayStyle);
    Assert.Equal([LiveMetric.Distance, LiveMetric.Incline], saved.Preferences.PrimaryMetrics);
    Assert.False(saved.Preferences.Cues.StepChanges);
    Assert.True(saved.Preferences.Cues.HeartRateDeparture);
    Assert.False(saved.Preferences.Cues.Halfway);
    Assert.True(saved.Preferences.Cues.ConnectionProblems);
    Assert.True(saved.Preferences.Cues.Completion);
    Assert.Equal(75, saved.Preferences.Cues.VolumePercent);

    VersionedRunnerExperiencePreferences reloaded = Assert.IsType<VersionedRunnerExperiencePreferences>(
      await client.GetFromJsonAsync<VersionedRunnerExperiencePreferences>(
        $"/api/local-first/profiles/{profileId}/preferences"));
    Assert.Equal(saved.ProfileId, reloaded.ProfileId);
    Assert.Equal(saved.Version, reloaded.Version);
    Assert.Equal(saved.UpdatedAtUtc, reloaded.UpdatedAtUtc);
    Assert.Equal(saved.Preferences.DisplayStyle, reloaded.Preferences.DisplayStyle);
    Assert.Equal(saved.Preferences.PrimaryMetrics, reloaded.Preferences.PrimaryMetrics);
    Assert.Equal(saved.Preferences.Cues, reloaded.Preferences.Cues);

    using HttpResponseMessage tooFewMetrics = await client.PutAsJsonAsync(
      $"/api/local-first/profiles/{profileId}/preferences",
      request with { primaryMetrics = new[] { "Speed" }, expectedVersion = 1 });
    using HttpResponseMessage invalidVolume = await client.PutAsJsonAsync(
      $"/api/local-first/profiles/{profileId}/preferences",
      request with
      {
        cues = new
        {
          stepChanges = true,
          heartRateDeparture = true,
          halfway = true,
          connectionProblems = true,
          completion = true,
          volumePercent = 101,
        },
        expectedVersion = 1,
      });
    Assert.Equal(HttpStatusCode.BadRequest, tooFewMetrics.StatusCode);
    Assert.Equal(HttpStatusCode.BadRequest, invalidVolume.StatusCode);
  }

  [Fact]
  public async Task Weekly_and_monthly_metric_goals_persist_and_use_completed_session_progress()
  {
    using HttpClient client = factory.CreateClient();
    Guid profileId = await CreateProfileAsync(client);
    DateTimeOffset now = DateTimeOffset.UtcNow;
    await SeedCompletedSessionsAsync(profileId, now);

    (string Kind, string Period, double Target)[] definitions =
    [
      ("Distance", "Weekly", 15),
      ("Minutes", "Weekly", 120),
      ("Sessions", "Weekly", 3),
      ("Distance", "Monthly", 60),
      ("Minutes", "Monthly", 480),
      ("Sessions", "Monthly", 12),
    ];
    foreach ((string kind, string period, double target) in definitions)
    {
      using HttpResponseMessage response = await client.PutAsJsonAsync(
        $"/api/local-first/profiles/{profileId}/goals",
        new
        {
          id = (Guid?)null,
          kind,
          period,
          targetValue = target,
          enabled = true,
          expectedVersion = (int?)null,
        });
      Assert.Equal(HttpStatusCode.OK, response.StatusCode);
      LocalGoalDefinition goal = Assert.IsType<LocalGoalDefinition>(
        await response.Content.ReadFromJsonAsync<LocalGoalDefinition>());
      Assert.Equal(1, goal.Version);
    }

    LocalGoalDefinition[] goals = Assert.IsType<LocalGoalDefinition[]>(
      await client.GetFromJsonAsync<LocalGoalDefinition[]>(
        $"/api/local-first/profiles/{profileId}/goals"));
    Assert.Equal(6, goals.Length);
    Assert.All(definitions, expected => Assert.Contains(goals, goal =>
      goal.Kind == expected.Kind &&
      goal.Period == expected.Period &&
      Math.Abs(goal.TargetValue - expected.Target) < 0.001 &&
      goal.Enabled));

    LocalInsightsResponse insights = Assert.IsType<LocalInsightsResponse>(
      await client.GetFromJsonAsync<LocalInsightsResponse>(
        $"/api/local-first/profiles/{profileId}/insights"));
    Assert.Equal(1, insights.WeeklyTrends.CompletedSessions);
    Assert.Equal(TimeSpan.FromMinutes(30), insights.WeeklyTrends.Duration);
    Assert.Equal(4.25, insights.WeeklyTrends.DistanceKilometers, precision: 3);
    Assert.Equal(2, insights.MonthlyTrends.CompletedSessions);
    Assert.Equal(TimeSpan.FromMinutes(70), insights.MonthlyTrends.Duration);
    Assert.Equal(10.75, insights.MonthlyTrends.DistanceKilometers, precision: 3);
    Assert.Equal(6, insights.Goals.Count);
  }

  private async Task SeedCompletedSessionsAsync(Guid profileId, DateTimeOffset now)
  {
    using IServiceScope scope = factory.Services.CreateScope();
    await using TreadmillRunnerDbContext context = await scope.ServiceProvider
      .GetRequiredService<IDbContextFactory<TreadmillRunnerDbContext>>()
      .CreateDbContextAsync();
    Guid revisionId = Guid.NewGuid();
    var workout = new WorkoutEntity
    {
      Id = Guid.NewGuid(),
      Name = "Metric goal evidence",
      CreatedAtUtc = now.AddDays(-20),
      Revisions =
      [
        new WorkoutRevisionEntity
        {
          Id = revisionId,
          RevisionNumber = 1,
          DefinitionJson = "{\"schemaVersion\":1,\"title\":\"Metric goal evidence\",\"blocks\":[]}",
          ContentSha256 = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"),
          CreatedAtUtc = now.AddDays(-20),
        },
      ],
    };
    context.Workouts.Add(workout);
    context.WorkoutSessions.AddRange(
      CompletedSession(profileId, revisionId, now.AddDays(-1), TimeSpan.FromMinutes(30), 4.25),
      CompletedSession(profileId, revisionId, now.AddDays(-14), TimeSpan.FromMinutes(40), 6.5));
    await context.SaveChangesAsync();
  }

  private static WorkoutSessionEntity CompletedSession(
    Guid profileId,
    Guid revisionId,
    DateTimeOffset endedAt,
    TimeSpan duration,
    double distanceKilometers) => new()
    {
      Id = Guid.NewGuid(),
      UserProfileId = profileId,
      UserProfileName = "Metric runner",
      WorkoutRevisionId = revisionId,
      SelectionSource = "Library",
      SessionOrigin = "Hardware",
      WorkoutTitle = "Metric goal evidence",
      State = "Completed",
      ArmedAtUtc = endedAt - duration,
      StartedAtUtc = endedAt - duration,
      EndedAtUtc = endedAt,
      DurationSeconds = duration.TotalSeconds,
      DistanceKilometers = distanceKilometers,
      EstimatedCalories = 100,
      AverageHeartRateBpm = 135,
      MaximumHeartRateBpm = 150,
      AverageSpeedKph = distanceKilometers / duration.TotalHours,
      AverageInclinePercent = 1,
      MetricAlgorithmVersion = SessionMetricAlgorithms.EstimatedCaloriesV2,
      ControllerConfigurationJson = "{}",
    };

  private static async Task<Guid> CreateProfileAsync(HttpClient client)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = $"Metric runner {Guid.NewGuid():N}",
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 20,
      heartRateZones = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    using JsonDocument document = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
    return document.RootElement.GetProperty("id").GetGuid();
  }

  private sealed record LocalInsightsResponse(
    LocalTrendSummary Trends,
    LocalTrendSummary WeeklyTrends,
    LocalTrendSummary MonthlyTrends,
    IReadOnlyList<LocalGoalDefinition> Goals);
}
