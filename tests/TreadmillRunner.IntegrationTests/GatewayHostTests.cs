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
