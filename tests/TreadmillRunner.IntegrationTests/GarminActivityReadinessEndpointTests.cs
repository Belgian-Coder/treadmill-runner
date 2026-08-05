using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Gateway.Garmin;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminActivityReadinessEndpointTests(GarminGatewayFactory factory) : IClassFixture<GarminGatewayFactory>
{
  [Fact]
  public async Task Status_includes_safe_ready_adapter_contract()
  {
    using HttpClient client = factory.CreateClient();
    JsonElement status = await client.GetFromJsonAsync<JsonElement>(
      $"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/status");

    Assert.Equal(GarminAdapterReadinessStates.Ready, status.GetProperty("adapterState").GetString());
    Assert.Equal("Garmin activity upload is ready to connect.", status.GetProperty("adapterMessage").GetString());
    Assert.True(status.GetProperty("canConnect").GetBoolean());
  }

  [Fact]
  public async Task Connect_returns_safe_503_when_adapter_is_not_ready()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> unavailable = factory.WithWebHostBuilder(builder =>
      builder.ConfigureServices(services =>
      {
        services.RemoveAll<IGarminActivityAdapterReadiness>();
        services.AddSingleton<IGarminActivityAdapterReadiness>(new GarminGatewayFactory.FixedGarminAdapterReadiness(
          new(GarminAdapterReadinessStates.DependencyMissing, "The Garmin adapter dependency is missing. Install or repair the current signed release.", false)));
      }));
    using HttpClient client = unavailable.CreateClient(new WebApplicationFactoryClientOptions
    {
      AllowAutoRedirect = false,
      BaseAddress = new Uri("https://localhost"),
    });

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      $"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/connect",
      new { email = "runner@example.test", password = "never-log-this", enabled = false });

    Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
    string body = await response.Content.ReadAsStringAsync();
    Assert.DoesNotContain("never-log-this", body, StringComparison.Ordinal);
    Assert.DoesNotContain("runner@example.test", body, StringComparison.Ordinal);
    Assert.DoesNotContain("C:\\", body, StringComparison.OrdinalIgnoreCase);
    using JsonDocument json = JsonDocument.Parse(body);
    Assert.Equal(GarminAdapterReadinessStates.DependencyMissing, json.RootElement.GetProperty("adapterState").GetString());
    Assert.Equal("The Garmin adapter dependency is missing. Install or repair the current signed release.", json.RootElement.GetProperty("error").GetString());
  }
}
