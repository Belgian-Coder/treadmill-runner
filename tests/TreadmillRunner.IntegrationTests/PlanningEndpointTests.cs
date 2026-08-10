using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.IntegrationTests;

public sealed class PlanningEndpointTests(PlanningGatewayFactory factory) : IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Profiles_support_zones_archive_and_stable_operation_replay()
  {
    using HttpClient client = factory.CreateClient();
    Guid operationId = Guid.NewGuid();
    var request = new
    {
      operationId,
      displayName = $"Runner {operationId:N}",
      unitSystem = "Metric",
      weightKilograms = 72.5,
      maximumHeartRateBpm = 188,
      maximumSpeedKph = 20.0,
      heartRateZones = new[] { new { number = 2, name = "Aerobic", minimumBpm = 125, maximumBpm = 145 } },
      heartRateIncreaseStepKph = 0.3,
      heartRateIncreaseCooldownSeconds = 45,
      heartRateDecreaseStepKph = 0.7,
      heartRateDecreaseCooldownSeconds = 20,
      expectedVersion = (int?)null,
    };

    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/profiles", request);
    using HttpResponseMessage replayed = await client.PostAsJsonAsync("/api/planning/profiles", request);

    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    Assert.Equal(HttpStatusCode.Created, replayed.StatusCode);
    JsonElement first = await ReadJsonAsync(created);
    JsonElement replay = await ReadJsonAsync(replayed);
    Assert.Equal(first.GetProperty("id").GetGuid(), replay.GetProperty("id").GetGuid());
    Assert.Equal("Aerobic", first.GetProperty("heartRateZones")[0].GetProperty("name").GetString());
    Assert.Equal(0.3, first.GetProperty("heartRateIncreaseStepKph").GetDouble(), precision: 3);
    Assert.Equal(45, first.GetProperty("heartRateIncreaseCooldownSeconds").GetInt32());
    Assert.Equal(0.7, first.GetProperty("heartRateDecreaseStepKph").GetDouble(), precision: 3);
    Assert.Equal(20, first.GetProperty("heartRateDecreaseCooldownSeconds").GetInt32());
    using HttpResponseMessage changedCreate = await client.PostAsJsonAsync(
      "/api/planning/profiles",
      new
      {
        request.operationId,
        displayName = request.displayName,
        request.unitSystem,
        weightKilograms = 73.0,
        request.maximumHeartRateBpm,
        request.maximumSpeedKph,
        request.heartRateZones,
        request.heartRateIncreaseStepKph,
        request.heartRateIncreaseCooldownSeconds,
        request.heartRateDecreaseStepKph,
        request.heartRateDecreaseCooldownSeconds,
        request.expectedVersion,
      });
    Assert.Equal(HttpStatusCode.Conflict, changedCreate.StatusCode);

    Guid id = first.GetProperty("id").GetGuid();
    using HttpResponseMessage changedAction = await client.PostAsJsonAsync(
      $"/api/planning/profiles/{id}/archive",
      new { operationId, expectedVersion = 1 });
    Assert.Equal(HttpStatusCode.Conflict, changedAction.StatusCode);
    JsonElement beforeUpdate = (await client.GetFromJsonAsync<JsonElement>($"/api/planning/profiles/{id}"));
    Assert.Equal(1, beforeUpdate.GetProperty("version").GetInt32());
    Guid updateOperation = Guid.NewGuid();
    var update = new
    {
      operationId = updateOperation,
      displayName = $"Updated {operationId:N}",
      unitSystem = "Metric",
      weightKilograms = 72.5,
      maximumHeartRateBpm = 188,
      maximumSpeedKph = 20.0,
      heartRateZones = new[] { new { number = 2, name = "Aerobic", minimumBpm = 125, maximumBpm = 145 } },
      heartRateIncreaseStepKph = 0.4,
      heartRateIncreaseCooldownSeconds = 60,
      heartRateDecreaseStepKph = 0.8,
      heartRateDecreaseCooldownSeconds = 30,
      expectedVersion = (int?)1,
    };
    using HttpResponseMessage updated = await client.PutAsJsonAsync($"/api/planning/profiles/{id}", update);
    using HttpResponseMessage wrongUpdateTarget = await client.PutAsJsonAsync(
      $"/api/planning/profiles/{Guid.NewGuid()}",
      update);
    Assert.True(
      updated.StatusCode == HttpStatusCode.OK,
      $"Expected profile update to succeed, but received {updated.StatusCode}: {await updated.Content.ReadAsStringAsync()}");
    Assert.Equal(HttpStatusCode.Conflict, wrongUpdateTarget.StatusCode);
    JsonElement updatedProfile = await updated.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(0.4, updatedProfile.GetProperty("heartRateIncreaseStepKph").GetDouble(), precision: 3);
    Assert.Equal(60, updatedProfile.GetProperty("heartRateIncreaseCooldownSeconds").GetInt32());
    Assert.Equal(0.8, updatedProfile.GetProperty("heartRateDecreaseStepKph").GetDouble(), precision: 3);
    Assert.Equal(30, updatedProfile.GetProperty("heartRateDecreaseCooldownSeconds").GetInt32());

    Guid archiveOperation = Guid.NewGuid();
    using HttpResponseMessage archived = await client.PostAsJsonAsync(
      $"/api/planning/profiles/{id}/archive",
      new { operationId = archiveOperation, expectedVersion = 2 });
    Assert.Equal(HttpStatusCode.NoContent, archived.StatusCode);
    using HttpResponseMessage wrongArchiveTarget = await client.PostAsJsonAsync(
      $"/api/planning/profiles/{Guid.NewGuid()}/archive",
      new { operationId = archiveOperation, expectedVersion = 2 });
    Assert.Equal(HttpStatusCode.Conflict, wrongArchiveTarget.StatusCode);
    Assert.DoesNotContain(
      (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/profiles"))!,
      profile => profile.GetProperty("id").GetGuid() == id);
  }

  [Fact]
  public async Task Profile_with_null_zone_returns_bad_request()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = $"Invalid profile {Guid.NewGuid():N}",
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = (int?)null,
      maximumSpeedKph = (double?)null,
      heartRateZones = new object?[] { null },
      expectedVersion = (int?)null,
    });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
  }

  [Fact]
  public async Task Workouts_round_trip_full_definition_and_append_immutable_revision()
  {
    using HttpClient client = factory.CreateClient();
    object Step(string goal, double value, string speed, string incline) => new
    {
      kind = "step",
      repetitions = 1,
      blocks = Array.Empty<object>(),
      goalKind = goal,
      goalValue = value,
      speedKind = speed,
      speedStartKph = 6.0,
      speedEndKph = 10.0,
      heartRateMinimumBpm = 120,
      heartRateMaximumBpm = 150,
      heartRateZoneNumber = 2,
      heartRateInitialSpeedKph = 7.0,
      heartRateMinimumSpeedKph = 4.0,
      heartRateMaximumSpeedKph = 11.0,
      inclineKind = incline,
      inclineStartPercent = 1.0,
      inclineEndPercent = 4.0,
      cue = "Relax",
      notes = "Acceptance coverage",
    };
    object[] blocks =
    [
      Step("distance", 1.25, "ramp", "ramp"),
      new
      {
        kind = "repeat", repetitions = 3, blocks = new[] { Step("time", 2.0, "heartRateZone", "fixed") },
        goalKind = "time", goalValue = 1.0, speedKind = "open", speedStartKph = 0.0, speedEndKph = 0.0,
        heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
        heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
        inclineKind = "fixed", inclineStartPercent = 0.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
      },
    ];
    Guid operationId = Guid.NewGuid();
    var createRequest = new { operationId, name = $"Mixed {operationId:N}", description = "All directives", blocks };

    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/workouts", createRequest);
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    JsonElement saved = await ReadJsonAsync(created);
    Guid workoutId = saved.GetProperty("workoutId").GetGuid();
    using HttpResponseMessage replayed = await client.PostAsJsonAsync("/api/planning/workouts", createRequest);
    Assert.Equal(saved.GetProperty("revisionId").GetGuid(), (await ReadJsonAsync(replayed)).GetProperty("revisionId").GetGuid());
    using HttpResponseMessage changedCreate = await client.PostAsJsonAsync(
      "/api/planning/workouts",
      new { operationId, name = $"Changed {operationId:N}", description = "All directives", blocks });
    Assert.Equal(HttpStatusCode.Conflict, changedCreate.StatusCode);

    JsonElement[] revisions = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/workouts/{workoutId}/revisions"))!;
    Assert.Equal("distance", revisions[0].GetProperty("blocks")[0].GetProperty("goalKind").GetString());
    Assert.Equal("repeat", revisions[0].GetProperty("blocks")[1].GetProperty("kind").GetString());
    Guid firstRevisionId = revisions[0].GetProperty("revisionId").GetGuid();
    JsonElement revisionById = (await client.GetFromJsonAsync<JsonElement>($"/api/planning/workouts/revisions/{firstRevisionId}"));
    Assert.Equal(workoutId, revisionById.GetProperty("workoutId").GetGuid());
    Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync($"/api/planning/workouts/revisions/{Guid.NewGuid()}")).StatusCode);

    Guid appendOperation = Guid.NewGuid();
    var appendRequest = new
    {
      operationId = appendOperation,
      name = $"Mixed {operationId:N} v2",
      description = "New revision",
      blocks,
    };
    using HttpResponseMessage appended = await client.PostAsJsonAsync(
      $"/api/planning/workouts/{workoutId}/revisions",
      appendRequest);
    Assert.Equal(2, (await ReadJsonAsync(appended)).GetProperty("revisionNumber").GetInt32());
    using HttpResponseMessage wrongAppendTarget = await client.PostAsJsonAsync(
      $"/api/planning/workouts/{Guid.NewGuid()}/revisions",
      appendRequest);
    Assert.Equal(HttpStatusCode.Conflict, wrongAppendTarget.StatusCode);
    Assert.Equal(2, (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/workouts/{workoutId}/revisions"))!.Length);

    JsonElement listed = (await client.GetFromJsonAsync<JsonElement[]>("/api/planning/workouts"))!
      .Single(item => item.GetProperty("id").GetGuid() == workoutId);
    Assert.Equal("New revision", listed.GetProperty("description").GetString());
    Assert.Equal("HR intervals", listed.GetProperty("structureLabel").GetString());
    Assert.Equal("Time + distance", listed.GetProperty("goalLabel").GetString());
    Assert.Equal("Z2 · 4–11 km/h", listed.GetProperty("speedLabel").GetString());
    Assert.Equal("1–4% incline", listed.GetProperty("inclineLabel").GetString());
    Assert.True(listed.GetProperty("usesHeartRate").GetBoolean());

    Guid archiveOperation = Guid.NewGuid();
    using HttpResponseMessage archived = await client.PostAsJsonAsync(
      $"/api/planning/workouts/{workoutId}/archive",
      new { operationId = archiveOperation });
    using HttpResponseMessage wrongArchiveTarget = await client.PostAsJsonAsync(
      $"/api/planning/workouts/{Guid.NewGuid()}/archive",
      new { operationId = archiveOperation });
    Assert.Equal(HttpStatusCode.NoContent, archived.StatusCode);
    Assert.Equal(HttpStatusCode.Conflict, wrongArchiveTarget.StatusCode);
  }

  [Fact]
  public async Task Workout_with_null_nested_block_or_discriminator_returns_bad_request()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage nullBlock = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = "Invalid nested block",
      description = (string?)null,
      blocks = new[] { new
      {
        kind = "repeat", repetitions = 2, blocks = new object?[] { null }, goalKind = "time", goalValue = 1.0,
        speedKind = "open", speedStartKph = 0.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
        heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
        heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
        inclineStartPercent = 0.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
      } },
    });
    using HttpResponseMessage nullDiscriminator = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = "Invalid discriminator",
      description = (string?)null,
      blocks = new[] { new
      {
        kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = (string?)null, goalValue = 1.0,
        speedKind = "fixed", speedStartKph = 6.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
        heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
        heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
        inclineStartPercent = 0.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
      } },
    });

    Assert.Equal(HttpStatusCode.BadRequest, nullBlock.StatusCode);
    Assert.Equal(HttpStatusCode.BadRequest, nullDiscriminator.StatusCode);
  }

  [Fact]
  public async Task Import_preview_rejects_oversized_multipart_and_extra_file()
  {
    using HttpClient client = factory.CreateClient();
    const string validSource = """
      {"schemaVersion":1,"title":"Multipart plan","description":null,"blocks":[{"kind":"step","goal":{"kind":"time","durationTicks":600000000},"speed":{"kind":"fixed","kilometersPerHour":7},"incline":{"kind":"fixed","percent":1},"cue":null,"notes":null}]}
      """;
    using var oversizedContent = new MultipartFormDataContent();
    oversizedContent.Add(new StringContent("NativeJson"), "format");
    oversizedContent.Add(
      new ByteArrayContent(new byte[WorkoutImportLimits.MaximumBytes + (64 * 1024)]),
      "file",
      "oversized.json");
    using HttpResponseMessage oversized = await client.PostAsync(
      "/api/planning/workouts/import/preview",
      oversizedContent);

    using var extraFileContent = new MultipartFormDataContent();
    extraFileContent.Add(new StringContent("NativeJson"), "format");
    extraFileContent.Add(
      new ByteArrayContent(Encoding.UTF8.GetBytes(validSource)),
      "file",
      "workout.json");
    extraFileContent.Add(new ByteArrayContent("{}"u8.ToArray()), "unexpected", "extra.json");
    using HttpResponseMessage extraFile = await client.PostAsync(
      "/api/planning/workouts/import/preview",
      extraFileContent);

    Assert.Equal(HttpStatusCode.RequestEntityTooLarge, oversized.StatusCode);
    Assert.Equal(HttpStatusCode.BadRequest, extraFile.StatusCode);
  }

  [Fact]
  public async Task Import_confirm_is_stable_and_rejects_operation_reuse_for_another_preview()
  {
    using HttpClient client = factory.CreateClient();
    const string source = """
      {"schemaVersion":1,"title":"Imported plan","description":null,"blocks":[{"kind":"step","goal":{"kind":"time","durationTicks":600000000},"speed":{"kind":"fixed","kilometersPerHour":7},"incline":{"kind":"fixed","percent":1},"cue":null,"notes":null}]}
      """;
    JsonElement preview = await PreviewAsync(client, source, "one.json");
    Guid operationId = Guid.NewGuid();
    var confirm = new
    {
      operationId,
      previewId = preview.GetProperty("previewId").GetGuid(),
      sourceSha256 = preview.GetProperty("sourceSha256").GetString(),
      profileId = (Guid?)null,
      qDomyosUnits = (string?)null,
    };

    using HttpResponseMessage first = await client.PostAsJsonAsync("/api/planning/workouts/import/confirm", confirm);
    using HttpResponseMessage replay = await client.PostAsJsonAsync("/api/planning/workouts/import/confirm", confirm);
    Assert.True(first.StatusCode == HttpStatusCode.Created, await first.Content.ReadAsStringAsync());
    Assert.Equal((await ReadJsonAsync(first)).GetProperty("revisionId").GetGuid(), (await ReadJsonAsync(replay)).GetProperty("revisionId").GetGuid());

    JsonElement other = await PreviewAsync(client, source.Replace("Imported plan", "Other plan"), "two.json");
    using HttpResponseMessage conflict = await client.PostAsJsonAsync("/api/planning/workouts/import/confirm", new
    {
      operationId,
      previewId = other.GetProperty("previewId").GetGuid(),
      sourceSha256 = other.GetProperty("sourceSha256").GetString(),
      profileId = (Guid?)null,
      qDomyosUnits = (string?)null,
    });
    Assert.Equal(HttpStatusCode.Conflict, conflict.StatusCode);
    using HttpResponseMessage changedScope = await client.PostAsJsonAsync("/api/planning/workouts/import/confirm", new
    {
      operationId,
      previewId = preview.GetProperty("previewId").GetGuid(),
      sourceSha256 = preview.GetProperty("sourceSha256").GetString(),
      profileId = (Guid?)Guid.NewGuid(),
      qDomyosUnits = "mph",
    });
    Assert.Equal(HttpStatusCode.Conflict, changedScope.StatusCode);
  }

  [Fact]
  public async Task Calendar_resolves_options_exceptions_and_persists_day_selection()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly date = DateOnly.FromDateTime(DateTime.Today.AddDays(1));
    int weekdayMask = 1 << (((int)date.DayOfWeek + 6) % 7);
    Guid seriesOperation = Guid.NewGuid();
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = seriesOperation,
      profileId,
      name = "Acceptance series",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    Guid seriesId = (await ReadJsonAsync(created)).GetProperty("id").GetGuid();
    using HttpResponseMessage changedSeriesCreate = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = seriesOperation,
      profileId,
      name = "Different series request",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Conflict, changedSeriesCreate.StatusCode);

    Guid seriesUpdateOperation = Guid.NewGuid();
    var seriesUpdate = new
    {
      operationId = seriesUpdateOperation,
      profileId,
      name = "Updated acceptance series",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)1,
    };
    using HttpResponseMessage updatedSeries = await client.PutAsJsonAsync(
      $"/api/planning/calendar/series/{seriesId}",
      seriesUpdate);
    using HttpResponseMessage wrongSeriesTarget = await client.PutAsJsonAsync(
      $"/api/planning/calendar/series/{Guid.NewGuid()}",
      seriesUpdate);
    Assert.Equal(HttpStatusCode.OK, updatedSeries.StatusCode);
    Assert.Equal(HttpStatusCode.Conflict, wrongSeriesTarget.StatusCode);

    JsonElement range = (await client.GetFromJsonAsync<JsonElement>($"/api/planning/calendar/{profileId}?from={date:yyyy-MM-dd}&to={date:yyyy-MM-dd}"));
    Assert.Equal(revisionId, range.GetProperty("days")[0].GetProperty("options")[0].GetProperty("workoutRevisionId").GetGuid());
    Guid selectionOperation = Guid.NewGuid();
    var selection = new { operationId = selectionOperation, seriesId, workoutRevisionId = revisionId };
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync($"/api/planning/calendar/{profileId}/days/{date:yyyy-MM-dd}/selection", selection)).StatusCode);
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync($"/api/planning/calendar/{profileId}/days/{date:yyyy-MM-dd}/selection", selection)).StatusCode);
    Assert.Equal(
      HttpStatusCode.Conflict,
      (await client.PostAsJsonAsync(
        $"/api/planning/calendar/{profileId}/days/{date.AddDays(1):yyyy-MM-dd}/selection",
        selection)).StatusCode);
    Assert.Equal(
      HttpStatusCode.Conflict,
      (await client.PostAsJsonAsync(
        $"/api/planning/calendar/{Guid.NewGuid()}/days/{date:yyyy-MM-dd}/selection",
        selection)).StatusCode);
    Assert.Equal(
      HttpStatusCode.Conflict,
      (await client.PostAsJsonAsync(
        $"/api/planning/calendar/{profileId}/days/{date:yyyy-MM-dd}/selection",
        new { operationId = selectionOperation, seriesId, workoutRevisionId = Guid.NewGuid() })).StatusCode);
    range = await client.GetFromJsonAsync<JsonElement>($"/api/planning/calendar/{profileId}?from={date:yyyy-MM-dd}&to={date:yyyy-MM-dd}");
    Assert.True(range.GetProperty("days")[0].GetProperty("options")[0].GetProperty("isSelected").GetBoolean());
  }

  [Fact]
  public async Task Calendar_range_handles_the_maximum_supported_date_without_overflowing()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();

    using HttpResponseMessage response = await client.GetAsync(
      $"/api/planning/calendar/{profileId}?from=9999-12-31&to=9999-12-31");

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    JsonElement range = await ReadJsonAsync(response);
    Assert.Equal("9999-12-31", range.GetProperty("from").GetString());
    Assert.Equal("9999-12-31", range.GetProperty("to").GetString());
    Assert.Empty(range.GetProperty("days").EnumerateArray());
  }

  [Fact]
  public async Task Calendar_move_rejects_a_date_occupied_by_another_schedule_group()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly monday = NextWeekday(DateOnly.FromDateTime(DateTime.Today.AddDays(1)), DayOfWeek.Monday);
    DateOnly tuesday = monday.AddDays(1);

    async Task<JsonElement> CreateSeriesAsync(string name, DateOnly date, int weekdayMask)
    {
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
      {
        operationId = Guid.NewGuid(),
        profileId,
        name,
        timeZoneId = "Europe/Brussels",
        startDate = date,
        endDate = date,
        intervalWeeks = 1,
        weekdayMask,
        alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
        exceptions = Array.Empty<object>(),
        expectedVersion = (int?)null,
      });
      Assert.Equal(HttpStatusCode.Created, response.StatusCode);
      return await ReadJsonAsync(response);
    }

    JsonElement mondaySeries = await CreateSeriesAsync("Monday schedule", monday, 1);
    await CreateSeriesAsync("Tuesday schedule", tuesday, 2);
    Guid mondaySeriesId = mondaySeries.GetProperty("id").GetGuid();
    using HttpResponseMessage moved = await client.PostAsJsonAsync(
      $"/api/planning/calendar/series/{mondaySeriesId}/occurrences/{monday:yyyy-MM-dd}/move",
      new
      {
        operationId = Guid.NewGuid(),
        targetDate = tuesday,
        moveFollowing = false,
        expectedVersion = 1,
        expectedSegments = (object?)null,
      });

    Assert.Equal(HttpStatusCode.BadRequest, moved.StatusCode);
  }

  [Fact]
  public async Task Calendar_following_move_rejects_collisions_on_later_shifted_dates()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly firstMonday = new(2026, 8, 10);
    DateOnly shiftedTuesday = firstMonday.AddDays(1);
    DateOnly laterCollision = new(2026, 8, 18);

    async Task<JsonElement> CreateSeriesAsync(string name, DateOnly startDate, DateOnly endDate, int weekdayMask)
    {
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
      {
        operationId = Guid.NewGuid(),
        profileId,
        name,
        timeZoneId = "Europe/Brussels",
        startDate,
        endDate,
        intervalWeeks = 1,
        weekdayMask,
        alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
        exceptions = Array.Empty<object>(),
        expectedVersion = (int?)null,
      });
      Assert.Equal(HttpStatusCode.Created, response.StatusCode);
      return await ReadJsonAsync(response);
    }

    JsonElement selected = await CreateSeriesAsync("Monday sequence", firstMonday, new DateOnly(2026, 8, 24), 1);
    await CreateSeriesAsync("Later Tuesday collision", laterCollision, laterCollision, 2);
    Guid selectedId = selected.GetProperty("id").GetGuid();

    using HttpResponseMessage moved = await client.PostAsJsonAsync(
      $"/api/planning/calendar/series/{selectedId}/occurrences/{firstMonday:yyyy-MM-dd}/move",
      new
      {
        operationId = Guid.NewGuid(),
        targetDate = shiftedTuesday,
        moveFollowing = true,
        expectedVersion = 1,
        expectedSegments = new[] { new { seriesId = selectedId, version = 1 } },
      });

    Assert.Equal(HttpStatusCode.BadRequest, moved.StatusCode);
    Assert.Contains("another workout group", await moved.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Calendar_moves_single_and_following_sessions_then_deletes_one_or_the_complete_group()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly firstMonday = NextWeekday(DateOnly.FromDateTime(DateTime.Today.AddDays(1)), DayOfWeek.Monday);
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = "Movable 5K group",
      timeZoneId = "Europe/Brussels",
      startDate = firstMonday,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask = (int)WeekdayFlags.Monday,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    JsonElement createdSeries = await ReadJsonAsync(created);
    Guid originalSeriesId = createdSeries.GetProperty("id").GetGuid();
    Guid scheduleGroupId = createdSeries.GetProperty("scheduleGroupId").GetGuid();
    Assert.Equal(originalSeriesId, scheduleGroupId);

    DateOnly singleTarget = firstMonday.AddDays(1);
    Guid singleMoveOperation = Guid.NewGuid();
    var singleMove = new
    {
      operationId = singleMoveOperation,
      targetDate = singleTarget,
      moveFollowing = false,
      expectedVersion = 1,
    };
    string singleMoveUrl = $"/api/planning/calendar/series/{originalSeriesId}/occurrences/{firstMonday:yyyy-MM-dd}/move";
    using HttpResponseMessage singleMoveResponse = await client.PostAsJsonAsync(singleMoveUrl, singleMove);
    Assert.True(singleMoveResponse.StatusCode == HttpStatusCode.NoContent, await singleMoveResponse.Content.ReadAsStringAsync());
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(singleMoveUrl, singleMove)).StatusCode);
    Assert.Equal(
      HttpStatusCode.Conflict,
      (await client.PostAsJsonAsync(singleMoveUrl, new
      {
        operationId = singleMoveOperation,
        targetDate = firstMonday.AddDays(2),
        moveFollowing = false,
        expectedVersion = 1,
      })).StatusCode);
    Assert.Empty((await CalendarRangeAsync(client, profileId, firstMonday, firstMonday)).GetProperty("days").EnumerateArray());
    JsonElement movedSingleRange = await CalendarRangeAsync(client, profileId, singleTarget, singleTarget);
    Assert.Equal(scheduleGroupId, movedSingleRange.GetProperty("days")[0].GetProperty("options")[0].GetProperty("scheduleGroupId").GetGuid());
    Assert.Equal("Movable 5K group", movedSingleRange.GetProperty("days")[0].GetProperty("options")[0].GetProperty("scheduleName").GetString());

    DateOnly followingSource = firstMonday.AddDays(7);
    DateOnly followingTarget = followingSource.AddDays(2);
    Guid followingMoveOperation = Guid.NewGuid();
    string followingMoveUrl = $"/api/planning/calendar/series/{originalSeriesId}/occurrences/{followingSource:yyyy-MM-dd}/move";
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(followingMoveUrl, new
    {
      operationId = followingMoveOperation,
      targetDate = followingTarget,
      moveFollowing = true,
      expectedVersion = 2,
      expectedSegments = new[] { new { seriesId = originalSeriesId, version = 2 } },
    })).StatusCode);

    Assert.Empty((await CalendarRangeAsync(client, profileId, followingSource, followingSource)).GetProperty("days").EnumerateArray());
    JsonElement followingTargetRange = await CalendarRangeAsync(client, profileId, followingTarget, followingTarget);
    JsonElement continuationOption = followingTargetRange.GetProperty("days")[0].GetProperty("options")[0];
    Guid continuationSeriesId = continuationOption.GetProperty("seriesId").GetGuid();
    Assert.NotEqual(originalSeriesId, continuationSeriesId);
    Assert.Equal(scheduleGroupId, continuationOption.GetProperty("scheduleGroupId").GetGuid());
    JsonElement nextShifted = await CalendarRangeAsync(client, profileId, firstMonday.AddDays(16), firstMonday.AddDays(16));
    Assert.Single(nextShifted.GetProperty("days").EnumerateArray());

    JsonElement[] segments = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={profileId}"))!;
    Assert.Equal(2, segments.Count(item => item.GetProperty("scheduleGroupId").GetGuid() == scheduleGroupId));
    JsonElement continuation = segments.Single(item => item.GetProperty("id").GetGuid() == continuationSeriesId);
    Assert.Equal(1, continuation.GetProperty("version").GetInt32());

    DateOnly nextShiftedDate = firstMonday.AddDays(16);
    using HttpResponseMessage staleFollowingMove = await client.PostAsJsonAsync(
      $"/api/planning/calendar/series/{continuationSeriesId}/occurrences/{nextShiftedDate:yyyy-MM-dd}/move",
      new
      {
        operationId = Guid.NewGuid(),
        targetDate = nextShiftedDate.AddDays(1),
        moveFollowing = true,
        expectedVersion = 1,
        expectedSegments = segments
          .Where(item => item.GetProperty("scheduleGroupId").GetGuid() == scheduleGroupId)
          .Select(item => new
          {
            seriesId = item.GetProperty("id").GetGuid(),
            version = item.GetProperty("version").GetInt32() + (item.GetProperty("id").GetGuid() == originalSeriesId ? 1 : 0),
          }).ToArray(),
      });
    Assert.Equal(HttpStatusCode.Conflict, staleFollowingMove.StatusCode);
    Assert.Single((await CalendarRangeAsync(client, profileId, nextShiftedDate, nextShiftedDate)).GetProperty("days").EnumerateArray());

    Guid deleteOccurrenceOperation = Guid.NewGuid();
    string deleteOccurrenceUrl = $"/api/planning/calendar/series/{continuationSeriesId}/occurrences/{followingTarget:yyyy-MM-dd}/delete";
    var deleteOccurrence = new { operationId = deleteOccurrenceOperation, expectedVersion = 1 };
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(deleteOccurrenceUrl, deleteOccurrence)).StatusCode);
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(deleteOccurrenceUrl, deleteOccurrence)).StatusCode);
    Assert.Empty((await CalendarRangeAsync(client, profileId, followingTarget, followingTarget)).GetProperty("days").EnumerateArray());

    Guid deleteGroupOperation = Guid.NewGuid();
    string deleteGroupUrl = $"/api/planning/calendar/series/{continuationSeriesId}/delete-group";
    JsonElement[] groupSegments = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={profileId}"))!
      .Where(item => item.GetProperty("scheduleGroupId").GetGuid() == scheduleGroupId)
      .ToArray();
    var staleGroupDelete = new
    {
      operationId = Guid.NewGuid(),
      expectedSegments = groupSegments.Select((item, index) => new
      {
        seriesId = item.GetProperty("id").GetGuid(),
        version = item.GetProperty("version").GetInt32() + (index == 0 ? 1 : 0),
      }).ToArray(),
    };
    Assert.Equal(HttpStatusCode.Conflict, (await client.PostAsJsonAsync(deleteGroupUrl, staleGroupDelete)).StatusCode);
    Assert.Equal(2, (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={profileId}"))!
      .Count(item => item.GetProperty("scheduleGroupId").GetGuid() == scheduleGroupId));
    var deleteGroup = new
    {
      operationId = deleteGroupOperation,
      expectedSegments = groupSegments.Select(item => new
      {
        seriesId = item.GetProperty("id").GetGuid(),
        version = item.GetProperty("version").GetInt32(),
      }).ToArray(),
    };
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(deleteGroupUrl, deleteGroup)).StatusCode);
    Assert.Equal(HttpStatusCode.NoContent, (await client.PostAsJsonAsync(deleteGroupUrl, deleteGroup)).StatusCode);
    Assert.DoesNotContain(
      (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={profileId}"))!,
      item => item.GetProperty("scheduleGroupId").GetGuid() == scheduleGroupId);
  }

  [Fact]
  public async Task Calendar_rejects_backward_following_move_that_would_overlap_preserved_sessions()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly firstMonday = NextWeekday(DateOnly.FromDateTime(DateTime.Today.AddDays(1)), DayOfWeek.Monday);
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = "Three day overlap guard",
      timeZoneId = "Europe/Brussels",
      startDate = firstMonday,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask = (int)(WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Friday),
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    JsonElement series = await ReadJsonAsync(created);
    Guid seriesId = series.GetProperty("id").GetGuid();
    DateOnly sourceFriday = firstMonday.AddDays(11);
    DateOnly targetSunday = sourceFriday.AddDays(-5);

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      $"/api/planning/calendar/series/{seriesId}/occurrences/{sourceFriday:yyyy-MM-dd}/move",
      new
      {
        operationId = Guid.NewGuid(),
        targetDate = targetSunday,
        moveFollowing = true,
        expectedVersion = 1,
        expectedSegments = new[] { new { seriesId, version = 1 } },
      });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    Assert.Contains("overlap", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
    JsonElement preservedWednesday = await CalendarRangeAsync(
      client, profileId, sourceFriday.AddDays(-2), sourceFriday.AddDays(-2));
    Assert.Single(preservedWednesday.GetProperty("days").EnumerateArray());
    Assert.Single((await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={profileId}"))!);
  }

  [Fact]
  public async Task Calendar_rejects_following_move_for_an_individually_added_exception()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly firstMonday = NextWeekday(DateOnly.FromDateTime(DateTime.Today.AddDays(1)), DayOfWeek.Monday);
    DateOnly addedTuesday = firstMonday.AddDays(1);
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = "Exception move guard",
      timeZoneId = "Europe/Brussels",
      startDate = firstMonday,
      endDate = firstMonday.AddDays(28),
      intervalWeeks = 1,
      weekdayMask = (int)WeekdayFlags.Monday,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = new[]
      {
        new
        {
          date = addedTuesday,
          kind = "add",
          alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
        },
      },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    JsonElement series = await ReadJsonAsync(created);
    Guid seriesId = series.GetProperty("id").GetGuid();

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      $"/api/planning/calendar/series/{seriesId}/occurrences/{addedTuesday:yyyy-MM-dd}/move",
      new
      {
        operationId = Guid.NewGuid(),
        targetDate = addedTuesday.AddDays(2),
        moveFollowing = true,
        expectedVersion = 1,
        expectedSegments = new[] { new { seriesId, version = 1 } },
      });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    Assert.Contains("only be moved by itself", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
    Assert.Single((await CalendarRangeAsync(client, profileId, addedTuesday, addedTuesday)).GetProperty("days").EnumerateArray());
    Assert.Single((await CalendarRangeAsync(client, profileId, firstMonday.AddDays(7), firstMonday.AddDays(7))).GetProperty("days").EnumerateArray());
  }

  [Fact]
  public async Task Calendar_update_rejects_profile_transfer_and_preserves_owner()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement owner = await CreateProfileAsync(client);
    JsonElement other = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid ownerId = owner.GetProperty("id").GetGuid();
    Guid otherId = other.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly date = DateOnly.FromDateTime(DateTime.Today.AddDays(1));
    int weekdayMask = 1 << (((int)date.DayOfWeek + 6) % 7);
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId = ownerId,
      name = "Owned schedule",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    JsonElement series = await ReadJsonAsync(created);
    Guid seriesId = series.GetProperty("id").GetGuid();

    using HttpResponseMessage transfer = await client.PutAsJsonAsync($"/api/planning/calendar/series/{seriesId}", new
    {
      operationId = Guid.NewGuid(),
      profileId = otherId,
      name = "Transferred schedule",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = 1,
    });

    Assert.Equal(HttpStatusCode.BadRequest, transfer.StatusCode);
    JsonElement[] ownerSeries = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={ownerId}"))!;
    Assert.Equal(ownerId, Assert.Single(ownerSeries).GetProperty("profileId").GetGuid());
    Assert.Empty((await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/calendar/series?profileId={otherId}"))!);
  }

  [Fact]
  public async Task Concurrent_operation_id_reuse_allows_one_profile_request_and_conflicts_the_other()
  {
    using HttpClient client = factory.CreateClient();
    Guid operationId = Guid.NewGuid();
    var firstRequest = ProfileRequest(operationId, $"Concurrent A {operationId:N}");
    var secondRequest = ProfileRequest(operationId, $"Concurrent B {operationId:N}");

    Task<HttpResponseMessage> firstTask = client.PostAsJsonAsync("/api/planning/profiles", firstRequest);
    Task<HttpResponseMessage> secondTask = client.PostAsJsonAsync("/api/planning/profiles", secondRequest);
    HttpResponseMessage[] responses = await Task.WhenAll(firstTask, secondTask);
    try
    {
      Assert.Contains(responses, response => response.StatusCode == HttpStatusCode.Created);
      Assert.Contains(responses, response => response.StatusCode == HttpStatusCode.Conflict);
      object winningRequest = responses[0].StatusCode == HttpStatusCode.Created ? firstRequest : secondRequest;
      using HttpResponseMessage replay = await client.PostAsJsonAsync("/api/planning/profiles", winningRequest);
      Assert.Equal(HttpStatusCode.Created, replay.StatusCode);
    }
    finally
    {
      foreach (HttpResponseMessage response in responses)
      {
        response.Dispose();
      }
    }

    Guid replayOperationId = Guid.NewGuid();
    object identicalRequest = ProfileRequest(
      replayOperationId,
      $"Concurrent identical {replayOperationId:N}");
    HttpResponseMessage[] identicalResponses = await Task.WhenAll(
      client.PostAsJsonAsync("/api/planning/profiles", identicalRequest),
      client.PostAsJsonAsync("/api/planning/profiles", identicalRequest));
    try
    {
      Assert.All(identicalResponses, response => Assert.Equal(HttpStatusCode.Created, response.StatusCode));
      JsonElement first = await ReadJsonAsync(identicalResponses[0]);
      JsonElement replay = await ReadJsonAsync(identicalResponses[1]);
      Assert.Equal(first.GetProperty("id").GetGuid(), replay.GetProperty("id").GetGuid());
    }
    finally
    {
      foreach (HttpResponseMessage response in identicalResponses)
      {
        response.Dispose();
      }
    }
  }

  [Fact]
  public async Task Calendar_with_null_exception_returns_bad_request()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    DateOnly date = DateOnly.FromDateTime(DateTime.Today.AddDays(1));
    int weekdayMask = 1 << (((int)date.DayOfWeek + 6) % 7);
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId = profile.GetProperty("id").GetGuid(),
      name = "Invalid exception",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = workout.GetProperty("revisionId").GetGuid(), displayOrder = 0 } },
      exceptions = new object?[] { null },
      expectedVersion = (int?)null,
    });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
  }

  [Fact]
  public async Task Calendar_rejects_missing_profile_and_workout_references_before_persistence()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement workout = await CreateSimpleWorkoutAsync(client);
    Guid profileId = profile.GetProperty("id").GetGuid();
    Guid revisionId = workout.GetProperty("revisionId").GetGuid();
    DateOnly date = DateOnly.FromDateTime(DateTime.Today.AddDays(1));
    int weekdayMask = 1 << (((int)date.DayOfWeek + 6) % 7);
    using HttpResponseMessage missingProfile = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId = Guid.NewGuid(),
      name = "Missing profile",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = revisionId, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    using HttpResponseMessage missingRevision = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = "Missing revision",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = Guid.NewGuid(), displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    using HttpResponseMessage invalidRevision = await client.PostAsJsonAsync("/api/planning/calendar/series", new
    {
      operationId = Guid.NewGuid(),
      profileId,
      name = "Invalid revision",
      timeZoneId = "Europe/Brussels",
      startDate = date,
      endDate = (DateOnly?)null,
      intervalWeeks = 1,
      weekdayMask,
      alternatives = new[] { new { workoutRevisionId = Guid.Empty, displayOrder = 0 } },
      exceptions = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });

    Assert.Equal(HttpStatusCode.NotFound, missingProfile.StatusCode);
    Assert.Equal(HttpStatusCode.NotFound, missingRevision.StatusCode);
    Assert.Equal(HttpStatusCode.BadRequest, invalidRevision.StatusCode);
    Assert.Empty((await client.GetFromJsonAsync<JsonElement[]>(
      $"/api/planning/calendar/series?profileId={profileId}"))!);
  }

  private static async Task<JsonElement> PreviewAsync(HttpClient client, string source, string fileName)
  {
    using var content = new MultipartFormDataContent();
    content.Add(new StringContent("NativeJson"), "format");
    content.Add(new ByteArrayContent(Encoding.UTF8.GetBytes(source)), "file", fileName);
    using HttpResponseMessage response = await client.PostAsync("/api/planning/workouts/import/preview", content);
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    return await ReadJsonAsync(response);
  }

  private static DateOnly NextWeekday(DateOnly date, DayOfWeek weekday)
  {
    while (date.DayOfWeek != weekday) date = date.AddDays(1);
    return date;
  }

  private static async Task<JsonElement> CalendarRangeAsync(
    HttpClient client,
    Guid profileId,
    DateOnly from,
    DateOnly to) =>
    await client.GetFromJsonAsync<JsonElement>(
      $"/api/planning/calendar/{profileId}?from={from:yyyy-MM-dd}&to={to:yyyy-MM-dd}");

  private static async Task<JsonElement> CreateProfileAsync(HttpClient client)
  {
    Guid operationId = Guid.NewGuid();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId,
      displayName = $"Calendar {operationId:N}",
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = (int?)null,
      maximumSpeedKph = (double?)null,
      heartRateZones = Array.Empty<object>(),
      expectedVersion = (int?)null,
    });
    return await ReadJsonAsync(response);
  }

  private static object ProfileRequest(Guid operationId, string displayName) => new
  {
    operationId,
    displayName,
    unitSystem = "Metric",
    weightKilograms = 70,
    maximumHeartRateBpm = (int?)null,
    maximumSpeedKph = (double?)null,
    heartRateZones = Array.Empty<object>(),
    expectedVersion = (int?)null,
  };

  private static async Task<JsonElement> CreateSimpleWorkoutAsync(HttpClient client)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = $"Calendar workout {Guid.NewGuid():N}",
      description = (string?)null,
      blocks = new[] { new
      {
        kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 10.0,
        speedKind = "fixed", speedStartKph = 7.0, speedEndKph = 0.0, heartRateMinimumBpm = 0,
        heartRateMaximumBpm = 0, heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
        heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
        inclineStartPercent = 1.0, inclineEndPercent = 0.0, cue = (string?)null, notes = (string?)null,
      } },
    });
    return await ReadJsonAsync(response);
  }

  private static async Task<JsonElement> ReadJsonAsync(HttpResponseMessage response)
  {
    using JsonDocument document = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
    return document.RootElement.Clone();
  }
}

public sealed class PlanningGatewayFactory : WebApplicationFactory<TreadmillRunner.Gateway.Program>
{
  private readonly string databasePath = Path.Combine(Path.GetTempPath(), $"treadmillrunner-planning-{Guid.NewGuid():N}.db");

  protected override void ConfigureWebHost(IWebHostBuilder builder)
  {
    using TreadmillRunnerDbContext database = TreadmillRunnerDatabase.CreateFactory(databasePath).CreateDbContext();
    database.Database.Migrate();
    builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(new Dictionary<string, string?>
    {
      ["Persistence:DatabasePath"] = databasePath,
    }));
  }
}
