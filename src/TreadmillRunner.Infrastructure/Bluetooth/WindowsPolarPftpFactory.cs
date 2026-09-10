using TreadmillRunner.Protocols.Polar;
using Windows.Devices.Bluetooth;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public interface IPolarPftpConnectionFactory
{
  ValueTask<IPolarPftpConnection> ConnectAsync(
    string deviceId,
    BluetoothAddressType? addressType = null,
    CancellationToken cancellationToken = default);
}

public sealed class WindowsPolarPftpConnectionFactory(WindowsBleCentralTransport transport) : IPolarPftpConnectionFactory
{
  public ValueTask<IPolarPftpConnection> ConnectAsync(
    string deviceId,
    BluetoothAddressType? addressType = null,
    CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    return ValueTask.FromResult<IPolarPftpConnection>(new WindowsPolarPftpConnection(
      deviceId,
      addressType ?? transport.ResolveAddressType(deviceId)));
  }
}
