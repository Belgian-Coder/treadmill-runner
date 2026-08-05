namespace TreadmillRunner.Protocols.HeartRate;

public enum HeartRateContactStatus
{
  NotSupported,
  NotDetected,
  Detected,
}

public sealed record HeartRateMeasurement(
    ushort BeatsPerMinute,
    HeartRateContactStatus ContactStatus,
    ushort? EnergyExpendedKilojoules,
    IReadOnlyList<TimeSpan> RrIntervals);

public static class HeartRateMeasurementParser
{
  private const byte UInt16HeartRateFlag = 1 << 0;
  private const byte ContactDetectedFlag = 1 << 1;
  private const byte ContactSupportedFlag = 1 << 2;
  private const byte EnergyExpendedFlag = 1 << 3;
  private const byte RrIntervalFlag = 1 << 4;

  public static HeartRateMeasurement Parse(ReadOnlySpan<byte> value)
  {
    if (value.IsEmpty)
    {
      throw new FormatException("Heart Rate Measurement has no flags byte.");
    }

    var flags = value[0];
    var offset = 1;
    ushort beatsPerMinute;

    if ((flags & UInt16HeartRateFlag) != 0)
    {
      EnsureAvailable(value, offset, 2);
      beatsPerMinute = ReadUInt16(value, offset);
      offset += 2;
    }
    else
    {
      EnsureAvailable(value, offset, 1);
      beatsPerMinute = value[offset++];
    }

    ushort? energyExpended = null;
    if ((flags & EnergyExpendedFlag) != 0)
    {
      EnsureAvailable(value, offset, 2);
      energyExpended = ReadUInt16(value, offset);
      offset += 2;
    }

    var rrIntervals = new List<TimeSpan>();
    if ((flags & RrIntervalFlag) != 0)
    {
      if ((value.Length - offset) % 2 != 0 || value.Length == offset)
      {
        throw new FormatException("Heart Rate Measurement contains a truncated RR interval.");
      }

      while (offset < value.Length)
      {
        var unitsOfOneOver1024Second = ReadUInt16(value, offset);
        rrIntervals.Add(TimeSpan.FromSeconds(unitsOfOneOver1024Second / 1024.0));
        offset += 2;
      }
    }
    else if (offset != value.Length)
    {
      throw new FormatException("Heart Rate Measurement contains bytes not declared by its flags.");
    }

    return new HeartRateMeasurement(
        beatsPerMinute,
        GetContactStatus(flags),
        energyExpended,
        rrIntervals.AsReadOnly());
  }

  private static HeartRateContactStatus GetContactStatus(byte flags)
  {
    if ((flags & ContactSupportedFlag) == 0)
    {
      return HeartRateContactStatus.NotSupported;
    }

    return (flags & ContactDetectedFlag) != 0
        ? HeartRateContactStatus.Detected
        : HeartRateContactStatus.NotDetected;
  }

  private static ushort ReadUInt16(ReadOnlySpan<byte> value, int offset) =>
      (ushort)(value[offset] | (value[offset + 1] << 8));

  private static void EnsureAvailable(ReadOnlySpan<byte> value, int offset, int length)
  {
    if (value.Length - offset < length)
    {
      throw new FormatException("Heart Rate Measurement is truncated.");
    }
  }
}
