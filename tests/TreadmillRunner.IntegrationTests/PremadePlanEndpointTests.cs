using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class PremadePlanEndpointTests(PlanningGatewayFactory factory)
  : IClassFixture<PlanningGatewayFactory>
{
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
      templateVersion = "1.0.0",
      freshCopy = false,
    };
    using HttpResponseMessage createdResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request);
    Assert.Equal(HttpStatusCode.Created, createdResponse.StatusCode);
    JsonElement created = await ReadJsonAsync(createdResponse);
    Assert.Equal(174, created.GetProperty("positionCount").GetInt32());
    Assert.True(created.GetProperty("uniqueWorkoutCount").GetInt32() < 174);
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
    Assert.Equal(174, installed.GetProperty("items").GetArrayLength());
    Assert.Equal("Foundation", installed.GetProperty("items")[0].GetProperty("phase").GetString());
    Assert.Equal(1, installed.GetProperty("items")[0].GetProperty("weekNumber").GetInt32());

    JsonElement[] otherPrograms = (await client.GetFromJsonAsync<JsonElement[]>($"/api/planning/programs?profileId={otherProfileId}"))!;
    Assert.DoesNotContain(otherPrograms, item => item.GetProperty("id").GetGuid() == programId);

    using HttpResponseMessage freshResponse = await client.PostAsJsonAsync("/api/planning/premade-plans/materialize", request with { operationId = Guid.NewGuid(), freshCopy = true });
    Assert.Equal(HttpStatusCode.Created, freshResponse.StatusCode);
    JsonElement fresh = await ReadJsonAsync(freshResponse);
    Assert.NotEqual(programId, fresh.GetProperty("programId").GetGuid());
    Assert.Equal(2, fresh.GetProperty("copyNumber").GetInt32());

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
