using System.Runtime.ExceptionServices;

namespace TreadmillRunner.Infrastructure.Bluetooth;

internal static class NativeResourceOwnership
{
  public static T? TransferFirst<T>(
    IReadOnlyList<T> resources,
    Action validate)
    where T : class, IDisposable
  {
    ArgumentNullException.ThrowIfNull(resources);
    ArgumentNullException.ThrowIfNull(validate);

    try
    {
      validate();
    }
    catch
    {
      DisposeAll(resources);
      throw;
    }

    if (resources.Count == 0) return null;

    T selected = resources[0];
    try
    {
      for (var index = 1; index < resources.Count; index++)
      {
        resources[index].Dispose();
      }

      return selected;
    }
    catch
    {
      selected.Dispose();
      throw;
    }
  }

  public static void RunCleanupActions(
    bool suppressExceptions,
    params Action[] actions)
  {
    ArgumentNullException.ThrowIfNull(actions);
    Exception? firstException = null;

    foreach (Action action in actions)
    {
      try
      {
        action();
      }
      catch (Exception exception)
      {
        firstException ??= exception;
      }
    }

    if (!suppressExceptions && firstException is not null)
    {
      ExceptionDispatchInfo.Capture(firstException).Throw();
    }
  }

  private static void DisposeAll<T>(IReadOnlyList<T> resources)
    where T : IDisposable
  {
    foreach (T resource in resources)
    {
      resource.Dispose();
    }
  }
}

internal sealed class AsyncNativeResourceOwner<T> : IDisposable
  where T : class, IDisposable
{
  private readonly SemaphoreSlim _acquisitionGate = new(1, 1);
  private readonly object _sync = new();
  private T? _resource;
  private bool _disposed;

  public async Task<T> GetOrCreateAsync(
    Func<CancellationToken, Task<T?>> factory,
    Func<Exception> unavailableExceptionFactory,
    CancellationToken cancellationToken)
  {
    ArgumentNullException.ThrowIfNull(factory);
    ArgumentNullException.ThrowIfNull(unavailableExceptionFactory);

    T? existing = GetPublishedResource(cancellationToken);
    if (existing is not null) return existing;

    await _acquisitionGate.WaitAsync(cancellationToken).ConfigureAwait(false);
    try
    {
      existing = GetPublishedResource(cancellationToken);
      if (existing is not null) return existing;

      T? candidate = await factory(cancellationToken).ConfigureAwait(false);
      try
      {
        cancellationToken.ThrowIfCancellationRequested();
        if (candidate is null) throw unavailableExceptionFactory();

        lock (_sync)
        {
          cancellationToken.ThrowIfCancellationRequested();
          ObjectDisposedException.ThrowIf(_disposed, this);
          T published = candidate;
          _resource = published;
          candidate = null;
          return published;
        }
      }
      finally
      {
        candidate?.Dispose();
      }
    }
    finally
    {
      _acquisitionGate.Release();
    }
  }

  public void Dispose()
  {
    T? resource;
    lock (_sync)
    {
      if (_disposed) return;

      _disposed = true;
      resource = _resource;
      _resource = null;
    }

    resource?.Dispose();
  }

  private T? GetPublishedResource(CancellationToken cancellationToken)
  {
    lock (_sync)
    {
      cancellationToken.ThrowIfCancellationRequested();
      ObjectDisposedException.ThrowIf(_disposed, this);
      return _resource;
    }
  }
}
