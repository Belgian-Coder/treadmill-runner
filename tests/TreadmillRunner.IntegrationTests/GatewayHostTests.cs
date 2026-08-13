using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Gateway.Health;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Diagnostics;
using TreadmillRunner.Gateway.Hosting;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Core.System;
using System.Text.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class GatewayHostTests(WebApplicationFactory<TreadmillRunner.Gateway.Program> factory) : IClassFixture<WebApplicationFactory<TreadmillRunner.Gateway.Program>>
{
  [Fact]
  public async Task Operational_telemetry_is_bounded_normalized_and_correlated()
  {
    using HttpClient client = factory.CreateClient();
    const string correlationId = "acceptance-telemetry-001";
    using var request = new HttpRequestMessage(HttpMethod.Get, $"/api/planning/sessions/{Guid.NewGuid():D}");
    request.Headers.Add(GatewayPipelineExtensions.CorrelationHeaderName, correlationId);

    using HttpResponseMessage response = await client.SendAsync(request);
    Assert.True(response.Headers.TryGetValues(GatewayPipelineExtensions.CorrelationHeaderName, out IEnumerable<string>? values));
    Assert.Equal(correlationId, Assert.Single(values));

    OperationalTelemetrySnapshot? snapshot = await client.GetFromJsonAsync<OperationalTelemetrySnapshot>("/api/operations/telemetry");
    Assert.NotNull(snapshot);
    Assert.InRange(snapshot.Routes.Count, 1, 64);
    Assert.Contains(snapshot.Routes, static route => route.Route == "/api/planning/sessions/{id}");
    Assert.DoesNotContain(snapshot.Routes, static route => route.Route.Contains('?', StringComparison.Ordinal));
  }

  [Theory]
  [InlineData("/api/planning/sessions/efb3bcc5-d94b-4a68-8b79-d0f14d5be76c", "/api/planning/sessions/{id}")]
  [InlineData("/api/planning/sessions/12345", "/api/planning/sessions/{id}")]
  [InlineData("/", "/")]
  public void Operational_routes_remove_unbounded_identifiers(string path, string expected) =>
    Assert.Equal(expected, OperationalTelemetry.NormalizeRoute(new PathString(path)));

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
  public async Task Pwa_manifest_and_offline_safety_assets_preserve_the_network_only_application_boundary()
  {
    using HttpClient client = factory.CreateClient();

    using HttpResponseMessage manifestResponse = await client.GetAsync("/manifest.webmanifest");
    Assert.Equal(HttpStatusCode.OK, manifestResponse.StatusCode);
    Assert.Equal("application/manifest+json", manifestResponse.Content.Headers.ContentType?.MediaType);
    Assert.Contains("no-store", manifestResponse.Headers.CacheControl?.ToString(), StringComparison.OrdinalIgnoreCase);
    using JsonDocument manifest = JsonDocument.Parse(await manifestResponse.Content.ReadAsStringAsync());
    Assert.Equal("/", manifest.RootElement.GetProperty("id").GetString());
    Assert.Equal("/", manifest.RootElement.GetProperty("scope").GetString());
    Assert.Equal("standalone", manifest.RootElement.GetProperty("display").GetString());
    Assert.Equal("navigate-existing", manifest.RootElement.GetProperty("launch_handler").GetProperty("client_mode").GetString());
    string[] shortcutUrls = manifest.RootElement.GetProperty("shortcuts")
      .EnumerateArray()
      .Select(static shortcut => shortcut.GetProperty("url").GetString()!)
      .ToArray();
    Assert.Equal(["/", "/calendar", "/history", "/operations"], shortcutUrls);

    using HttpResponseMessage bridge = await client.GetAsync("/pwa-shell.js");
    using HttpResponseMessage worker = await client.GetAsync("/service-worker.js");
    using HttpResponseMessage offline = await client.GetAsync("/offline.html");
    foreach (HttpResponseMessage response in new[] { bridge, worker, offline })
    {
      Assert.Equal(HttpStatusCode.OK, response.StatusCode);
      Assert.Contains("no-store", response.Headers.CacheControl?.ToString(), StringComparison.OrdinalIgnoreCase);
    }
    Assert.Equal("text/javascript", bridge.Content.Headers.ContentType?.MediaType);
    Assert.Equal("text/javascript", worker.Content.Headers.ContentType?.MediaType);
    Assert.Equal("text/html", offline.Content.Headers.ContentType?.MediaType);

    using HttpResponseMessage entry = await client.GetAsync("/workouts");
    string entryDocument = await entry.Content.ReadAsStringAsync();
    Assert.Contains("id=\"app-boot-shell\"", entryDocument, StringComparison.Ordinal);
    Assert.Contains("Loading TreadmillRunner", entryDocument, StringComparison.Ordinal);

    using HttpResponseMessage operationsEntry = await client.GetAsync("/operations");
    string operationsDocument = await operationsEntry.Content.ReadAsStringAsync();
    Assert.Contains("<h1>Operations</h1>", operationsDocument, StringComparison.Ordinal);
    Assert.Contains("Loading maintenance controls", operationsDocument, StringComparison.Ordinal);
    Assert.Contains("Private access", operationsDocument, StringComparison.Ordinal);
    Assert.Contains("/api/operations/access/qr/", operationsDocument, StringComparison.Ordinal);
    Assert.Contains("fetchpriority=\"high\"", operationsDocument, StringComparison.Ordinal);
    Assert.DoesNotContain("Confirm activation", operationsDocument, StringComparison.Ordinal);
    string bridgeSource = await bridge.Content.ReadAsStringAsync();
    Assert.Contains("document.getElementById(\"main-content\")", bridgeSource, StringComparison.Ordinal);
    Assert.Contains("shell.remove()", bridgeSource, StringComparison.Ordinal);

    string workerSource = await worker.Content.ReadAsStringAsync();
    Assert.Contains("event.request.mode !== \"navigate\"", workerSource, StringComparison.Ordinal);
    Assert.Contains("[502, 503, 504]", workerSource, StringComparison.Ordinal);
    Assert.DoesNotContain("skipWaiting", workerSource, StringComparison.Ordinal);
    Assert.DoesNotContain("clients.claim", workerSource, StringComparison.Ordinal);
    string offlineDocument = await offline.Content.ReadAsStringAsync();
    Assert.Contains("Wi-Fi or Bluetooth loss does not stop the treadmill belt", offlineDocument, StringComparison.Ordinal);
    Assert.Contains("physical Stop control", offlineDocument, StringComparison.Ordinal);
    Assert.DoesNotContain("<script", offlineDocument, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("<link", offlineDocument, StringComparison.OrdinalIgnoreCase);
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
