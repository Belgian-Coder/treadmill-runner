namespace TreadmillRunner.Infrastructure;

/// <summary>
/// Marks the Windows-only composition boundary. Real Bluetooth and persistence
/// adapters are introduced by later stories after their acceptance gates.
/// </summary>
public static class WindowsInfrastructure
{
  public const string BluetoothProvider = "windows";
}
