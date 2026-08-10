using System.Net;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class DeviceEnrollmentEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  private static readonly Guid CyclingSpeedAndCadence = Guid.Parse("00001816-0000-1000-8000-00805f9b34fb");
  private static readonly Guid Ftms = Guid.Parse("00001826-0000-1000-8000-00805f9b34fb");
  private static readonly Guid HeartRate = Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");
  private static readonly Guid Polar = Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb");

  [Fact]
  public async Task Scan_offers_only_the_unnamed_complete_Omega_service_signature()
  {
    var transport = new AdvertisementOnlyTransport(
    [
      new BleAdvertisement("anonymous-complete", null, -45, [CyclingSpeedAndCadence, Ftms]),
      new BleAdvertisement("named-other", "Future Treadmill", -40, [CyclingSpeedAndCadence, Ftms]),
      new BleAdvertisement("anonymous-incomplete", null, -35, [Ftms]),
    ]);
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = factory.WithWebHostBuilder(
      builder => builder.ConfigureServices(services =>
      {
        services.RemoveAll<IBleCentralTransport>();
        services.AddSingleton<IBleCentralTransport>(transport);
      }));
    using HttpClient client = application.CreateClient();

    JsonElement result = await client.GetFromJsonAsync<JsonElement>("/api/devices/scan?durationSeconds=1");

    JsonElement candidate = Assert.Single(result.EnumerateArray());
    Assert.Equal("anonymous-complete", candidate.GetProperty("deviceId").GetString());
    Assert.Contains(
      candidate.GetProperty("supportedRoles").EnumerateArray(),
      role => role.GetString() == "Treadmill");
  }

  [Fact]
  public async Task Scan_lists_unnamed_Polar_first_and_classifies_watches()
  {
    var transport = new AdvertisementOnlyTransport(
    [
      new BleAdvertisement("watch-device", "Garmin Forerunner 965", -35, [HeartRate]),
      new BleAdvertisement("generic-device", "Generic HRM", -30, [HeartRate]),
      new BleAdvertisement("polar-device", null, -58, [HeartRate, Polar]),
    ]);
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = factory.WithWebHostBuilder(
      builder => builder.ConfigureServices(services =>
      {
        services.RemoveAll<IBleCentralTransport>();
        services.AddSingleton<IBleCentralTransport>(transport);
      }));
    using HttpClient client = application.CreateClient();

    JsonElement result = await client.GetFromJsonAsync<JsonElement>("/api/devices/scan?durationSeconds=1");
    JsonElement[] candidates = result.EnumerateArray().ToArray();

    Assert.Equal(3, candidates.Length);
    Assert.Equal("polar-device", candidates[0].GetProperty("deviceId").GetString());
    Assert.True(candidates[0].GetProperty("isPreferredHeartRate").GetBoolean());
    Assert.Equal("ChestStrap", candidates[0].GetProperty("heartRateDeviceKind").GetString());
    Assert.Equal("Watch", candidates[1].GetProperty("heartRateDeviceKind").GetString());
    Assert.False(candidates[1].GetProperty("isPreferredHeartRate").GetBoolean());
  }

  [Fact]
  public async Task Enrolls_one_device_per_role_replays_and_forgets_by_version()
  {
    using HttpClient client = factory.CreateClient();
    Guid treadmillOperation = Guid.NewGuid();
    var treadmill = new
    {
      operationId = treadmillOperation,
      role = "Treadmill",
      deviceId = "A1B2C3D4E5F6",
      displayName = "JFTMOmega Z",
      advertisedName = "JFTMOmega Z",
      serviceUuids = new[] { Ftms },
      modelNumber = "Omega Z",
      firmwareRevision = (string?)null,
      telemetryMode = "Ftms",
    };

    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/devices/enrollments", treadmill);
    using HttpResponseMessage replay = await client.PostAsJsonAsync("/api/devices/enrollments", treadmill);
    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    Assert.Equal(HttpStatusCode.Created, replay.StatusCode);
    JsonElement createdJson = await created.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal("horizon-omega-z", createdJson.GetProperty("protocolId").GetString());
    Assert.Equal("Ftms", createdJson.GetProperty("telemetryMode").GetString());
    Assert.False(createdJson.GetProperty("capabilities").GetProperty("canStartRemotely").GetBoolean());

    using HttpResponseMessage duplicate = await client.PostAsJsonAsync(
      "/api/devices/enrollments",
      treadmill with { operationId = Guid.NewGuid(), deviceId = "112233445566" });
    Assert.Equal(HttpStatusCode.Conflict, duplicate.StatusCode);

    using HttpResponseMessage heart = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "HeartRate",
      deviceId = "102030405060",
      displayName = "Polar H10",
      serviceUuids = new[] { HeartRate },
      modelNumber = "H10",
      firmwareRevision = (string?)null,
      telemetryMode = (string?)null,
    });
    Assert.Equal(HttpStatusCode.Created, heart.StatusCode);
    JsonElement list = await client.GetFromJsonAsync<JsonElement>("/api/devices/enrollments");
    Assert.Single(list.EnumerateArray(), item => item.GetProperty("role").GetString() == "Treadmill");
    Assert.Contains(list.EnumerateArray(), item => item.GetProperty("deviceId").GetString() == "102030405060");

    using HttpRequestMessage forgetRequest = new(HttpMethod.Delete, "/api/devices/enrollments/Treadmill")
    {
      Content = JsonContent.Create(new { operationId = Guid.NewGuid(), expectedVersion = 1 }),
    };
    using HttpResponseMessage forgotten = await client.SendAsync(forgetRequest);
    Assert.Equal(HttpStatusCode.NoContent, forgotten.StatusCode);
    JsonElement afterForget = await client.GetFromJsonAsync<JsonElement>("/api/devices/enrollments");
    Assert.DoesNotContain(afterForget.EnumerateArray(), item => item.GetProperty("role").GetString() == "Treadmill");
  }

  [Fact]
  public async Task Enrolls_anonymous_dual_service_candidate_without_promoting_control_capabilities()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage created = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "Treadmill",
      deviceId = "A0BB3E102117",
      displayName = "Unnamed Bluetooth device",
      advertisedName = (string?)null,
      serviceUuids = new[] { CyclingSpeedAndCadence, Ftms },
      modelNumber = (string?)null,
      firmwareRevision = (string?)null,
      telemetryMode = "Ftms",
    });

    Assert.Equal(HttpStatusCode.Created, created.StatusCode);
    JsonElement json = await created.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal("horizon-omega-z", json.GetProperty("protocolId").GetString());
    Assert.Equal("Unknown", json.GetProperty("evidence").GetString());
    JsonElement capabilities = json.GetProperty("capabilities");
    Assert.False(capabilities.GetProperty("canSetSpeedRemotely").GetBoolean());
    Assert.False(capabilities.GetProperty("canSetInclineRemotely").GetBoolean());
    Assert.False(capabilities.GetProperty("canPauseRemotely").GetBoolean());
    Assert.False(capabilities.GetProperty("canStopRemotely").GetBoolean());
    Assert.False(capabilities.GetProperty("canStartRemotely").GetBoolean());

    using var forgetRequest = new HttpRequestMessage(HttpMethod.Delete, "/api/devices/enrollments/Treadmill")
    {
      Content = JsonContent.Create(new { operationId = Guid.NewGuid(), expectedVersion = 1 }),
    };
    using HttpResponseMessage forgotten = await client.SendAsync(forgetRequest);
    Assert.Equal(HttpStatusCode.NoContent, forgotten.StatusCode);
  }

  [Fact]
  public async Task Rejects_heart_rate_device_without_standard_service()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "HeartRate",
      deviceId = "665544332211",
      displayName = "Not HR",
      serviceUuids = Array.Empty<Guid>(),
      modelNumber = (string?)null,
      firmwareRevision = (string?)null,
      telemetryMode = (string?)null,
    });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
  }

  [Fact]
  public async Task Enrolls_multiple_heart_rate_devices_and_forgets_one_by_id()
  {
    using HttpClient client = factory.CreateClient();
    async Task<HttpResponseMessage> EnrollAsync(string deviceId, string name) =>
      await client.PostAsJsonAsync("/api/devices/enrollments", new
      {
        operationId = Guid.NewGuid(),
        role = "HeartRate",
        deviceId,
        displayName = name,
        serviceUuids = new[] { HeartRate },
        telemetryMode = (string?)null,
        ownerProfileIds = Array.Empty<Guid>(),
        autoConnect = true,
      });

    using HttpResponseMessage polar = await EnrollAsync("POLAR-MULTI", "Polar H10 A1B2C3D4");
    using HttpResponseMessage watch = await EnrollAsync("GARMIN-MULTI", "Garmin fēnix 8");
    Assert.Equal(HttpStatusCode.Created, polar.StatusCode);
    Assert.Equal(HttpStatusCode.Created, watch.StatusCode);
    JsonElement watchJson = await watch.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal("Watch", watchJson.GetProperty("heartRateDeviceKind").GetString());
    Assert.Equal("Garmin", watchJson.GetProperty("heartRateDeviceFamily").GetString());

    using var forget = new HttpRequestMessage(
      HttpMethod.Delete,
      $"/api/devices/enrollments/{watchJson.GetProperty("id").GetGuid()}")
    {
      Content = JsonContent.Create(new { operationId = Guid.NewGuid(), expectedVersion = 1 }),
    };
    using HttpResponseMessage forgotten = await client.SendAsync(forget);
    Assert.Equal(HttpStatusCode.NoContent, forgotten.StatusCode);
  }

  [Fact]
  public async Task Reliability_report_is_bounded_and_does_not_expose_ble_device_identifiers()
  {
    using HttpClient client = factory.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/devices/reliability?days=7");
    response.EnsureSuccessStatusCode();
    string json = await response.Content.ReadAsStringAsync();
    using JsonDocument document = JsonDocument.Parse(json);

    Assert.Equal(7, document.RootElement.GetProperty("windowDays").GetInt32());
    Assert.Equal("no-store", response.Headers.CacheControl?.ToString());
    Assert.DoesNotContain("\"deviceId\"", json, StringComparison.OrdinalIgnoreCase);
    Assert.DoesNotContain("identityFingerprint", json, StringComparison.OrdinalIgnoreCase);

    using HttpResponseMessage invalid = await client.GetAsync("/api/devices/reliability?days=0");
    Assert.Equal(HttpStatusCode.BadRequest, invalid.StatusCode);
  }

  [Fact]
  public async Task Reliability_report_merges_the_live_coalesced_failure_count()
  {
    var coordinator = new ReliabilityCountCoordinator();
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = factory.WithWebHostBuilder(
      builder => builder.ConfigureServices(services =>
      {
        services.RemoveAll<IReadOnlyDeviceCoordinator>();
        services.AddSingleton<IReadOnlyDeviceCoordinator>(coordinator);
      }));
    using HttpClient client = application.CreateClient();
    using HttpResponseMessage enrolled = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "HeartRate",
      deviceId = $"LIVE-RELIABILITY-{Guid.NewGuid():N}",
      displayName = "Live reliability sensor",
      serviceUuids = new[] { HeartRate },
      telemetryMode = (string?)null,
      ownerProfileIds = Array.Empty<Guid>(),
      autoConnect = false,
    });
    enrolled.EnsureSuccessStatusCode();
    JsonElement enrollment = await enrolled.Content.ReadFromJsonAsync<JsonElement>();
    Guid enrollmentId = enrollment.GetProperty("id").GetGuid();
    coordinator.SetFailureCount(enrollmentId, 7);

    JsonElement report = await client.GetFromJsonAsync<JsonElement>("/api/devices/reliability?days=7");
    JsonElement device = Assert.Single(
      report.GetProperty("devices").EnumerateArray(),
      item => item.GetProperty("enrollmentId").GetGuid() == enrollmentId);
    Assert.Equal(7, device.GetProperty("currentFailedAttemptCount").GetInt32());
  }

  private sealed class AdvertisementOnlyTransport(IReadOnlyList<BleAdvertisement> advertisements) :
    IBleCentralTransport
  {
    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      await Task.Yield();
      foreach (BleAdvertisement advertisement in advertisements)
      {
        cancellationToken.ThrowIfCancellationRequested();
        yield return advertisement;
      }
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      throw new InvalidOperationException("The scan test must not connect to a device.");
  }

  private sealed class ReliabilityCountCoordinator : IReadOnlyDeviceCoordinator
  {
    private readonly Dictionary<Guid, int> _failureCounts = [];

    public DeviceTelemetrySnapshot Current { get; } = new(
      DateTimeOffset.UtcNow,
      Disconnected(DeviceRole.Treadmill),
      Disconnected(DeviceRole.HeartRate),
      null,
      null,
      null,
      null);

    public int ActiveReliabilityFailureCount(Guid enrollmentId) =>
      _failureCounts.GetValueOrDefault(enrollmentId);

    public void SetFailureCount(Guid enrollmentId, int count) =>
      _failureCounts[enrollmentId] = count;

    private static DeviceConnectionSnapshot Disconnected(DeviceRole role) => new(
      role,
      DeviceConnectionState.Disconnected,
      0,
      null,
      null,
      null,
      null,
      null);
  }
}
