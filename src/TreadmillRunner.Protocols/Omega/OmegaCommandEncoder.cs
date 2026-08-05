namespace TreadmillRunner.Protocols.Omega;

public static class OmegaCommandEncoder
{
  private static readonly byte[] PauseFrame =
      [0x55, 0xAA, 0x00, 0x00, 0x03, 0x03, 0x00, 0x00, 0x00, 0x00, 0x0D, 0x0A];

  private static readonly byte[] StopFrame =
      [0x55, 0xAA, 0x00, 0x00, 0x02, 0x14, 0x00, 0x00, 0x00, 0x00, 0x0D, 0x0A];

  public static byte[] EncodeSpeed(double speedKph)
  {
    var scaledSpeed = Scale(speedKph, ushort.MaxValue, nameof(speedKph));
    Span<byte> payload = [(byte)scaledSpeed, (byte)(scaledSpeed >> 8), 0x01];
    return EncodePayloadCommand(0x05, payload);
  }

  public static byte[] EncodeIncline(double inclinePercent)
  {
    var scaledIncline = Scale(inclinePercent, byte.MaxValue, nameof(inclinePercent));
    Span<byte> payload = [(byte)scaledIncline, 0x00];
    return EncodePayloadCommand(0x06, payload);
  }

  public static byte[] EncodePause() => PauseFrame.ToArray();

  public static byte[] EncodeStop() => StopFrame.ToArray();

  private static byte[] EncodePayloadCommand(byte command, ReadOnlySpan<byte> payload)
  {
    var frame = new byte[payload.Length + 12];
    frame[0] = 0x55;
    frame[1] = 0xAA;
    frame[4] = 0x03;
    frame[5] = command;
    frame[6] = (byte)payload.Length;
    frame[7] = (byte)(payload.Length >> 8);

    var crc = CrcCcitt.Compute(payload);
    // Omega carries the standard CRC-CCITT value least-significant byte first.
    frame[8] = (byte)crc;
    frame[9] = (byte)(crc >> 8);
    payload.CopyTo(frame.AsSpan(10));
    frame[^2] = 0x0D;
    frame[^1] = 0x0A;
    return frame;
  }

  private static ushort Scale(double value, ushort maximum, string parameterName)
  {
    if (!double.IsFinite(value) || value < 0 || value * 10 > maximum)
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }

    return (ushort)Math.Round(value * 10, MidpointRounding.AwayFromZero);
  }
}
