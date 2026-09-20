using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Reflection;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Gateway.Live;
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
    using HttpResponseMessage noWatch = await client.GetAsync($"/api/integrations/garmin/watch/profiles/{factory.ProfileId}");
    Assert.Equal(HttpStatusCode.NoContent, noWatch.StatusCode);
    Assert.Equal(0, noWatch.Content.Headers.ContentLength ?? 0);
    JsonElement uploadStatus = await client.GetFromJsonAsync<JsonElement>($"/api/integrations/garmin/activity-upload/profiles/{factory.ProfileId}/status");
    Assert.False(uploadStatus.GetProperty("connected").GetBoolean());
    Assert.Equal("Disconnected", uploadStatus.GetProperty("state").GetString());
    Assert.Equal(GarminAdapterReadinessStates.Ready, uploadStatus.GetProperty("adapterState").GetString());
    Assert.True(uploadStatus.GetProperty("canConnect").GetBoolean());
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

  [Fact]
  public async Task Watch_status_excludes_retained_terminal_session()
  {
    DateTimeOffset now = DateTimeOffset.Parse("2026-09-19T14:23:05Z");
    var terminal = new ActiveSessionSnapshot(
      Guid.NewGuid(),
      factory.ProfileId,
      "Marc",
      factory.WorkoutRevisionId,
      "Completed run",
      new LiveSnapshot(
        now,
        DeviceConnectionState.Ready,
        DeviceConnectionState.Ready,
        SessionState.Completed,
        0,
        0,
        null,
        TimeSpan.FromMinutes(30),
        3,
        null,
        TimeSpan.Zero),
      4,
      null,
      null,
      null,
      null,
      0,
      null,
      0,
      null,
      HeartRateSource.None,
      null,
      SessionControlAccess.Observer,
      null,
      []);
    ILiveSessionCoordinator coordinator = DispatchProxy.Create<ILiveSessionCoordinator, FixedLiveSessionCoordinator>();
    ((FixedLiveSessionCoordinator)(object)coordinator).Snapshot = terminal;
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> terminalFactory = factory.WithWebHostBuilder(builder =>
      builder.ConfigureServices(services =>
      {
        services.RemoveAll<ILiveSessionCoordinator>();
        services.AddSingleton(coordinator);
      }));
    using HttpClient client = terminalFactory.CreateClient(new WebApplicationFactoryClientOptions
    {
      AllowAutoRedirect = false,
      BaseAddress = new Uri("https://localhost"),
    });
    using HttpResponseMessage pair = await client.PostAsJsonAsync(
      $"/api/integrations/garmin/watch/profiles/{factory.ProfileId}",
      new { deviceLabel = "Terminal-session watch" });
    pair.EnsureSuccessStatusCode();
    JsonElement payload = await pair.Content.ReadFromJsonAsync<JsonElement>();
    int version = payload.GetProperty("binding").GetProperty("version").GetInt32();
    client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue(
      "Bearer",
      payload.GetProperty("token").GetString());

    JsonElement status = await client.GetFromJsonAsync<JsonElement>("/api/watch/status");

    Assert.Equal("Manual treadmill", status.GetProperty("sessionTitle").GetString());
    Assert.Equal("Ready", status.GetProperty("state").GetString());
    Assert.Equal(JsonValueKind.Null, status.GetProperty("sessionId").ValueKind);
    using HttpResponseMessage revoke = await client.PostAsJsonAsync(
      $"/api/integrations/garmin/watch/profiles/{factory.ProfileId}/revoke",
      new { expectedVersion = version });
    Assert.Equal(HttpStatusCode.NoContent, revoke.StatusCode);
  }

  private class FixedLiveSessionCoordinator : DispatchProxy
  {
    public ActiveSessionSnapshot? Snapshot { get; set; }

    protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
    {
      ArgumentNullException.ThrowIfNull(targetMethod);
      return targetMethod.Name switch
      {
        "get_CurrentSession" => Snapshot,
        "get_IsHardwareSession" => false,
        _ => throw new NotSupportedException(targetMethod.Name),
      };
    }
  }
}
