using System.Threading.Channels;
using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Infrastructure.Bluetooth;

namespace TreadmillRunner.IntegrationTests;

public sealed class NativeResourceOwnershipTests
{
  [Fact]
  public void TransferFirst_disposes_every_returned_resource_when_validation_fails()
  {
    var resources = new[]
    {
      new TrackedResource(),
      new TrackedResource(),
      new TrackedResource(),
    };

    Assert.Throws<InvalidOperationException>(() =>
      NativeResourceOwnership.TransferFirst(
        resources,
        static () => throw new InvalidOperationException("native failure")));

    Assert.All(resources, resource => Assert.Equal(1, resource.DisposeCount));
  }

  [Fact]
  public void TransferFirst_transfers_only_the_selected_resource_on_success()
  {
    var resources = new[]
    {
      new TrackedResource(),
      new TrackedResource(),
      new TrackedResource(),
    };

    TrackedResource? selected = NativeResourceOwnership.TransferFirst(
      resources,
      static () => { });

    Assert.Same(resources[0], selected);
    Assert.Equal(0, resources[0].DisposeCount);
    Assert.Equal(1, resources[1].DisposeCount);
    Assert.Equal(1, resources[2].DisposeCount);
  }

  [Fact]
  public void Cleanup_attempts_every_action_and_preserves_first_active_exception()
  {
    var firstAttempted = false;
    var secondAttempted = false;
    var expected = new InvalidOperationException("first cleanup failure");

    InvalidOperationException actual = Assert.Throws<InvalidOperationException>(() =>
      NativeResourceOwnership.RunCleanupActions(
        suppressExceptions: false,
        () =>
        {
          firstAttempted = true;
          throw expected;
        },
        () =>
        {
          secondAttempted = true;
          throw new NotSupportedException("second cleanup failure");
        }));

    Assert.True(firstAttempted);
    Assert.True(secondAttempted);
    Assert.Same(expected, actual);
  }

  [Fact]
  public void Cleanup_suppresses_teardown_exceptions_after_attempting_every_action()
  {
    var attempts = 0;

    NativeResourceOwnership.RunCleanupActions(
      suppressExceptions: true,
      () =>
      {
        attempts++;
        throw new ObjectDisposedException("first");
      },
      () =>
      {
        attempts++;
        throw new ObjectDisposedException("second");
      });

    Assert.Equal(2, attempts);
  }

  [Fact]
  public async Task Closed_device_exception_is_routed_to_subscription_channel()
  {
    Channel<BleNotification> channel = Channel.CreateUnbounded<BleNotification>();
    var expected = new ObjectDisposedException("device");

    WindowsBleReadOnlyConnection.CompleteChannelOnDisconnect(
      () => throw expected,
      channel.Writer);

    ObjectDisposedException actual = await Assert.ThrowsAsync<ObjectDisposedException>(
      async () => await channel.Reader.Completion);
    Assert.Same(expected, actual);
  }

  [Fact]
  public async Task Async_owner_serializes_acquisition_and_publishes_one_resource()
  {
    using var owner = new AsyncNativeResourceOwner<TrackedResource>();
    var acquired = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var resource = new TrackedResource();
    var factoryCalls = 0;

    async Task<TrackedResource?> Acquire(CancellationToken cancellationToken)
    {
      Interlocked.Increment(ref factoryCalls);
      acquired.SetResult();
      await release.Task.WaitAsync(cancellationToken);
      return resource;
    }

    Task<TrackedResource> first = owner.GetOrCreateAsync(
      Acquire,
      static () => new InvalidOperationException("unavailable"),
      CancellationToken.None);
    await acquired.Task;
    Task<TrackedResource> second = owner.GetOrCreateAsync(
      Acquire,
      static () => new InvalidOperationException("unavailable"),
      CancellationToken.None);
    release.SetResult();

    TrackedResource[] results = await Task.WhenAll(first, second);

    Assert.Equal(1, factoryCalls);
    Assert.All(results, result => Assert.Same(resource, result));
    Assert.Equal(0, resource.DisposeCount);
  }

  [Fact]
  public async Task Async_owner_disposes_candidate_when_cancellation_wins_before_publication()
  {
    using var owner = new AsyncNativeResourceOwner<TrackedResource>();
    using var cancellation = new CancellationTokenSource();
    var candidate = new TrackedResource();

    async Task<TrackedResource?> Acquire(CancellationToken _)
    {
      await Task.Yield();
      cancellation.Cancel();
      return candidate;
    }

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => owner.GetOrCreateAsync(
      Acquire,
      static () => new InvalidOperationException("unavailable"),
      cancellation.Token));

    Assert.Equal(1, candidate.DisposeCount);
  }

  [Fact]
  public async Task Async_owner_preserves_cancellation_when_factory_returns_no_resource()
  {
    using var owner = new AsyncNativeResourceOwner<TrackedResource>();
    using var cancellation = new CancellationTokenSource();

    Task<TrackedResource?> Acquire(CancellationToken _)
    {
      cancellation.Cancel();
      return Task.FromResult<TrackedResource?>(null);
    }

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => owner.GetOrCreateAsync(
      Acquire,
      static () => new InvalidOperationException("unavailable"),
      cancellation.Token));
  }

  [Fact]
  public async Task Async_owner_disposes_late_candidate_when_disposal_wins_acquisition_race()
  {
    var owner = new AsyncNativeResourceOwner<TrackedResource>();
    var acquired = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var candidate = new TrackedResource();

    async Task<TrackedResource?> Acquire(CancellationToken cancellationToken)
    {
      acquired.SetResult();
      await release.Task.WaitAsync(cancellationToken);
      return candidate;
    }

    Task<TrackedResource> acquisition = owner.GetOrCreateAsync(
      Acquire,
      static () => new InvalidOperationException("unavailable"),
      CancellationToken.None);
    await acquired.Task;

    owner.Dispose();
    release.SetResult();

    await Assert.ThrowsAsync<ObjectDisposedException>(() => acquisition);
    Assert.Equal(1, candidate.DisposeCount);
  }

  [Fact]
  public async Task Async_owner_disposes_published_resource_exactly_once()
  {
    var owner = new AsyncNativeResourceOwner<TrackedResource>();
    var resource = new TrackedResource();
    TrackedResource published = await owner.GetOrCreateAsync(
      _ => Task.FromResult<TrackedResource?>(resource),
      static () => new InvalidOperationException("unavailable"),
      CancellationToken.None);

    owner.Dispose();
    owner.Dispose();

    Assert.Same(resource, published);
    Assert.Equal(1, resource.DisposeCount);
  }

  private sealed class TrackedResource : IDisposable
  {
    public int DisposeCount { get; private set; }

    public void Dispose() => DisposeCount++;
  }
}
