using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminEndpointTests(GarminGatewayFactory factory) : IClassFixture<GarminGatewayFactory>
{
  [Fact]
  public async Task Mock_OAuth_connects_one_profile_encrypts_tokens_and_syncs_automatically()
  {
    using HttpClient client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
    JsonElement initial = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{factory.ProfileId}/status");
    Assert.True(initial.GetProperty("providerConfigured").GetBoolean());
    Assert.False(initial.GetProperty("connected").GetBoolean());

    using HttpResponseMessage startResponse = await client.PostAsync($"/api/integrations/garmin/profiles/{factory.ProfileId}/connect", content: null);
    startResponse.EnsureSuccessStatusCode();
    JsonElement start = await startResponse.Content.ReadFromJsonAsync<JsonElement>();
    string authorizationUrl = start.GetProperty("authorizationUrl").GetString()!;
    string rawState = QueryHelpers.ParseQuery(new Uri(authorizationUrl).Query)["state"].ToString();
    await using (var context = await factory.CreateContextAsync())
    {
      GarminOAuthStateEntity saved = await context.GarminOAuthStates.AsNoTracking().SingleAsync();
      Assert.NotEqual(rawState, saved.StateHash);
      Assert.DoesNotContain("mock-", saved.ProtectedCodeVerifier, StringComparison.OrdinalIgnoreCase);
    }

    using HttpResponseMessage callback = await client.GetAsync(authorizationUrl);
    Assert.Equal(HttpStatusCode.Redirect, callback.StatusCode);
    Assert.StartsWith("/profiles?garmin=connected", callback.Headers.Location?.OriginalString, StringComparison.Ordinal);

    JsonElement status = await WaitForSyncedAsync(client, 1);
    Assert.True(status.GetProperty("connected").GetBoolean());
    Assert.Contains("Garmin test account", status.GetProperty("accountLabel").GetString(), StringComparison.Ordinal);
    Assert.True(status.GetProperty("syncedItems").GetInt32() >= 1);

    using HttpResponseMessage workout = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = "Automatic Garmin follow-up",
      description = "Created after account connection.",
      blocks = new[]
      {
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 5.0,
          speedKind = "fixed", speedStartKph = 5.0, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0, heartRateZoneNumber = 0,
          heartRateInitialSpeedKph = 0.0, heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0,
          inclineKind = "fixed", inclineStartPercent = 0.5, inclineEndPercent = 0.0,
          cue = "Easy", notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, workout.StatusCode);
    JsonElement afterChange = await WaitForSyncedAsync(client, 2);
    Assert.True(afterChange.GetProperty("syncedItems").GetInt32() >= 2);

    await using (var context = await factory.CreateContextAsync())
    {
      GarminAccountLinkEntity link = await context.GarminAccountLinks.AsNoTracking()
        .SingleAsync(candidate => candidate.UserProfileId == factory.ProfileId);
      Assert.DoesNotContain("mock-access", link.ProtectedAccessToken, StringComparison.Ordinal);
      Assert.DoesNotContain("mock-refresh", link.ProtectedRefreshToken, StringComparison.Ordinal);
    }

    using HttpResponseMessage replay = await client.GetAsync(authorizationUrl);
    Assert.Equal(HttpStatusCode.Redirect, replay.StatusCode);
    Assert.Contains("garmin=error", replay.Headers.Location?.OriginalString, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Disconnect_removes_only_the_selected_profiles_credentials_and_queue()
  {
    using HttpClient client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
    JsonElement status = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{factory.ProfileId}/status");
    if (!status.GetProperty("connected").GetBoolean())
    {
      JsonElement start = await (await client.PostAsync($"/api/integrations/garmin/profiles/{factory.ProfileId}/connect", null)).Content.ReadFromJsonAsync<JsonElement>();
      await client.GetAsync(start.GetProperty("authorizationUrl").GetString());
    }

    using HttpResponseMessage disconnect = await client.PostAsync($"/api/integrations/garmin/profiles/{factory.ProfileId}/disconnect", null);
    Assert.Equal(HttpStatusCode.NoContent, disconnect.StatusCode);
    JsonElement after = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{factory.ProfileId}/status");
    Assert.False(after.GetProperty("connected").GetBoolean());
    Assert.Equal(0, after.GetProperty("pendingItems").GetInt32());
    Assert.Equal(0, after.GetProperty("syncedItems").GetInt32());
  }

  [Fact]
  public async Task Two_profiles_connect_distinct_accounts_and_disconnect_independently()
  {
    using HttpClient client = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
    await EnsureConnectedAsync(client, factory.ProfileId);
    await EnsureConnectedAsync(client, factory.SecondProfileId);
    JsonElement first = await WaitForSyncedAsync(client, factory.ProfileId, 1);
    JsonElement second = await WaitForSyncedAsync(client, factory.SecondProfileId, 1);
    Assert.NotEqual(first.GetProperty("accountLabel").GetString(), second.GetProperty("accountLabel").GetString());

    using HttpResponseMessage disconnect = await client.PostAsync($"/api/integrations/garmin/profiles/{factory.ProfileId}/disconnect", null);
    Assert.Equal(HttpStatusCode.NoContent, disconnect.StatusCode);
    JsonElement firstAfter = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{factory.ProfileId}/status");
    JsonElement secondAfter = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{factory.SecondProfileId}/status");
    Assert.False(firstAfter.GetProperty("connected").GetBoolean());
    Assert.True(secondAfter.GetProperty("connected").GetBoolean());
    Assert.True(secondAfter.GetProperty("syncedItems").GetInt32() >= 1);
  }

  private static async Task EnsureConnectedAsync(HttpClient client, Guid profileId)
  {
    JsonElement status = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{profileId}/status");
    if (status.GetProperty("connected").GetBoolean()) return;
    using HttpResponseMessage startResponse = await client.PostAsync($"/api/integrations/garmin/profiles/{profileId}/connect", null);
    startResponse.EnsureSuccessStatusCode();
    JsonElement start = await startResponse.Content.ReadFromJsonAsync<JsonElement>();
    using HttpResponseMessage callback = await client.GetAsync(start.GetProperty("authorizationUrl").GetString());
    Assert.Equal(HttpStatusCode.Redirect, callback.StatusCode);
  }

  private async Task<JsonElement> WaitForSyncedAsync(HttpClient client, int minimum)
    => await WaitForSyncedAsync(client, factory.ProfileId, minimum);

  private static async Task<JsonElement> WaitForSyncedAsync(HttpClient client, Guid profileId, int minimum)
  {
    for (var attempt = 0; attempt < 50; attempt++)
    {
      JsonElement status = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/profiles/{profileId}/status");
      if (status.GetProperty("syncedItems").GetInt32() >= minimum) return status;
      await Task.Delay(50);
    }
    throw new TimeoutException("Mock Garmin synchronization did not complete.");
  }
}

public sealed class GarminGatewayFactory : WebApplicationFactory<TreadmillRunner.Gateway.Program>
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", $"garmin-gateway-{Guid.NewGuid():N}");
  private string DatabasePath => Path.Combine(directory, "gateway.db");
  public Guid ProfileId { get; } = Guid.NewGuid();
  public Guid SecondProfileId { get; } = Guid.NewGuid();

  protected override void ConfigureWebHost(IWebHostBuilder builder)
  {
    Directory.CreateDirectory(directory);
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    using TreadmillRunnerDbContext database = factory.CreateDbContext();
    database.Database.Migrate();
    if (!database.UserProfiles.Any())
    {
      DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T17:00:00Z");
      database.UserProfiles.Add(new UserProfileEntity
      {
        Id = ProfileId,
        DisplayName = "Marc",
        NormalizedDisplayName = "MARC",
        UnitSystem = "Metric",
        WeightKilograms = 70,
        Version = 1,
        CreatedAtUtc = now,
        UpdatedAtUtc = now,
      });
      database.UserProfiles.Add(new UserProfileEntity
      {
        Id = SecondProfileId,
        DisplayName = "Runner 2",
        NormalizedDisplayName = "RUNNER 2",
        UnitSystem = "Metric",
        WeightKilograms = 65,
        Version = 1,
        CreatedAtUtc = now,
        UpdatedAtUtc = now,
      });
      var workout = new WorkoutEntity
      {
        Id = Guid.NewGuid(),
        Name = "Easy 5K",
        Kind = "Structured",
        CreatedAtUtc = now,
      };
      workout.Revisions.Add(new WorkoutRevisionEntity
      {
        Id = Guid.NewGuid(),
        RevisionNumber = 1,
        DefinitionJson = "{\"schemaVersion\":1,\"title\":\"Easy 5K\",\"description\":\"Mock sync fixture\",\"blocks\":[]}",
        ContentSha256 = new string('c', 64),
        CreatedAtUtc = now,
      });
      database.Workouts.Add(workout);
      database.SaveChanges();
    }

    builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(new Dictionary<string, string?>
    {
      ["Persistence:DatabasePath"] = DatabasePath,
      ["Persistence:DataProtectionKeyPath"] = Path.Combine(directory, "keys"),
      ["GarminConnect:Provider"] = "Mock",
    }));
  }

  public async Task<TreadmillRunnerDbContext> CreateContextAsync()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    return await factory.CreateDbContextAsync();
  }

  protected override void Dispose(bool disposing)
  {
    base.Dispose(disposing);
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
  }
}
