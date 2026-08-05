using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminWatchEndpointTests(GarminGatewayFactory factory) : IClassFixture<GarminGatewayFactory>
{
  [Fact]
  public async Task Pairing_token_is_shown_once_hashed_at_rest_and_revocable()
  {
    using (HttpClient insecure = factory.CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false }))
    {
      using HttpResponseMessage blocked = await insecure.PostAsJsonAsync($"/api/integrations/garmin/watch/profiles/{factory.SecondProfileId}", new { deviceLabel = "Unsafe token request" });
      Assert.Equal(HttpStatusCode.UpgradeRequired, blocked.StatusCode);
    }
    using HttpClient client = factory.CreateClient(new WebApplicationFactoryClientOptions
    {
      AllowAutoRedirect = false,
      BaseAddress = new Uri("https://localhost"),
    });
    JsonElement uploadStatus = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/status");
    Assert.False(uploadStatus.GetProperty("connected").GetBoolean());
    Assert.Equal("Disconnected", uploadStatus.GetProperty("state").GetString());
    JsonElement uploadJobs = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/jobs");
    Assert.Equal(JsonValueKind.Array, uploadJobs.ValueKind);
    Assert.Equal(0, uploadJobs.GetArrayLength());

    using HttpResponseMessage pair = await client.PostAsJsonAsync($"/api/integrations/garmin/watch/profiles/{factory.ProfileId}", new { deviceLabel = "Marc Fenix 8" });
    pair.EnsureSuccessStatusCode();
    JsonElement payload = await pair.Content.ReadFromJsonAsync<JsonElement>();
    string token = payload.GetProperty("token").GetString()!;
    int version = payload.GetProperty("binding").GetProperty("version").GetInt32();
    Assert.True(token.Length >= 40);

    await using (TreadmillRunnerDbContext context = await factory.CreateContextAsync())
    {
      GarminWatchBindingEntity stored = await context.GarminWatchBindings.AsNoTracking().SingleAsync(item => item.UserProfileId == factory.ProfileId);
      Assert.NotEqual(token, stored.TokenSha256);
      Assert.Equal(64, stored.TokenSha256.Length);
    }

    client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
    JsonElement status = await client.GetFromJsonAsync<JsonElement>("/api/watch/status");
    Assert.Equal("Marc", status.GetProperty("runnerName").GetString());
    Assert.Equal("Ready", status.GetProperty("state").GetString());

    using HttpResponseMessage revoke = await client.PostAsJsonAsync($"/api/integrations/garmin/watch/profiles/{factory.ProfileId}/revoke", new { expectedVersion = version });
    Assert.Equal(HttpStatusCode.NoContent, revoke.StatusCode);
    using HttpResponseMessage after = await client.GetAsync("/api/watch/status");
    Assert.Equal(HttpStatusCode.Unauthorized, after.StatusCode);
  }
}
