namespace TreadmillRunner.Protocols.Omega;

public static class CrcCcitt
{
  private const ushort Polynomial = 0x1021;
  private const ushort InitialValue = 0xFFFF;

  public static ushort Compute(ReadOnlySpan<byte> data)
  {
    var crc = InitialValue;

    foreach (var value in data)
    {
      crc ^= (ushort)(value << 8);
      for (var bit = 0; bit < 8; bit++)
      {
        crc = (ushort)((crc & 0x8000) != 0
            ? (crc << 1) ^ Polynomial
            : crc << 1);
      }
    }

    return crc;
  }
}
