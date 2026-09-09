namespace TreadmillRunner.Gateway.Operations;

public interface IApplicationMaintenanceState
{
  bool IsActive { get; }
  bool TryBegin();
  void End();
  bool TryBeginMutation();
  void EndMutation();
}

public sealed class ApplicationMaintenanceState(IConfiguration? configuration = null) : IApplicationMaintenanceState
{
  private readonly object _sync = new();
  private readonly string? _persistentMarkerPath = ResolvePersistentMarkerPath(configuration);
  private readonly string? _pendingActivationPath = ResolvePendingActivationPath(configuration);
  private bool _active;
  private int _mutations;

  public bool IsActive
  {
    get { lock (_sync) return _active || HasPersistentMaintenance(); }
  }

  public bool TryBegin()
  {
    lock (_sync)
    {
      if (_active || _mutations != 0 || HasPersistentMaintenance()) return false;
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
      if (_active || HasPersistentMaintenance()) return false;
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

  private bool HasPersistentMaintenance() =>
    (_persistentMarkerPath is not null && File.Exists(_persistentMarkerPath)) ||
    (_pendingActivationPath is not null && File.Exists(_pendingActivationPath));

  private static string? ResolvePersistentMarkerPath(IConfiguration? configuration)
  {
    string? dataRoot = configuration?["Updates:DataRoot"];
    return string.IsNullOrWhiteSpace(dataRoot)
      ? null
      : Path.Combine(Path.GetFullPath(dataRoot), "updates", "service-maintenance.lock");
  }

  private static string? ResolvePendingActivationPath(IConfiguration? configuration)
  {
    string? planRoot = configuration?["Updates:PlanRoot"];
    if (!string.IsNullOrWhiteSpace(planRoot))
      return Path.Combine(Path.GetFullPath(planRoot), "pending-activation.json");
    string? dataRoot = configuration?["Updates:DataRoot"];
    return string.IsNullOrWhiteSpace(dataRoot)
      ? null
      : Path.Combine(Path.GetFullPath(dataRoot), "updates", "plans", "pending-activation.json");
  }
}
