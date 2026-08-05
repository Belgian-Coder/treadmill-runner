namespace TreadmillRunner.Protocols.HeartRate;

public static class BatteryLevelParser
{
  public static bool TryParse(ReadOnlySpan<byte> payload, out byte percent)
  {
    if (payload.Length != 1 || payload[0] > 100)
    {
      percent = 0;
      return false;
    }

    percent = payload[0];
    return true;
  }
}
