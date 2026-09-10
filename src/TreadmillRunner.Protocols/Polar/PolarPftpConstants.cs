namespace TreadmillRunner.Protocols.Polar;

public static class PolarPftpConstants
{
  public static readonly Guid ServiceUuid = Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb");
  public static readonly Guid MtuCharacteristicUuid = Guid.Parse("fb005c51-02e7-f387-1cad-8acd2d8df0c8");
  public static readonly Guid DeviceToHostCharacteristicUuid = Guid.Parse("fb005c52-02e7-f387-1cad-8acd2d8df0c8");
  public static readonly Guid HostToDeviceCharacteristicUuid = Guid.Parse("fb005c53-02e7-f387-1cad-8acd2d8df0c8");

  public const int MinimumFrameSize = 1;
  // Frame bytes available to a characteristic value. ATT overhead is removed
  // from GattSession.MaxPduSize by the Windows transport.
  public const int DefaultFrameSize = 20;
  public const int MaximumFrameSize = 509;
}
