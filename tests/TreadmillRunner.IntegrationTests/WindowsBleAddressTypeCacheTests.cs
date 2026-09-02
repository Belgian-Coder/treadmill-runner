using TreadmillRunner.Infrastructure.Bluetooth;
using Windows.Devices.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class WindowsBleAddressTypeCacheTests
{
  [Fact]
  public void Cache_evicts_oldest_observation_at_strict_capacity_bound()
  {
    var cache = new BluetoothAddressTypeCache(
      capacity: 2,
      ttl: TimeSpan.FromMinutes(10));
    DateTimeOffset observedAt = new(2099, 8, 29, 12, 0, 0, TimeSpan.Zero);

    cache.Observe("000000000001", BluetoothAddressType.Public, observedAt);
    cache.Observe("000000000002", BluetoothAddressType.Random, observedAt.AddSeconds(1));
    cache.Observe("000000000003", BluetoothAddressType.Public, observedAt.AddSeconds(2));

    Assert.Equal(2, cache.Count);
    Assert.Null(cache.TryGet("000000000001", observedAt.AddSeconds(2)));
    Assert.Equal(
      BluetoothAddressType.Random,
      cache.TryGet("000000000002", observedAt.AddSeconds(2)));
    Assert.Equal(
      BluetoothAddressType.Public,
      cache.TryGet("000000000003", observedAt.AddSeconds(2)));
  }

  [Fact]
  public void Cache_expires_address_type_observations()
  {
    var cache = new BluetoothAddressTypeCache(
      capacity: 2,
      ttl: TimeSpan.FromSeconds(30));
    DateTimeOffset observedAt = new(2099, 8, 29, 12, 0, 0, TimeSpan.Zero);

    cache.Observe("A1B2C3D4E5F6", BluetoothAddressType.Random, observedAt);

    Assert.Equal(
      BluetoothAddressType.Random,
      cache.TryGet("a1b2c3d4e5f6", observedAt.AddSeconds(29)));
    Assert.Null(cache.TryGet("A1B2C3D4E5F6", observedAt.AddSeconds(30)));
    Assert.Equal(0, cache.Count);
  }

  [Theory]
  [InlineData(BluetoothAddressType.Public, BluetoothAddressType.Public)]
  [InlineData(BluetoothAddressType.Random, BluetoothAddressType.Random)]
  [InlineData(BluetoothAddressType.Unspecified, null)]
  public void Only_public_or_random_observations_select_address_type_overload(
    BluetoothAddressType observedType,
    BluetoothAddressType? expected)
  {
    Assert.Equal(
      expected,
      WindowsBleAddressTypePolicy.SelectForConnection(observedType));
  }

  [Fact]
  public void Unknown_numeric_address_type_falls_back_to_one_argument_overload()
  {
    var unknown = (BluetoothAddressType)1234;

    Assert.Null(WindowsBleAddressTypePolicy.SelectForConnection(unknown));
  }

  [Fact]
  public void Cache_unknown_observation_returns_null_for_one_argument_fallback()
  {
    var cache = new BluetoothAddressTypeCache(ttl: TimeSpan.FromMinutes(1));
    DateTimeOffset observedAt = new(2099, 8, 29, 12, 0, 0, TimeSpan.Zero);
    cache.Observe("A1B2C3D4E5F6", BluetoothAddressType.Unspecified, observedAt);

    Assert.Null(cache.TryGet("A1B2C3D4E5F6", observedAt.AddSeconds(1)));
  }
}
