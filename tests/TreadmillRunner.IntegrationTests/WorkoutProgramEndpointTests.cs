using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class WorkoutProgramEndpointTests(PlanningGatewayFactory factory)
  : IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Program_create_supports_personal_and_household_visibility()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement firstProfile = await CreateProfileAsync(client);
    JsonElement secondProfile = await CreateProfileAsync(client);
    JsonElement workout = await CreateWorkoutAsync(client, $"Scoped workout {Guid.NewGuid():N}", 6.0);

    async Task<JsonElement> CreateProgramAsync(string name, Guid? ownerProfileId)
    {
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/programs", new
      {
        operationId = Guid.NewGuid(),
        name,
        description = "Visibility contract",
        category = "Custom",
        ownerProfileId,
        items = new[] { new { workoutRevisionId = workout.GetProperty("revisionId").GetGuid() } },
      });
      Assert.Equal(HttpStatusCode.Created, response.StatusCode);
      return await ReadJsonAsync(response);
    }

    JsonElement personal = await CreateProgramAsync($"Personal {Guid.NewGuid():N}", firstProfile.GetProperty("id").GetGuid());
    JsonElement household = await CreateProgramAsync($"Household {Guid.NewGuid():N}", null);
    Assert.Equal(firstProfile.GetProperty("id").GetGuid(), personal.GetProperty("ownerProfileId").GetGuid());
    Assert.Equal(JsonValueKind.Null, household.GetProperty("ownerProfileId").ValueKind);

    JsonElement[] firstList = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={firstProfile.GetProperty("id").GetGuid()}"))!;
    JsonElement[] secondList = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={secondProfile.GetProperty("id").GetGuid()}"))!;
    Assert.Contains(firstList, item => item.GetProperty("id").GetGuid() == personal.GetProperty("id").GetGuid());
    Assert.DoesNotContain(secondList, item => item.GetProperty("id").GetGuid() == personal.GetProperty("id").GetGuid());
    Assert.Contains(secondList, item => item.GetProperty("id").GetGuid() == household.GetProperty("id").GetGuid());
  }

  [Fact]
  public async Task Program_create_start_list_and_operation_replay_preserve_the_same_run()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement profile = await CreateProfileAsync(client);
    JsonElement firstWorkout = await CreateWorkoutAsync(client, "5K easy foundation", 6.5);
    JsonElement secondWorkout = await CreateWorkoutAsync(client, "5K steady finish", 7.0);
    Guid createOperationId = Guid.NewGuid();
    var createRequest = new
    {
      operationId = createOperationId,
      name = "First 5K",
      description = "Two ordered sessions.",
      category = "5K",
      items = new[]
      {
        new { workoutRevisionId = firstWorkout.GetProperty("revisionId").GetGuid() },
        new { workoutRevisionId = secondWorkout.GetProperty("revisionId").GetGuid() },
      },
    };

    using HttpResponseMessage createdResponse = await client.PostAsJsonAsync("/api/planning/programs", createRequest);
    Assert.Equal(HttpStatusCode.Created, createdResponse.StatusCode);
    JsonElement created = await ReadJsonAsync(createdResponse);
    using HttpResponseMessage replayResponse = await client.PostAsJsonAsync("/api/planning/programs", createRequest);
    Assert.Equal(HttpStatusCode.Created, replayResponse.StatusCode);
    JsonElement replay = await ReadJsonAsync(replayResponse);
    Assert.Equal(created.GetProperty("id").GetGuid(), replay.GetProperty("id").GetGuid());
    Assert.Equal(created.GetProperty("revisionId").GetGuid(), replay.GetProperty("revisionId").GetGuid());
    Assert.Equal(2, created.GetProperty("items").GetArrayLength());

    Guid programId = created.GetProperty("id").GetGuid();
    Guid startOperationId = Guid.NewGuid();
    var startRequest = new
    {
      operationId = startOperationId,
      profileId = profile.GetProperty("id").GetGuid(),
      expectedProgramRevisionId = created.GetProperty("revisionId").GetGuid(),
      expectedActiveRunId = (Guid?)null,
      expectedActiveRunVersion = (int?)null,
    };
    using HttpResponseMessage startedResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start",
      startRequest);
    Assert.Equal(HttpStatusCode.OK, startedResponse.StatusCode);
    JsonElement started = await ReadJsonAsync(startedResponse);
    using HttpResponseMessage startReplayResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start",
      startRequest);
    Assert.Equal(HttpStatusCode.OK, startReplayResponse.StatusCode);
    JsonElement startReplay = await ReadJsonAsync(startReplayResponse);
    Assert.Equal(started.GetProperty("id").GetGuid(), startReplay.GetProperty("id").GetGuid());

    using HttpResponseMessage staleStartResponse = await client.PostAsJsonAsync(
      $"/api/planning/programs/{programId}/start",
      new
      {
        operationId = Guid.NewGuid(),
        profileId = profile.GetProperty("id").GetGuid(),
        expectedProgramRevisionId = created.GetProperty("revisionId").GetGuid(),
        expectedActiveRunId = (Guid?)null,
        expectedActiveRunVersion = (int?)null,
      });
    Assert.Equal(HttpStatusCode.Conflict, staleStartResponse.StatusCode);

    JsonElement[] listed = (await client.GetFromJsonAsync<JsonElement[]>(
      $"/api/planning/programs?profileId={profile.GetProperty("id").GetGuid()}"))!;
    JsonElement listedProgram = Assert.Single(listed, candidate => candidate.GetProperty("id").GetGuid() == programId);
    Assert.Equal(started.GetProperty("id").GetGuid(), listedProgram.GetProperty("run").GetProperty("id").GetGuid());
    Assert.Equal(0, listedProgram.GetProperty("completedItemCount").GetInt32());
    Assert.Equal(
      created.GetProperty("items")[0].GetProperty("id").GetGuid(),
      listedProgram.GetProperty("nextItemId").GetGuid());
    Assert.Equal(
      firstWorkout.GetProperty("revisionId").GetGuid(),
      listedProgram.GetProperty("nextWorkoutRevisionId").GetGuid());
  }

  private static async Task<JsonElement> CreateProfileAsync(HttpClient client)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = $"Program runner {Guid.NewGuid():N}",
      unitSystem = "Metric",
      weightKilograms = 70,
      maximumHeartRateBpm = 190,
      maximumSpeedKph = 12.0,
      heartRateZones = new[] { new { number = 1, name = "Warm up", minimumBpm = 95, maximumBpm = 114 } },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    return await ReadJsonAsync(response);
  }

  private static async Task<JsonElement> CreateWorkoutAsync(HttpClient client, string name, double speed)
  {
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name,
      description = "Program endpoint fixture",
      blocks = new[]
      {
        new
        {
          kind = "step",
          repetitions = 1,
          blocks = Array.Empty<object>(),
          goalKind = "time",
          goalValue = 10.0,
          speedKind = "fixed",
          speedStartKph = speed,
          speedEndKph = 0.0,
          heartRateMinimumBpm = 0,
          heartRateMaximumBpm = 0,
          heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0,
          heartRateMinimumSpeedKph = 0.0,
          heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed",
          inclineStartPercent = 1.0,
          inclineEndPercent = 0.0,
          cue = (string?)null,
          notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    return await ReadJsonAsync(response);
  }

  private static async Task<JsonElement> ReadJsonAsync(HttpResponseMessage response)
  {
    using JsonDocument document = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync());
    return document.RootElement.Clone();
  }
}
