using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.AspNetCore.Hosting;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Gateway.Health;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Core.System;

namespace TreadmillRunner.IntegrationTests;

public sealed class GatewayHostTests(WebApplicationFactory<TreadmillRunner.Gateway.Program> factory) : IClassFixture<WebApplicationFactory<TreadmillRunner.Gateway.Program>>
{
  [Fact]
  public async Task Health_endpoints_are_healthy()
  {
    using var client = factory.CreateClient();

    var live = await client.GetAsync("/health/live");
    var ready = await client.GetAsync("/health/ready");
    var ble = await client.GetAsync("/health/ble");

    Assert.Equal(HttpStatusCode.OK, live.StatusCode);
    Assert.Equal(HttpStatusCode.OK, ready.StatusCode);
    Assert.Equal(HttpStatusCode.OK, ble.StatusCode);
  }

  [Fact]
  public async Task Snapshot_endpoint_returns_authoritative_live_contract()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> production = factory.WithWebHostBuilder(
      builder => builder.UseEnvironment("Production"));
    using var client = production.CreateClient();

    var snapshot = await client.GetFromJsonAsync<LiveSnapshot>("/api/live/snapshot");

    Assert.NotNull(snapshot);
    Assert.True(snapshot.CapturedAt <= DateTimeOffset.UtcNow);
    Assert.Equal(TreadmillRunner.Core.Sessions.SessionState.Idle, snapshot.SessionState);
    Assert.Equal(0, snapshot.SpeedKph);
    Assert.Null(snapshot.HeartRateBpm);
  }

  [Fact]
  public async Task Version_and_entry_documents_are_not_cacheable_and_stale_mutations_are_rejected()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage version = await client.GetAsync("/api/system/version");
    Assert.Equal(HttpStatusCode.OK, version.StatusCode);
    Assert.Contains("no-store", version.Headers.CacheControl?.ToString(), StringComparison.OrdinalIgnoreCase);
    Assert.Contains(AppBuildInfo.Fingerprint, await version.Content.ReadAsStringAsync(), StringComparison.Ordinal);
    using HttpResponseMessage manifest = await client.GetAsync("/manifest.webmanifest");
    Assert.Contains("no-store", manifest.Headers.CacheControl?.ToString(), StringComparison.OrdinalIgnoreCase);
    using HttpResponseMessage touchIcon = await client.GetAsync("/apple-touch-icon-180.png");
    Assert.Contains("no-store", touchIcon.Headers.CacheControl?.ToString(), StringComparison.OrdinalIgnoreCase);

    using var request = new HttpRequestMessage(HttpMethod.Post, "/api/updates/check") { Content = JsonContent.Create(new { }) };
    request.Headers.Add("X-TreadmillRunner-Client-Build", "stale-build");
    using HttpResponseMessage stale = await client.SendAsync(request);
    Assert.Equal(HttpStatusCode.Conflict, stale.StatusCode);
    Assert.Equal(AppBuildInfo.Fingerprint, stale.Headers.GetValues("X-TreadmillRunner-Server-Build").Single());
    Assert.Contains("ClientUpdateRequired", await stale.Content.ReadAsStringAsync(), StringComparison.Ordinal);

    using var missingRequest = new HttpRequestMessage(HttpMethod.Post, "/api/updates/check") { Content = JsonContent.Create(new { }) };
    missingRequest.Headers.Add("Sec-Fetch-Site", "same-origin");
    using HttpResponseMessage missing = await client.SendAsync(missingRequest);
    Assert.Equal(HttpStatusCode.Conflict, missing.StatusCode);
    Assert.Contains("ClientUpdateRequired", await missing.Content.ReadAsStringAsync(), StringComparison.Ordinal);

    using var matchingRequest = new HttpRequestMessage(HttpMethod.Post, "/api/updates/check") { Content = JsonContent.Create(new { }) };
    matchingRequest.Headers.Add("Sec-Fetch-Site", "same-origin");
    matchingRequest.Headers.Add("X-TreadmillRunner-Client-Build", AppBuildInfo.Fingerprint);
    using HttpResponseMessage matching = await client.SendAsync(matchingRequest);
    Assert.NotEqual(HttpStatusCode.Conflict, matching.StatusCode);
  }

  [Fact]
  public async Task Live_hub_negotiate_route_starts()
  {
    using var client = factory.CreateClient();

    using var response = await client.PostAsync("/hubs/live/negotiate?negotiateVersion=1", content: null);

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
  }

  [Fact]
  public void Gateway_registers_the_Omega_Z_profile_through_the_generic_protocol_registry()
  {
    _ = factory.CreateClient();
    var registry = factory.Services.GetRequiredService<TreadmillProtocolRegistry>();

    var match = registry.Resolve(new TreadmillAdvertisementIdentity("JFTMOmega Z", []));

    Assert.NotNull(match);
    Assert.Equal("horizon-omega-z", match.ProtocolId);
  }

  [Fact]
  public async Task Ble_health_requires_fresh_treadmill_and_heart_rate_telemetry()
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var devices = new FixedDevices(new DeviceTelemetrySnapshot(
      now,
      new DeviceConnectionSnapshot(DeviceRole.Treadmill, DeviceConnectionState.Ready, 4, "Omega Z", "horizon-omega-z", "Ftms", now, null),
      new DeviceConnectionSnapshot(DeviceRole.HeartRate, DeviceConnectionState.Ready, 7, "Polar H10", "bluetooth-heart-rate", null, now, null),
      new TreadmillTelemetry(now, 0, 0),
      132,
      now,
      null));

    HealthCheckResult healthy = await new BleDiagnosticHealthCheck(devices).CheckHealthAsync(new HealthCheckContext());
    devices.Snapshot = devices.Snapshot with { HeartRateObservedAt = now.AddSeconds(-6) };
    HealthCheckResult stale = await new BleDiagnosticHealthCheck(devices).CheckHealthAsync(new HealthCheckContext());

    Assert.Equal(HealthStatus.Healthy, healthy.Status);
    Assert.Equal(HealthStatus.Degraded, stale.Status);
    Assert.Contains("heart-rate", stale.Description, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Application_maintenance_rejects_concurrent_http_mutations()
  {
    using HttpClient client = factory.CreateClient();
    IApplicationMaintenanceState maintenance = factory.Services.GetRequiredService<IApplicationMaintenanceState>();
    Assert.True(maintenance.TryBegin());
    try
    {
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/updates/check", new { });
      Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
      Assert.Contains("maintenance", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
    }
    finally
    {
      maintenance.End();
    }
  }

  private sealed class FixedDevices(DeviceTelemetrySnapshot snapshot) : IReadOnlyDeviceCoordinator
  {
    public DeviceTelemetrySnapshot Snapshot { get; set; } = snapshot;
    public DeviceTelemetrySnapshot Current => Snapshot;
  }
}
