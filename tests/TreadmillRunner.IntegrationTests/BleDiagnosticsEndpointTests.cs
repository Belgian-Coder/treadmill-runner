using System.Net;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Core.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class BleDiagnosticsEndpointTests(BleDiagnosticsFactory factory) : IClassFixture<BleDiagnosticsFactory>
{
  [Fact]
  public async Task Scan_is_bounded_deduplicated_and_sorted()
  {
    using var client = factory.CreateClient();

    using var response = await client.GetAsync("/api/diagnostics/ble/scan?durationSeconds=1");

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    using var document = JsonDocument.Parse(await response.Content.ReadAsStreamAsync());
    var devices = document.RootElement.GetProperty("devices").EnumerateArray().ToArray();
    Assert.Equal(2, devices.Length);
    Assert.Equal("device-a", devices[0].GetProperty("deviceId").GetString());
    Assert.Equal(-40, devices[0].GetProperty("signalStrength").GetInt16());
    Assert.Equal("device-b", devices[1].GetProperty("deviceId").GetString());
  }

  [Fact]
  public async Task Scan_rejects_durations_outside_the_safe_range()
  {
    using var client = factory.CreateClient();

    using var response = await client.GetAsync("/api/diagnostics/ble/scan?durationSeconds=31");

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
  }

  [Fact]
  public async Task Gatt_enumeration_uses_the_requested_device_and_returns_sorted_read_only_metadata()
  {
    using var client = factory.CreateClient();

    using var response = await client.GetAsync("/api/diagnostics/ble/devices/device-a/gatt");

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.Equal("device-a", factory.Transport.LastConnectedDeviceId);
    using var document = JsonDocument.Parse(await response.Content.ReadAsStreamAsync());
    var services = document.RootElement.GetProperty("services").EnumerateArray().ToArray();
    Assert.Equal(2, services.Length);
    Assert.Equal("00000000-0000-0000-0000-000000000001", services[0].GetProperty("uuid").GetString());
    Assert.False(factory.Transport.WriteWasCalled);
    Assert.False(factory.Transport.SubscribeWasCalled);
  }

  [Fact]
  public async Task Gatt_enumeration_rejects_an_empty_or_oversized_device_id()
  {
    using var client = factory.CreateClient();
    var longDeviceId = new string('a', 257);

    using var response = await client.GetAsync($"/api/diagnostics/ble/devices/{longDeviceId}/gatt");

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
  }
}

public sealed class BleDiagnosticsFactory : WebApplicationFactory<TreadmillRunner.Gateway.Program>
{
  public FakeBleCentralTransport Transport { get; } = new();

  protected override void ConfigureWebHost(IWebHostBuilder builder)
  {
    builder.ConfigureServices(services =>
    {
      services.RemoveAll<IBleCentralTransport>();
      services.AddSingleton<IBleCentralTransport>(Transport);
    });
  }
}

public sealed class FakeBleCentralTransport : IBleCentralTransport
{
  private static readonly Guid FirstService = Guid.Parse("00000000-0000-0000-0000-000000000001");
  private static readonly Guid SecondService = Guid.Parse("00000000-0000-0000-0000-000000000002");

  public string? LastConnectedDeviceId { get; private set; }

  public bool SubscribeWasCalled { get; private set; }

  public bool WriteWasCalled { get; private set; }

  public async IAsyncEnumerable<BleAdvertisement> ScanAsync([EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    await Task.Yield();
    yield return new BleAdvertisement("device-b", "Runner B", -55, [SecondService]);
    yield return new BleAdvertisement("device-a", "Runner A", -65, [FirstService]);
    yield return new BleAdvertisement("device-a", "Runner A", -40, [FirstService, SecondService]);
  }

  public ValueTask<IBleConnection> ConnectAsync(string deviceId, CancellationToken cancellationToken = default)
  {
    LastConnectedDeviceId = deviceId;
    return ValueTask.FromResult<IBleConnection>(new FakeBleConnection(this, deviceId));
  }

  private sealed class FakeBleConnection(FakeBleCentralTransport owner, string deviceId) : IBleConnection
  {
    public string DeviceId { get; } = deviceId;

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;

    public ValueTask<IReadOnlyList<BleService>> DiscoverServicesAsync(CancellationToken cancellationToken = default)
    {
      IReadOnlyList<BleService> services =
      [
          new BleService(SecondService, [new BleCharacteristic(SecondService, Guid.Parse("00000000-0000-0000-0000-000000000004"), true, false, false)]),
                new BleService(FirstService, [new BleCharacteristic(FirstService, Guid.Parse("00000000-0000-0000-0000-000000000003"), true, false, true)]),
            ];
      return ValueTask.FromResult(services);
    }

    public ValueTask<ReadOnlyMemory<byte>> ReadAsync(
      Guid serviceUuid,
      Guid characteristicUuid,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromResult(ReadOnlyMemory<byte>.Empty);

    public async IAsyncEnumerable<BleNotification> SubscribeAsync(Guid serviceUuid, Guid characteristicUuid, [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      owner.SubscribeWasCalled = true;
      await Task.Yield();
      yield break;
    }

    public ValueTask WriteAsync(Guid serviceUuid, Guid characteristicUuid, ReadOnlyMemory<byte> value, CancellationToken cancellationToken = default)
    {
      owner.WriteWasCalled = true;
      return ValueTask.CompletedTask;
    }
  }
}
