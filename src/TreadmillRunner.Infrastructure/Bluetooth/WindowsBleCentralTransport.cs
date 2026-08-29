using System.Globalization;
using System.Runtime.CompilerServices;
using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class WindowsBleCentralTransport : IBleCentralTransport, IBleCommandCentralTransport
{
  private const int AdvertisementBufferCapacity = 256;
  private readonly BluetoothAddressTypeCache _addressTypes;

  public WindowsBleCentralTransport()
    : this(new BluetoothAddressTypeCache())
  {
  }

  internal WindowsBleCentralTransport(BluetoothAddressTypeCache addressTypes)
  {
    _addressTypes = addressTypes ?? throw new ArgumentNullException(nameof(addressTypes));
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
        FullMode = BoundedChannelFullMode.Wait,
      });
    var watcher = new BluetoothLEAdvertisementWatcher
    {
      // Active scanning asks Windows for scan-response metadata such as a
      // watch's local name. Adapter power use and active-scan support remain
      // platform/hardware dependent, so discovery still accepts sparse data.
      ScanningMode = BluetoothLEScanningMode.Active,
    };

    void OnReceived(
      BluetoothLEAdvertisementWatcher sender,
      BluetoothLEAdvertisementReceivedEventArgs eventArgs)
    {
      try
      {
        if (eventArgs.BluetoothAddress == 0) return;

        var advertisement = WindowsBleContractMapper.MapAdvertisement(
          eventArgs.BluetoothAddress,
          eventArgs.Advertisement.LocalName,
          eventArgs.RawSignalStrengthInDBm,
          eventArgs.Advertisement.ServiceUuids);

        // BluetoothAddressType is only available on the received event. Keep
        // it before publishing the advertisement so a concurrent ConnectAsync
        // can select the matching address overload. Zero-address events were
        // discarded above because they are not usable connection locators.
        _addressTypes.Observe(advertisement.DeviceId, eventArgs.BluetoothAddressType);

        if (!channel.Writer.TryWrite(advertisement))
        {
          channel.Writer.TryComplete(new WindowsBleException(
            "Windows BLE advertisement scanning exceeded its bounded buffer."));
        }
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
      if (cancellationToken.IsCancellationRequested)
      {
        channel.Writer.TryComplete();
      }
      else if (eventArgs.Error == BluetoothError.Success)
      {
        // A bounded scan is stopped by cancellation in the iterator's finally
        // block after handlers are detached. Success observed here is therefore
        // an unsolicited early stop and its candidate set is incomplete.
        channel.Writer.TryComplete(new WindowsBleException(
          "Windows BLE advertisement scanning stopped before its bounded interval completed."));
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
    BluetoothAddressType? addressType = _addressTypes.TryGet(deviceId);
    return ValueTask.FromResult<IBleConnection>(
      addressType is { } observedType
        ? new WindowsBleReadOnlyConnection(deviceId, observedType)
        : new WindowsBleReadOnlyConnection(deviceId));
  }

  public ValueTask<IBleCommandConnection> ConnectCommandAsync(
    string deviceId,
    CancellationToken cancellationToken = default)
  {
    cancellationToken.ThrowIfCancellationRequested();
    return ValueTask.FromResult<IBleCommandConnection>(new WindowsBleCommandConnection(deviceId));
  }
}

/// <summary>
/// Short-lived, bounded address-type observations obtained from advertisements.
/// Windows can rotate random private addresses, so this is deliberately not a
/// persistent device identity store.
/// </summary>
internal sealed class BluetoothAddressTypeCache
{
  internal const int DefaultCapacity = 256;
  internal static readonly TimeSpan DefaultTtl = TimeSpan.FromMinutes(5);

  private readonly object _gate = new();
  private readonly int _capacity;
  private readonly TimeSpan _ttl;
  private readonly Dictionary<string, Entry> _entries = new(StringComparer.OrdinalIgnoreCase);
  private readonly LinkedList<string> _order = new();

  internal BluetoothAddressTypeCache(
    int capacity = DefaultCapacity,
    TimeSpan? ttl = null)
  {
    if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity));
    _capacity = capacity;
    _ttl = ttl ?? DefaultTtl;
    if (_ttl <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(ttl));
  }

  internal int Count
  {
    get
    {
      lock (_gate)
      {
        PruneExpired(DateTimeOffset.UtcNow);
        return _entries.Count;
      }
    }
  }

  internal void Observe(
    string deviceId,
    BluetoothAddressType addressType,
    DateTimeOffset? observedAt = null)
  {
    if (!TryNormalizeAddress(deviceId, out string normalized)) return;
    DateTimeOffset timestamp = observedAt ?? DateTimeOffset.UtcNow;
    lock (_gate)
    {
      PruneExpired(timestamp);
      if (_entries.TryGetValue(normalized, out Entry? existing))
      {
        existing.AddressType = addressType;
        existing.ObservedAt = timestamp;
        _order.Remove(existing.Node);
        _order.AddLast(existing.Node);
        return;
      }

      while (_entries.Count >= _capacity && _order.First is not null)
      {
        LinkedListNode<string> oldest = _order.First;
        _order.RemoveFirst();
        _entries.Remove(oldest.Value);
      }

      var node = new LinkedListNode<string>(normalized);
      _order.AddLast(node);
      _entries.Add(normalized, new Entry(addressType, timestamp, node));
    }
  }

  internal BluetoothAddressType? TryGet(
    string deviceId,
    DateTimeOffset? observedAt = null)
  {
    if (!TryNormalizeAddress(deviceId, out string normalized)) return null;
    DateTimeOffset timestamp = observedAt ?? DateTimeOffset.UtcNow;
    lock (_gate)
    {
      PruneExpired(timestamp);
      if (!_entries.TryGetValue(normalized, out Entry? entry)) return null;
      _order.Remove(entry.Node);
      _order.AddLast(entry.Node);
      return WindowsBleAddressTypePolicy.SelectForConnection(entry.AddressType);
    }
  }

  private void PruneExpired(DateTimeOffset now)
  {
    LinkedListNode<string>? node = _order.First;
    while (node is not null)
    {
      LinkedListNode<string>? next = node.Next;
      Entry entry = _entries[node.Value];
      if (now >= entry.ObservedAt + _ttl)
      {
        _order.Remove(node);
        _entries.Remove(node.Value);
      }

      node = next;
    }
  }

  private static bool TryNormalizeAddress(string deviceId, out string normalized)
  {
    normalized = string.Empty;
    if (deviceId is null ||
        deviceId.Length != 12 ||
        !ulong.TryParse(
          deviceId,
          NumberStyles.AllowHexSpecifier,
          CultureInfo.InvariantCulture,
          out ulong address) ||
        address == 0)
    {
      return false;
    }

    normalized = address.ToString("X12", CultureInfo.InvariantCulture);
    return true;
  }

  private sealed class Entry(
    BluetoothAddressType addressType,
    DateTimeOffset observedAt,
    LinkedListNode<string> node)
  {
    public BluetoothAddressType AddressType { get; set; } = addressType;
    public DateTimeOffset ObservedAt { get; set; } = observedAt;
    public LinkedListNode<string> Node { get; } = node;
  }
}

internal static class WindowsBleAddressTypePolicy
{
  internal static BluetoothAddressType? SelectForConnection(BluetoothAddressType? observedType) =>
    observedType is BluetoothAddressType.Public or BluetoothAddressType.Random
      ? observedType
      : null;
}
