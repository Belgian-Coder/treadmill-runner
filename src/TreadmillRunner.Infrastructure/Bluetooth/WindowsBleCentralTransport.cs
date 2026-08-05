using System.Runtime.CompilerServices;
using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class WindowsBleCentralTransport : IBleCentralTransport, IBleCommandCentralTransport
{
  private const int AdvertisementBufferCapacity = 256;

  public WindowsBleCentralTransport()
  {
  }

  public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
    [EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();

    var channel = Channel.CreateBounded<BleAdvertisement>(
      new BoundedChannelOptions(AdvertisementBufferCapacity)
      {
        SingleReader = true,
        SingleWriter = false,
        FullMode = BoundedChannelFullMode.DropOldest,
      });
    var watcher = new BluetoothLEAdvertisementWatcher
    {
      ScanningMode = BluetoothLEScanningMode.Passive,
    };

    void OnReceived(
      BluetoothLEAdvertisementWatcher sender,
      BluetoothLEAdvertisementReceivedEventArgs eventArgs)
    {
      try
      {
        var advertisement = WindowsBleContractMapper.MapAdvertisement(
          eventArgs.BluetoothAddress,
          eventArgs.Advertisement.LocalName,
          eventArgs.RawSignalStrengthInDBm,
          eventArgs.Advertisement.ServiceUuids);
        channel.Writer.TryWrite(advertisement);
      }
      catch (Exception exception)
      {
        channel.Writer.TryComplete(exception);
      }
    }

    void OnStopped(
      BluetoothLEAdvertisementWatcher sender,
      BluetoothLEAdvertisementWatcherStoppedEventArgs eventArgs)
    {
      if (eventArgs.Error == BluetoothError.Success || cancellationToken.IsCancellationRequested)
      {
        channel.Writer.TryComplete();
      }
      else
      {
        channel.Writer.TryComplete(
          new WindowsBleException($"Windows BLE advertisement scanning stopped with {eventArgs.Error}."));
      }
    }

    watcher.Received += OnReceived;
    watcher.Stopped += OnStopped;

    try
    {
      watcher.Start();

      await foreach (var advertisement in channel.Reader
        .ReadAllAsync(cancellationToken)
        .ConfigureAwait(false))
      {
        yield return advertisement;
      }
    }
    finally
    {
      watcher.Received -= OnReceived;
      watcher.Stopped -= OnStopped;
      watcher.Stop();
      channel.Writer.TryComplete();
    }
  }

  public ValueTask<IBleConnection> ConnectAsync(
    string deviceId,
    CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    return ValueTask.FromResult<IBleConnection>(new WindowsBleReadOnlyConnection(deviceId));
  }

  public ValueTask<IBleCommandConnection> ConnectCommandAsync(
    string deviceId,
    CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    return ValueTask.FromResult<IBleCommandConnection>(new WindowsBleCommandConnection(deviceId));
  }
}
