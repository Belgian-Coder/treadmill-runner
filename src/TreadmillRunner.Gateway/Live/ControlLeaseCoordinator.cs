using TreadmillRunner.Core.Control;

namespace TreadmillRunner.Gateway.Live;

/// <summary>
/// Gateway adapter over the Core control-lease contract. It deliberately does not issue treadmill commands.
/// </summary>
public interface IControlLeaseCoordinator
{
  ControlLease? Current { get; }

  ControlLease? TryAcquire(string holderId);

  ControlLease? Heartbeat(Guid leaseId, string holderId);

  bool Release(Guid leaseId, string holderId);

  void RevokeCurrent();
}

public sealed class ControlLeaseCoordinator(ControlLeaseManager leaseManager) : IControlLeaseCoordinator
{
  public ControlLease? Current => leaseManager.Current;

  public ControlLease? TryAcquire(string holderId) => leaseManager.TryAcquire(holderId);

  public ControlLease? Heartbeat(Guid leaseId, string holderId) => leaseManager.Heartbeat(leaseId, holderId);

  public bool Release(Guid leaseId, string holderId) => leaseManager.Release(leaseId, holderId);

  public void RevokeCurrent() => leaseManager.RevokeCurrent();
}
