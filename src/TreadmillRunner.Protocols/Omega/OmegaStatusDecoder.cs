namespace TreadmillRunner.Protocols.Omega;

public sealed record OmegaStatus(double SpeedKph, double InclinePercent);

public static class OmegaStatusDecoder
{
  private const double MilesToKilometers = 1.60934;

  public static bool TryDecode(ReadOnlySpan<byte> frame, out OmegaStatus? status)
  {
    if (frame.Length <= 30 || frame[0] != 0x55 || frame[1] != 0xAA || frame[5] != 0x17)
    {
      status = null;
      return false;
    }

    var speedHundredthsMph = frame[24] | (frame[25] << 8);
    status = new OmegaStatus(
        speedHundredthsMph / 100.0 * MilesToKilometers,
        frame[30] / 10.0);
    return true;
  }
}
