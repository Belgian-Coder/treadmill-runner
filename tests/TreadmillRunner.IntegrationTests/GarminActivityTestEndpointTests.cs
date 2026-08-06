using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminActivityTestEndpointTests(GarminGatewayFactory factory) : IClassFixture<GarminGatewayFactory>
{
  [Fact]
  public async Task Test_activity_uses_the_normal_session_export_and_durable_upload_job()
  {
    var adapter = new ConfirmingAdapter();
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = factory.WithWebHostBuilder(builder =>
      builder.ConfigureServices(services =>
      {
        services.RemoveAll<IGarminActivityAdapter>();
        services.AddSingleton<IGarminActivityAdapter>(adapter);
      }));
    using HttpClient client = application.CreateClient(new WebApplicationFactoryClientOptions
    {
      AllowAutoRedirect = false,
      BaseAddress = new Uri("https://localhost"),
    });

    int version;
    using (IServiceScope scope = application.Services.CreateScope())
    {
      IGarminActivityUploadStore uploads = scope.ServiceProvider.GetRequiredService<IGarminActivityUploadStore>();
      GarminActivityConnectionService connections = scope.ServiceProvider.GetRequiredService<GarminActivityConnectionService>();
      GarminActivityUploadAccount account = await uploads.ConnectAsync(
        factory.ProfileId,
        "test-account",
        connections.Protect("test-token-store"),
        enabled: true,
        DateTimeOffset.UtcNow.AddMinutes(-1));
      version = account.Version;
    }

    Guid operationId = Guid.NewGuid();
    using HttpResponseMessage response = await client.PostAsJsonAsync(
      $"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/test-activity",
      new { operationId, expectedVersion = version });

    Assert.Equal(HttpStatusCode.Accepted, response.StatusCode);
    JsonElement history = await client.GetFromJsonAsync<JsonElement>($"/api/history/{operationId}");
    Assert.Equal("TreadmillRunner Garmin upload test", history.GetProperty("definition").GetProperty("workoutTitle").GetString());
    Assert.Equal(60, history.GetProperty("samples").GetArrayLength());

    JsonElement? confirmed = null;
    for (var attempt = 0; attempt < 50; attempt++)
    {
      JsonElement jobs = await client.GetFromJsonAsync<JsonElement>(
        $"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/jobs");
      if (jobs.GetArrayLength() == 1 && jobs[0].GetProperty("status").GetString() == "Confirmed")
      {
        confirmed = jobs[0];
        break;
      }
      await Task.Delay(50);
    }

    Assert.True(confirmed.HasValue);
    Assert.Equal(operationId, confirmed.Value.GetProperty("workoutSessionId").GetGuid());
    Assert.True(adapter.SawFit);

    using HttpResponseMessage replay = await client.PostAsJsonAsync(
      $"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/test-activity",
      new { operationId, expectedVersion = version });
    Assert.Equal(HttpStatusCode.Accepted, replay.StatusCode);
    Assert.Contains("already completed", await replay.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
  }

  private sealed class ConfirmingAdapter : IGarminActivityAdapter
  {
    public bool SawFit { get; private set; }

    public Task<IGarminAdapterConnectProcess> BeginConnectAsync(string email, string password, CancellationToken cancellationToken) =>
      throw new NotSupportedException();

    public Task<GarminAdapterMessage> UploadAsync(string tokenStore, string activityPath, CancellationToken cancellationToken)
    {
      SawFit = tokenStore == "test-token-store" && File.Exists(activityPath) && new FileInfo(activityPath).Length > 16;
      return Task.FromResult(new GarminAdapterMessage("confirmed", null, null, null, "updated-token-store", "test-remote-id"));
    }
  }
}
