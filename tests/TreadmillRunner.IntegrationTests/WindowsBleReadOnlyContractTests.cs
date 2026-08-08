using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Infrastructure.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;

namespace TreadmillRunner.IntegrationTests;

public sealed class WindowsBleReadOnlyContractTests
{
  [Fact]
  public void Adapter_is_parameterless_and_implements_portable_contract()
  {
    var constructor = typeof(WindowsBleCentralTransport).GetConstructor(Type.EmptyTypes);

    Assert.NotNull(constructor);
    Assert.IsAssignableFrom<IBleCentralTransport>(new WindowsBleCentralTransport());
    Assert.IsAssignableFrom<IBleCommandCentralTransport>(new WindowsBleCentralTransport());
    Assert.Null(typeof(IBleConnection).GetMethod("WriteAsync"));
    Assert.Null(typeof(IBleConnection).GetMethod("ExchangeAsync"));
    Assert.NotNull(typeof(IBleCommandConnection).GetMethod("ExchangeAsync"));
  }

  [Theory]
  [InlineData("07", "800001", false)]
  [InlineData("07", "800701", true)]
  [InlineData("026400", "800201", true)]
  [InlineData("07", "8007", false)]
  public void Command_connection_correlates_the_response_to_the_exact_written_opcode(
    string requestHex,
    string responseHex,
    bool expected)
  {
    Assert.Equal(
      expected,
      WindowsBleCommandConnection.IsResponseForRequest(
        Convert.FromHexString(requestHex),
        Convert.FromHexString(responseHex)));
  }

  [Fact]
  public async Task Command_connection_is_separate_and_precancellation_prevents_hardware_access()
  {
    var transport = new WindowsBleCentralTransport();
    await using IBleCommandConnection connection = await transport.ConnectCommandAsync("A1B2C3D4E5F6");
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(async () => await connection.ExchangeAsync(
      Guid.NewGuid(),
      Guid.NewGuid(),
      new byte[] { 0x00 },
      TimeSpan.FromSeconds(1),
      cancellation.Token));
  }

  [Fact]
  public void Maps_address_advertisement_and_characteristic_properties()
  {
    var heartRateService = Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");
    var advertisement = WindowsBleContractMapper.MapAdvertisement(
      0xA1B2C3D4E5F6,
      "Heart Rate",
      -57,
      [heartRateService]);
    var characteristic = WindowsBleContractMapper.MapCharacteristic(
      heartRateService,
      Guid.Parse("00002a37-0000-1000-8000-00805f9b34fb"),
      GattCharacteristicProperties.Read |
      GattCharacteristicProperties.WriteWithoutResponse |
      GattCharacteristicProperties.Indicate);

    Assert.Equal("A1B2C3D4E5F6", advertisement.DeviceId);
    Assert.Equal("Heart Rate", advertisement.Name);
    Assert.Equal((short)-57, advertisement.SignalStrength);
    Assert.Equal([heartRateService], advertisement.ServiceUuids);
    Assert.True(characteristic.CanRead);
    Assert.True(characteristic.CanWrite);
    Assert.True(characteristic.CanNotify);
  }

  [Fact]
  public async Task Precancelled_scan_stops_before_native_watcher_is_created()
  {
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();
    var transport = new WindowsBleCentralTransport();
    await using var enumerator = transport.ScanAsync(cancellation.Token).GetAsyncEnumerator();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      async () => await enumerator.MoveNextAsync());
  }

  [Fact]
  public async Task Connection_is_lazy_and_precancelled_discovery_stops_before_hardware_access()
  {
    var transport = new WindowsBleCentralTransport();
    await using var connection = await transport.ConnectAsync("A1B2C3D4E5F6");
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      async () => await connection.DiscoverServicesAsync(cancellation.Token));
  }

  [Fact]
  public async Task Read_and_subscribe_honor_precancellation_before_hardware_access()
  {
    var transport = new WindowsBleCentralTransport();
    await using var connection = await transport.ConnectAsync("A1B2C3D4E5F6");
    using var cancellation = new CancellationTokenSource();
    cancellation.Cancel();

    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      async () => await connection.ReadAsync(
        Guid.NewGuid(),
        Guid.NewGuid(),
        cancellation.Token));

    await using var notifications = connection.SubscribeAsync(
      Guid.NewGuid(),
      Guid.NewGuid(),
      cancellation.Token).GetAsyncEnumerator();
    await Assert.ThrowsAnyAsync<OperationCanceledException>(
      async () => await notifications.MoveNextAsync());
  }

  [Fact]
  public void Unsuccessful_gatt_status_preserves_operation_and_protocol_error()
  {
    var exception = Assert.Throws<WindowsBleException>(() =>
      WindowsBleStatus.ThrowIfFailed(
        GattCommunicationStatus.ProtocolError,
        0x0005,
        "service discovery"));

    Assert.Equal(GattCommunicationStatus.ProtocolError, exception.Status);
    Assert.Equal((ushort)0x0005, exception.ProtocolError);
    Assert.Contains("service discovery", exception.Message, StringComparison.Ordinal);
  }

  [Fact]
  public async Task Disposed_connection_rejects_discovery_before_hardware_access()
  {
    var transport = new WindowsBleCentralTransport();
    var connection = await transport.ConnectAsync("A1B2C3D4E5F6");
    await connection.DisposeAsync();

    await Assert.ThrowsAsync<ObjectDisposedException>(
      async () => await connection.DiscoverServicesAsync());
  }
}
