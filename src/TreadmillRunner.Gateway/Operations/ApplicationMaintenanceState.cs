namespace TreadmillRunner.Gateway.Operations;

public interface IApplicationMaintenanceState
{
  bool IsActive { get; }
  bool TryBegin();
  void End();
  bool TryBeginMutation();
  void EndMutation();
}

public sealed class ApplicationMaintenanceState : IApplicationMaintenanceState
{
  private readonly object _sync = new();
  private bool _active;
  private int _mutations;

  public bool IsActive
  {
    get { lock (_sync) return _active; }
  }

  public bool TryBegin()
  {
    lock (_sync)
    {
      if (_active || _mutations != 0) return false;
      _active = true;
      return true;
    }
  }

  public void End()
  {
    lock (_sync) _active = false;
  }

  public bool TryBeginMutation()
  {
    lock (_sync)
    {
      if (_active) return false;
      _mutations++;
      return true;
    }
  }

  public void EndMutation()
  {
    lock (_sync)
    {
      if (_mutations <= 0) throw new InvalidOperationException("No application mutation is active.");
      _mutations--;
    }
  }
}
