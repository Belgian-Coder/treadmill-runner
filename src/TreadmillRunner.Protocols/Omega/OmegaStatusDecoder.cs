namespace TreadmillRunner.Protocols.Omega;

public sealed record OmegaStatus(double SpeedKph, double InclinePercent);

public static class OmegaStatusDecoder
{
  private const double MilesToKilometers = 1.60934;

  public static bool TryDecode(ReadOnlySpan<byte> frame, out OmegaStatus? status)
  {
    // Status fields use byte 30 and the frame terminator occupies the final
    // two bytes, so at least 33 bytes are required before decoding.
    if (frame.Length < 33 ||
        frame[0] != 0x55 ||
        frame[1] != 0xAA ||
        frame[5] != 0x17 ||
        frame[^2] != 0x0D ||
        frame[^1] != 0x0A)
    {
      status = null;
      return false;
    }

    int declaredLength = frame[6] | (frame[7] << 8);
    if (declaredLength > OmegaFrameReassembler.MaximumPayloadLength ||
        declaredLength + 10 != frame.Length)
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
