namespace TreadmillRunner.Gateway.Polar;

/// <summary>Serializes every PFTP workflow in this process, including status-before-mutation sequences.</summary>
public sealed class PolarH10OperationGate
{
  private readonly SemaphoreSlim _gate = new(1, 1);

  public async ValueTask<IAsyncDisposable> EnterAsync(CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
    return new Releaser(_gate);
  }

  private sealed class Releaser(SemaphoreSlim gate) : IAsyncDisposable
  {
    private int _released;

    public ValueTask DisposeAsync()
    {
      if (Interlocked.Exchange(ref _released, 1) == 0) gate.Release();
      return ValueTask.CompletedTask;
    }
  }
}

public sealed class PolarH10OperationFilter(PolarH10OperationGate gate) : IEndpointFilter
{
  public async ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
  {
    await using IAsyncDisposable lease = await gate.EnterAsync(context.HttpContext.RequestAborted);
    return await next(context).ConfigureAwait(false);
  }
}
