using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PremadePlanEndpointTests(PlanningGatewayFactory factory)
  : IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Archived_template_plan_is_not_reported_as_added_and_can_be_added_again()
  {
    using HttpClient client = factory.CreateClient();
    Guid profileId = await CreateProfileAsync(client, includeAllZones: true);
    var request = new
    {
      operationId = Guid.NewGuid(),
      profileId,
      templateId = "getting-started",
      templateVersion = "1.0.0",
      freshCopy = false,
    };

    using HttpResponseMessage firstResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request);
    Assert.Equal(HttpStatusCode.Created, firstResponse.StatusCode);
    Guid firstProgramId = (await ReadJsonAsync(firstResponse)).GetProperty("programId").GetGuid();

    using HttpResponseMessage archiveResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{firstProgramId}/archive",
      new { operationId = Guid.NewGuid() });
    Assert.Equal(HttpStatusCode.NoContent, archiveResponse.StatusCode);

    JsonElement[] catalog = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/premade-plans?profileId={profileId}"))!;
    JsonElement template = Assert.Single(catalog, item => item.GetProperty("id").GetString() == "getting-started");
    Assert.False(template.GetProperty("alreadyAdded").GetBoolean());

    using HttpResponseMessage secondResponse = await client.PostAsJsonAsync(
      "/api/planning/premade-plans/materialize",
      request with { operationId = Guid.NewGuid() });
    Assert.Equal(HttpStatusCode.Created, secondResponse.StatusCode);
    JsonElement second = await ReadJsonAsync(secondResponse);
    Assert.False(second.GetProperty("alreadyAdded").GetBoolean());
    Assert.NotEqual(firstProgramId, second.GetProperty("programId").GetGuid());
    Assert.Equal(2, second.GetProperty("copyNumber").GetInt32());
  }

  [Fact]
  public async Task Catalog_materializes_long_plan_idempotently_and_keeps_it_profile_scoped()
  {
    using HttpClient client = factory.CreateClient();
    Guid profileId = await CreateProfileAsync(client, includeAllZones: true);
    Guid otherProfileId = await CreateProfileAsync(client, includeAllZones: true);

    JsonElement[] catalog = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/premade-plans?profileId={profileId}"))!;
    Assert.Equal(16, catalog.Length);
    JsonElement longPlan = Assert.Single(catalog, item => item.GetProperty("id").GetString() == "5k-to-10k-distance-first-58");
    Assert.Equal(174, longPlan.GetProperty("sessionCount").GetInt32());
    Assert.False(longPlan.GetProperty("alreadyAdded").GetBoolean());

    Guid operationId = Guid.NewGuid();
    var request = new
    {
      operationId,
      profileId,
      templateId = "5k-to-10k-distance-first-58",
      templateVersion = longPlan.GetProperty("version").GetString()!,
      freshCopy = false,
    };
    using HttpResponseMessage createdResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request);
    Assert.Equal(HttpStatusCode.Created, createdResponse.StatusCode);
    JsonElement created = await ReadJsonAsync(createdResponse);
    Assert.Equal(174, created.GetProperty("positionCount").GetInt32());
    Assert.InRange(created.GetProperty("uniqueWorkoutCount").GetInt32(), 174, 260);
    Guid programId = created.GetProperty("programId").GetGuid();

    using HttpResponseMessage replayResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request);
    Assert.Equal(HttpStatusCode.Created, replayResponse.StatusCode);
    JsonElement replay = await ReadJsonAsync(replayResponse);
    Assert.Equal(programId, replay.GetProperty("programId").GetGuid());
    Assert.True(replay.GetProperty("replayed").GetBoolean());

    using HttpResponseMessage alreadyResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request with { operationId = Guid.NewGuid() });
    Assert.Equal(HttpStatusCode.OK, alreadyResponse.StatusCode);
    JsonElement already = await ReadJsonAsync(alreadyResponse);
    Assert.True(already.GetProperty("alreadyAdded").GetBoolean());
    Assert.Equal(programId, already.GetProperty("programId").GetGuid());

    JsonElement[] ownPrograms = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={profileId}"))!;
    JsonElement installed = Assert.Single(ownPrograms, item => item.GetProperty("id").GetGuid() == programId);
    Assert.Equal("5k-to-10k-distance-first-58", installed.GetProperty("templateId").GetString());
    Assert.Equal(profileId, installed.GetProperty("ownerProfileId").GetGuid());
    Assert.Equal(174, installed.GetProperty("itemCount").GetInt32());
    Assert.False(installed.TryGetProperty("items", out _));
    JsonElement installedDetails = (await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/programs/{programId}?profileId={profileId}"));
    Assert.Equal(174, installedDetails.GetProperty("items").GetArrayLength());
    Assert.Equal("Foundation", installedDetails.GetProperty("items")[0].GetProperty("phase").GetString());
    Assert.Equal(1, installedDetails.GetProperty("items")[0].GetProperty("weekNumber").GetInt32());

    JsonElement[] otherPrograms = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={otherProfileId}"))!;
    Assert.DoesNotContain(otherPrograms, item => item.GetProperty("id").GetGuid() == programId);

    // Releases before TR-024 stored generated template workouts as Structured. Proven
    // template provenance must keep those legacy rows out of the reusable library.
    using (IServiceScope scope = factory.Services.CreateScope())
    await using (TreadmillRunnerDbContext context = await scope.ServiceProvider
      .GetRequiredService<IDbContextFactory<TreadmillRunnerDbContext>>()
      .CreateDbContextAsync())
    {
      Guid[] generatedWorkoutIds = await context.WorkoutProgramItems
        .Where(item => item.WorkoutProgramRevision.WorkoutProgramId == programId)
        .Join(context.WorkoutRevisions,
          item => item.WorkoutRevisionId,
          revision => revision.Id,
          (_, revision) => revision.WorkoutId)
        .Distinct()
        .ToArrayAsync();
      await context.Workouts
        .Where(workout => generatedWorkoutIds.Contains(workout.Id))
        .ExecuteUpdateAsync(setters => setters.SetProperty(workout => workout.Kind, nameof(WorkoutKind.Structured)));
    }

    JsonElement[] publicWorkouts = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/workouts"))!;
    Assert.DoesNotContain(publicWorkouts, workout =>
      string.Equals(workout.GetProperty("kind").GetString(), "PlanInternal", StringComparison.Ordinal));
    Assert.DoesNotContain(publicWorkouts, workout =>
      workout.TryGetProperty("description", out JsonElement description) &&
      (description.GetString()?.StartsWith("Premade plan workout", StringComparison.Ordinal) ?? false));

    var scheduleRequest = new
    {
      operationId = Guid.NewGuid(),
      profileId,
      expectedProgramRevisionId = installed.GetProperty("revisionId").GetGuid(),
      expectedActiveRunId = (Guid?)null,
      expectedActiveRunVersion = (int?)null,
      scheduledStartDate = new DateOnly(2026, 8, 10),
      scheduledWeekdayMask = 37,
      scheduleTimeZoneId = "Europe/Brussels",
    };
    using HttpResponseMessage wrongOwnerStart = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start",
      scheduleRequest with { operationId = Guid.NewGuid(), profileId = otherProfileId });
    Assert.Equal(HttpStatusCode.Forbidden, wrongOwnerStart.StatusCode);

    using HttpResponseMessage startResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start", scheduleRequest);
    Assert.Equal(HttpStatusCode.OK, startResponse.StatusCode);
    JsonElement started = await ReadJsonAsync(startResponse);
    Assert.Equal("2026-08-10", started.GetProperty("scheduledStartDate").GetString());
    Assert.Equal(37, started.GetProperty("scheduledWeekdayMask").GetInt32());

    JsonElement calendar = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-08-16");
    JsonElement[] scheduledDays = calendar.GetProperty("days").EnumerateArray().ToArray();
    Assert.Equal(["2026-08-10", "2026-08-12", "2026-08-15"],
      scheduledDays.Select(day => day.GetProperty("date").GetString()));
    for (int index = 0; index < scheduledDays.Length; index++)
    {
      JsonElement option = scheduledDays[index].GetProperty("options").EnumerateArray().First();
      Assert.Equal("Program", option.GetProperty("source").GetString());
      Assert.Equal(started.GetProperty("id").GetGuid(), option.GetProperty("programRunId").GetGuid());
      Assert.Equal(index + 1, option.GetProperty("programPosition").GetInt32());
      Assert.Equal(174, option.GetProperty("programTotal").GetInt32());
    }

    Guid runId = started.GetProperty("id").GetGuid();
    Guid firstItemId = installedDetails.GetProperty("items")[0].GetProperty("id").GetGuid();
    var moveRequest = new
    {
      operationId = (Guid?)null,
      profileId,
      programItemId = firstItemId,
      action = "MoveFollowing",
      targetDate = (DateOnly?)new DateOnly(2026, 8, 11),
      expectedRunVersion = (int?)null,
    };
    JsonElement movePreview = await (await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/schedule/preview", moveRequest)).Content.ReadFromJsonAsync<JsonElement>();
    Assert.True(movePreview.GetProperty("canApply").GetBoolean());
    Assert.Equal(174, movePreview.GetProperty("impacts").GetArrayLength());
    int runVersion = movePreview.GetProperty("runVersion").GetInt32();
    using HttpResponseMessage moveResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/schedule/apply",
      moveRequest with { operationId = Guid.NewGuid(), expectedRunVersion = runVersion });
    Assert.Equal(HttpStatusCode.OK, moveResponse.StatusCode);
    JsonElement moved = await ReadJsonAsync(moveResponse);
    Assert.Equal(runVersion + 1, moved.GetProperty("runVersion").GetInt32());

    JsonElement movedCalendar = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-08-17");
    Assert.Equal(["2026-08-11", "2026-08-13", "2026-08-16"],
      movedCalendar.GetProperty("days").EnumerateArray().Take(3).Select(day => day.GetProperty("date").GetString()));

    var skipRequest = moveRequest with
    {
      operationId = (Guid?)Guid.NewGuid(),
      action = "Skip",
      targetDate = (DateOnly?)null,
      expectedRunVersion = (int?)moved.GetProperty("runVersion").GetInt32(),
    };
    using HttpResponseMessage skipResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/schedule/apply", skipRequest);
    Assert.Equal(HttpStatusCode.OK, skipResponse.StatusCode);
    JsonElement programAfterSkip = Assert.Single(
      (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={profileId}"))!,
      item => item.GetProperty("id").GetGuid() == programId);
    Assert.Equal(1, programAfterSkip.GetProperty("skippedItemCount").GetInt32());
    Assert.Equal(installedDetails.GetProperty("items")[1].GetProperty("id").GetGuid(), programAfterSkip.GetProperty("nextItemId").GetGuid());

    using HttpResponseMessage duplicateResponse = await client.PostAsJsonAsync(
      "/api/planning/premade-plans/materialize", request with { operationId = Guid.NewGuid(), freshCopy = true });
    Assert.Equal(HttpStatusCode.OK, duplicateResponse.StatusCode);
    JsonElement duplicate = await ReadJsonAsync(duplicateResponse);
    Assert.Equal(programId, duplicate.GetProperty("programId").GetGuid());
    Assert.Equal(1, duplicate.GetProperty("copyNumber").GetInt32());
    Assert.True(duplicate.GetProperty("alreadyAdded").GetBoolean());

    using HttpResponseMessage editResponse = await client.PostAsJsonAsync($"/api/planning/programs/{programId}/revisions", new
    {
      operationId = Guid.NewGuid(),
      name = "Changed",
      description = "Must stay immutable",
      category = "10K",
      items = Array.Empty<object>(),
    });
    Assert.Equal(HttpStatusCode.Conflict, editResponse.StatusCode);
  }

  [Fact]
  public async Task Default_training_days_preview_and_apply_are_profile_scoped_idempotent_and_collision_safe()
  {
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<TimeProvider>();
      services.AddSingleton<TimeProvider>(new FixedTimeProvider(new DateTimeOffset(2026, 8, 4, 10, 0, 0, TimeSpan.Zero)));
    }));
    using HttpClient client = application.CreateClient();
    Guid profileId = await CreateProfileAsync(client, includeAllZones: true);
    Guid otherProfileId = await CreateProfileAsync(client, includeAllZones: true);
    var materializeRequest = new
    {
      operationId = Guid.NewGuid(),
      profileId,
      templateId = "getting-started",
      templateVersion = "1.0.0",
      freshCopy = false,
    };
    using HttpResponseMessage materializeResponse = await client.PostAsJsonAsync(
      "/api/planning/premade-plans/materialize", materializeRequest);
    Assert.Equal(HttpStatusCode.Created, materializeResponse.StatusCode);
    Guid programId = (await ReadJsonAsync(materializeResponse)).GetProperty("programId").GetGuid();
    JsonElement program = Assert.Single(
      (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={profileId}"))!,
      item => item.GetProperty("id").GetGuid() == programId);

    using HttpResponseMessage startResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start",
      new
      {
        operationId = Guid.NewGuid(),
        profileId,
        expectedProgramRevisionId = program.GetProperty("revisionId").GetGuid(),
        expectedActiveRunId = (Guid?)null,
        expectedActiveRunVersion = (int?)null,
        scheduledStartDate = new DateOnly(2026, 8, 10),
        scheduledWeekdayMask = 37,
        scheduleTimeZoneId = "Europe/Brussels",
      });
    Assert.Equal(HttpStatusCode.OK, startResponse.StatusCode);
    JsonElement started = await ReadJsonAsync(startResponse);
    Guid runId = started.GetProperty("id").GetGuid();

    var previewRequest = new
    {
      operationId = (Guid?)null,
      profileId,
      weekdayMask = 69,
      effectiveDate = new DateOnly(2026, 8, 10),
      expectedRunVersion = (int?)null,
      expectedRevision = (string?)null,
    };
    using HttpResponseMessage wrongOwnerPreview = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/preview",
      previewRequest with { profileId = otherProfileId });
    Assert.Equal(HttpStatusCode.NotFound, wrongOwnerPreview.StatusCode);

    using HttpResponseMessage previewResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/preview", previewRequest);
    Assert.Equal(HttpStatusCode.OK, previewResponse.StatusCode);
    JsonElement preview = await ReadJsonAsync(previewResponse);
    Assert.True(preview.GetProperty("canApply").GetBoolean());
    Assert.Equal(37, preview.GetProperty("currentWeekdayMask").GetInt32());
    Assert.Equal(69, preview.GetProperty("newWeekdayMask").GetInt32());
    Assert.NotEmpty(preview.GetProperty("impacts").EnumerateArray());

    JsonElement beforeApply = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-09-06");
    Assert.Equal(
      Enumerable.Range(1, 12),
      ProgramSchedule(beforeApply, runId).Select(static item => item.Position));

    Guid operationId = Guid.NewGuid();
    var applyRequest = previewRequest with
    {
      operationId = (Guid?)operationId,
      expectedRunVersion = (int?)preview.GetProperty("runVersion").GetInt32(),
      expectedRevision = preview.GetProperty("revision").GetString(),
    };
    using HttpResponseMessage applyResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/apply", applyRequest);
    Assert.Equal(HttpStatusCode.OK, applyResponse.StatusCode);
    JsonElement applied = await ReadJsonAsync(applyResponse);
    Assert.Equal(preview.GetProperty("runVersion").GetInt32() + 1, applied.GetProperty("runVersion").GetInt32());

    using HttpResponseMessage replayResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/apply", applyRequest);
    Assert.Equal(HttpStatusCode.OK, replayResponse.StatusCode);
    JsonElement replay = await ReadJsonAsync(replayResponse);
    Assert.Equal(applied.GetProperty("runVersion").GetInt32(), replay.GetProperty("runVersion").GetInt32());
    JsonElement afterReplay = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-09-06");
    IReadOnlyList<(int Position, DateOnly Date)> afterReplaySchedule = ProgramSchedule(afterReplay, runId);

    using HttpResponseMessage conflictResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/apply",
      applyRequest with { weekdayMask = 44 });
    Assert.Equal(HttpStatusCode.Conflict, conflictResponse.StatusCode);

    JsonElement afterApply = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-09-06");
    IReadOnlyList<(int Position, DateOnly Date)> afterApplySchedule = ProgramSchedule(afterApply, runId);
    Assert.Equal(
      [
        (1, new DateOnly(2026, 8, 10)), (2, new DateOnly(2026, 8, 12)),
        (3, new DateOnly(2026, 8, 16)), (4, new DateOnly(2026, 8, 17)),
        (5, new DateOnly(2026, 8, 19)), (6, new DateOnly(2026, 8, 23)),
        (7, new DateOnly(2026, 8, 24)), (8, new DateOnly(2026, 8, 26)),
        (9, new DateOnly(2026, 8, 30)), (10, new DateOnly(2026, 8, 31)),
        (11, new DateOnly(2026, 9, 2)), (12, new DateOnly(2026, 9, 6)),
      ],
      afterApplySchedule);
    Assert.Equal(afterApplySchedule, afterReplaySchedule);

    var reversePreviewRequest = previewRequest with
    {
      weekdayMask = 37,
      effectiveDate = new DateOnly(2026, 8, 12),
    };
    using HttpResponseMessage reversePreviewResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/preview", reversePreviewRequest);
    Assert.Equal(HttpStatusCode.OK, reversePreviewResponse.StatusCode);
    JsonElement reversePreview = await ReadJsonAsync(reversePreviewResponse);
    Assert.True(reversePreview.GetProperty("canApply").GetBoolean());
    Assert.Equal(69, reversePreview.GetProperty("currentWeekdayMask").GetInt32());
    Assert.Equal(37, reversePreview.GetProperty("newWeekdayMask").GetInt32());
    Assert.NotEmpty(reversePreview.GetProperty("impacts").EnumerateArray());

    using HttpResponseMessage reverseApplyResponse = await client.PostAsJsonAsync(
      $"/api/planning/calendar/program-runs/{runId}/default-days/apply",
      reversePreviewRequest with
      {
        operationId = (Guid?)Guid.NewGuid(),
        expectedRunVersion = (int?)reversePreview.GetProperty("runVersion").GetInt32(),
        expectedRevision = reversePreview.GetProperty("revision").GetString(),
      });
    Assert.Equal(HttpStatusCode.OK, reverseApplyResponse.StatusCode);

    JsonElement afterReverse = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from=2026-08-10&to=2026-09-06");
    Assert.Equal(
      [
        (1, new DateOnly(2026, 8, 10)), (2, new DateOnly(2026, 8, 12)),
        (3, new DateOnly(2026, 8, 15)), (4, new DateOnly(2026, 8, 17)),
        (5, new DateOnly(2026, 8, 19)), (6, new DateOnly(2026, 8, 22)),
        (7, new DateOnly(2026, 8, 24)), (8, new DateOnly(2026, 8, 26)),
        (9, new DateOnly(2026, 8, 29)), (10, new DateOnly(2026, 8, 31)),
        (11, new DateOnly(2026, 9, 2)), (12, new DateOnly(2026, 9, 5)),
      ],
      ProgramSchedule(afterReverse, runId));
  }

  private static IReadOnlyList<(int Position, DateOnly Date)> ProgramSchedule(JsonElement calendar, Guid runId) =>
    calendar.GetProperty("days").EnumerateArray()
      .SelectMany(day => day.GetProperty("options").EnumerateArray()
        .Where(option => option.TryGetProperty("programRunId", out JsonElement value) && value.GetGuid() == runId)
        .Select(option => (
          Position: option.GetProperty("programPosition").GetInt32(),
          Date: DateOnly.Parse(day.GetProperty("date").GetString()!))))
      .Distinct()
      .OrderBy(static item => item.Position)
      .ToArray();

  private sealed class FixedTimeProvider(DateTimeOffset nowUtc) : TimeProvider
  {
    public override DateTimeOffset GetUtcNow() => nowUtc;
  }

  [Fact]
  public async Task Preview_resolves_profile_zones_and_blocks_missing_heart_rate_zone()
  {
    using HttpClient client = factory.CreateClient();
    Guid readyProfileId = await CreateProfileAsync(client, includeAllZones: true);
    Guid incompleteProfileId = await CreateProfileAsync(client, includeAllZones: false);

    JsonElement ready = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/premade-plans/heart-rate-5k-gentle/preview?profileId={readyProfileId}&version=1.0.0");
    Assert.True(ready.GetProperty("compatible").GetBoolean());
    Assert.True(ready.GetProperty("heartRateZonesReady").GetBoolean());
    Assert.Equal("heart-rate-5k-gentle", ready.GetProperty("template").GetProperty("id").GetString());

    JsonElement incomplete = await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/premade-plans/heart-rate-5k-gentle/preview?profileId={incompleteProfileId}&version=1.0.0");
    Assert.False(incomplete.GetProperty("compatible").GetBoolean());
    Assert.False(incomplete.GetProperty("heartRateZonesReady").GetBoolean());
    Assert.Contains("heart-rate zones", incomplete.GetProperty("compatibilityMessage").GetString(), StringComparison.OrdinalIgnoreCase);
  }

  private static async Task<Guid> CreateProfileAsync(HttpClient client, bool includeAllZones)
  {
    object[] zones = includeAllZones
      ?
      [
        new { number = 1, name = "Warm up", minimumBpm = 95, maximumBpm = 113 },
        new { number = 2, name = "Easy", minimumBpm = 114, maximumBpm = 132 },
        new { number = 3, name = "Aerobic", minimumBpm = 133, maximumBpm = 151 },
        new { number = 4, name = "Threshold", minimumBpm = 152, maximumBpm = 170 },
        new { number = 5, name = "Maximum", minimumBpm = 171, maximumBpm = 190 },
      ]
      : [new { number = 1, name = "Warm up", minimumBpm = 95, maximumBpm = 113 }];
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = $"Catalog runner {Guid.NewGuid():N}",
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 12.0,
      heartRateZones = zones,
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    return (await ReadJsonAsync(response)).GetProperty("id").GetGuid();
  }

  private static async Task<JsonElement> ReadJsonAsync(HttpResponseMessage response)
  {
    using JsonDocument document = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
    return document.RootElement.Clone();
  }
}
