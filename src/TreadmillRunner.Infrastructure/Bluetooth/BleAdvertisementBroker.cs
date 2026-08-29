using Microsoft.Extensions.Logging;
using System.Runtime.CompilerServices;
using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;

namespace TreadmillRunner.Infrastructure.Bluetooth;

/// <summary>
/// Fans out one bounded active read-only adapter scan to all current callers. A subscriber owns
/// only its bounded channel; cancelling one subscriber never stops another.
/// </summary>
public sealed class BleAdvertisementBroker(
  IBleCentralTransport transport,
  ILogger<BleAdvertisementBroker> logger) : IBleAdvertisementBroker, IAsyncDisposable
{
  private const int SubscriberBufferCapacity = 256;

  private readonly IBleCentralTransport _transport = transport;
  private readonly object _sync = new();
  private readonly Dictionary<long, Channel<BleAdvertisement>> _subscribers = [];
  private long _nextSubscriberId;
  private long _scanGeneration;
  private Task? _scanTask;
  private CancellationTokenSource? _scanCancellation;
  private bool _scanStopping;
  private bool _disposed;

  public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
    [EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    Subscription? subscription = null;
    try
    {
      subscription = await SubscribeAsync(cancellationToken).ConfigureAwait(false);
      await foreach (BleAdvertisement advertisement in subscription.Channel.Reader
        .ReadAllAsync(cancellationToken)
        .ConfigureAwait(false))
      {
        yield return advertisement;
      }
    }
    finally
    {
      if (subscription is not null)
      {
        await UnsubscribeAsync(subscription).ConfigureAwait(false);
      }
    }
  }

  public async ValueTask DisposeAsync()
  {
    Task? scanTask;
    CancellationTokenSource? scanCancellation;
    lock (_sync)
    {
      if (_disposed) return;
      _disposed = true;
      scanTask = _scanTask;
      scanCancellation = _scanCancellation;
      foreach (Channel<BleAdvertisement> channel in _subscribers.Values)
      {
        channel.Writer.TryComplete();
      }
      _subscribers.Clear();
      scanCancellation?.Cancel();
    }

    if (scanTask is not null)
    {
      try
      {
        await scanTask.ConfigureAwait(false);
      }
      catch (OperationCanceledException) when (scanCancellation?.IsCancellationRequested == true)
      {
      }
    }

    scanCancellation?.Dispose();
  }

  private async Task<Subscription> SubscribeAsync(CancellationToken cancellationToken)
  {
    while (true)
    {
      Task? stoppingScan;
      lock (_sync)
      {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!_scanStopping)
        {
          var channel = Channel.CreateBounded<BleAdvertisement>(
            new BoundedChannelOptions(SubscriberBufferCapacity)
            {
              SingleReader = true,
              SingleWriter = false,
              FullMode = BoundedChannelFullMode.Wait,
            });
          long subscriberId = ++_nextSubscriberId;
          _subscribers.Add(subscriberId, channel);
          EnsureScanStartedLocked();
          return new Subscription(subscriberId, channel);
        }

        stoppingScan = _scanTask;
      }

      if (stoppingScan is null)
      {
        await Task.Yield();
      }
      else
      {
        await stoppingScan.WaitAsync(cancellationToken).ConfigureAwait(false);
      }
      cancellationToken.ThrowIfCancellationRequested();
    }
  }

  private async Task UnsubscribeAsync(Subscription subscription)
  {
    Task? stoppingScan = null;
    lock (_sync)
    {
      if (_subscribers.Remove(subscription.Id, out Channel<BleAdvertisement>? channel))
      {
        channel.Writer.TryComplete();
      }

      if (_subscribers.Count == 0 && !_scanStopping && _scanCancellation is not null)
      {
        _scanStopping = true;
        stoppingScan = _scanTask;
        _scanCancellation.Cancel();
      }
    }

    if (stoppingScan is not null)
    {
      try
      {
        await stoppingScan.ConfigureAwait(false);
      }
      catch (OperationCanceledException)
      {
      }
    }
  }

  private void EnsureScanStartedLocked()
  {
    if (_scanTask is not null || _disposed) return;

    _scanStopping = false;
    _scanCancellation = new CancellationTokenSource();
    long generation = ++_scanGeneration;
    CancellationToken cancellationToken = _scanCancellation.Token;
    _scanTask = RunScanAsync(generation, cancellationToken);
  }

  private async Task RunScanAsync(long generation, CancellationToken cancellationToken)
  {
    // Always publish the task before a synchronous adapter implementation can
    // complete the source and clear the generation state.
    await Task.Yield();
    Exception? completionException = null;
    try
    {
      await foreach (BleAdvertisement advertisement in _transport
        .ScanAsync(cancellationToken)
        .WithCancellation(cancellationToken)
        .ConfigureAwait(false))
      {
        Channel<BleAdvertisement>[] channels;
        lock (_sync)
        {
          channels = _subscribers.Values.ToArray();
        }

        foreach (Channel<BleAdvertisement> channel in channels)
        {
          if (!channel.Writer.TryWrite(advertisement))
          {
            channel.Writer.TryComplete(new InvalidOperationException(
              "The BLE advertisement subscriber exceeded its bounded buffer."));
          }
        }
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
    }
    catch (Exception exception)
    {
      completionException = exception;
      logger.LogWarning(exception, "The shared active read-only BLE advertisement scan failed.");
    }
    finally
    {
      CancellationTokenSource? scanCancellation = null;
      lock (_sync)
      {
        if (_scanGeneration == generation)
        {
          foreach (Channel<BleAdvertisement> channel in _subscribers.Values)
          {
            channel.Writer.TryComplete(completionException);
          }
          _subscribers.Clear();
          scanCancellation = _scanCancellation;
          _scanCancellation = null;
          _scanTask = null;
          _scanStopping = false;
        }
      }

      scanCancellation?.Dispose();
    }
  }

  private sealed record Subscription(long Id, Channel<BleAdvertisement> Channel);
}
