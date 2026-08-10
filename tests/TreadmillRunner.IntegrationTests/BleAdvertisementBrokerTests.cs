using System.Runtime.CompilerServices;
using Microsoft.Extensions.Logging.Abstractions;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Infrastructure.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class BleAdvertisementBrokerTests
{
  [Fact]
  public async Task Concurrent_scans_share_one_source_and_cancel_independently()
  {
    var transport = new CoordinatedScanTransport();
    await using var broker = new BleAdvertisementBroker(
      transport,
      NullLogger<BleAdvertisementBroker>.Instance);
    using var firstCancellation = new CancellationTokenSource();
    using var secondCancellation = new CancellationTokenSource();
    IAsyncEnumerator<BleAdvertisement> first = broker
      .ScanAsync(firstCancellation.Token)
      .GetAsyncEnumerator(firstCancellation.Token);
    IAsyncEnumerator<BleAdvertisement> second = broker
      .ScanAsync(secondCancellation.Token)
      .GetAsyncEnumerator(secondCancellation.Token);

    try
    {
      Task<bool> firstMove = first.MoveNextAsync().AsTask();
      await transport.ScanStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
      Task<bool> secondMove = second.MoveNextAsync().AsTask();
      transport.Release.TrySetResult(true);

      Assert.True(await firstMove.WaitAsync(TimeSpan.FromSeconds(2)));
      Assert.True(await secondMove.WaitAsync(TimeSpan.FromSeconds(2)));
      Assert.Equal(1, transport.ScanCount);
      Assert.Equal("A1B2C3D4E5F6", first.Current.DeviceId);
      Assert.Equal("A1B2C3D4E5F6", second.Current.DeviceId);

      firstCancellation.Cancel();
      await first.DisposeAsync();
      Assert.False(transport.Stop.Task.IsCompleted);

      secondCancellation.Cancel();
      await second.DisposeAsync();
      await transport.Stop.Task.WaitAsync(TimeSpan.FromSeconds(2));
    }
    finally
    {
      await first.DisposeAsync();
      await second.DisposeAsync();
    }
  }

  [Fact]
  public async Task Source_failure_is_delivered_and_a_later_subscription_restarts_the_scan()
  {
    var transport = new FailingThenSuccessfulScanTransport();
    await using var broker = new BleAdvertisementBroker(
      transport,
      NullLogger<BleAdvertisementBroker>.Instance);

    using var failedCancellation = new CancellationTokenSource();
    await using (IAsyncEnumerator<BleAdvertisement> failed = broker
      .ScanAsync(failedCancellation.Token)
      .GetAsyncEnumerator(failedCancellation.Token))
    {
      await Assert.ThrowsAsync<InvalidOperationException>(
        async () => await failed.MoveNextAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(2)));
    }

    using var successfulCancellation = new CancellationTokenSource();
    await using (IAsyncEnumerator<BleAdvertisement> successful = broker
      .ScanAsync(successfulCancellation.Token)
      .GetAsyncEnumerator(successfulCancellation.Token))
    {
      Assert.True(await successful.MoveNextAsync().AsTask().WaitAsync(TimeSpan.FromSeconds(2)));
      Assert.Equal("A1B2C3D4E5F7", successful.Current.DeviceId);
    }

    Assert.Equal(2, transport.ScanCount);
  }

  private sealed class CoordinatedScanTransport : IBleCentralTransport
  {
    public TaskCompletionSource<bool> ScanStarted { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource<bool> Release { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);
    public TaskCompletionSource<bool> Stop { get; } =
      new(TaskCreationOptions.RunContinuationsAsynchronously);
    public int ScanCount { get; private set; }

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      ScanCount++;
      ScanStarted.TrySetResult(true);
      await Release.Task.WaitAsync(cancellationToken);
      yield return new BleAdvertisement(
        "A1B2C3D4E5F6",
        "Shared",
        -40,
        []);
      try
      {
        await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
      }
      finally
      {
        Stop.TrySetResult(true);
      }
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromException<IBleConnection>(new NotSupportedException());
  }

  private sealed class FailingThenSuccessfulScanTransport : IBleCentralTransport
  {
    private int _scanCount;

    public int ScanCount => Volatile.Read(ref _scanCount);

    public async IAsyncEnumerable<BleAdvertisement> ScanAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
      int scan = Interlocked.Increment(ref _scanCount);
      await Task.Yield();
      if (scan == 1)
      {
        throw new InvalidOperationException("The adapter scan failed.");
      }

      cancellationToken.ThrowIfCancellationRequested();
      yield return new BleAdvertisement("A1B2C3D4E5F7", "Restarted", -42, []);
      await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
    }

    public ValueTask<IBleConnection> ConnectAsync(
      string deviceId,
      CancellationToken cancellationToken = default) =>
      ValueTask.FromException<IBleConnection>(new NotSupportedException());
  }
}
