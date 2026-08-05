using System.Buffers.Binary;

namespace TreadmillRunner.Protocols.Ftms;

public sealed record FtmsTreadmillData(
  ushort Flags,
  double? InstantaneousSpeedKph,
  double? InclinationPercent,
  double? RampAngleDegrees);

/// <summary>
/// Parses the Bluetooth SIG FTMS Treadmill Data characteristic. Only fields
/// consumed by TreadmillRunner are exposed, but every flag-indicated field is
/// length-checked so later values cannot be decoded at the wrong offset.
/// </summary>
public static class FtmsTreadmillDataParser
{
  private const ushort MoreData = 1 << 0;
  private const ushort AverageSpeedPresent = 1 << 1;
  private const ushort TotalDistancePresent = 1 << 2;
  private const ushort InclinationPresent = 1 << 3;
  private const ushort ElevationGainPresent = 1 << 4;
  private const ushort InstantaneousPacePresent = 1 << 5;
  private const ushort AveragePacePresent = 1 << 6;
  private const ushort ExpendedEnergyPresent = 1 << 7;
  private const ushort HeartRatePresent = 1 << 8;
  private const ushort MetabolicEquivalentPresent = 1 << 9;
  private const ushort ElapsedTimePresent = 1 << 10;
  private const ushort RemainingTimePresent = 1 << 11;
  private const ushort ForceAndPowerPresent = 1 << 12;
  private const ushort ReservedFlags = 0b1110_0000_0000_0000;

  public static bool TryParse(
    ReadOnlySpan<byte> payload,
    out FtmsTreadmillData? data)
  {
    data = null;
    if (payload.Length < 2)
    {
      return false;
    }

    ushort flags = BinaryPrimitives.ReadUInt16LittleEndian(payload);
    if ((flags & ReservedFlags) != 0)
    {
      return false;
    }

    var offset = 2;
    double? speed = null;
    double? inclination = null;
    double? rampAngle = null;

    if ((flags & MoreData) == 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawSpeed)) return false;
      speed = rawSpeed / 100d;
    }

    if (!TrySkip(payload, ref offset, flags, AverageSpeedPresent, 2) ||
        !TrySkip(payload, ref offset, flags, TotalDistancePresent, 3))
    {
      return false;
    }

    if ((flags & InclinationPresent) != 0)
    {
      if (!TryReadInt16(payload, ref offset, out short rawInclination) ||
          !TryReadInt16(payload, ref offset, out short rawRampAngle))
      {
        return false;
      }

      inclination = rawInclination / 10d;
      rampAngle = rawRampAngle / 10d;
    }

    if (!TrySkip(payload, ref offset, flags, ElevationGainPresent, 4) ||
        !TrySkip(payload, ref offset, flags, InstantaneousPacePresent, 1) ||
        !TrySkip(payload, ref offset, flags, AveragePacePresent, 1) ||
        !TrySkip(payload, ref offset, flags, ExpendedEnergyPresent, 5) ||
        !TrySkip(payload, ref offset, flags, HeartRatePresent, 1) ||
        !TrySkip(payload, ref offset, flags, MetabolicEquivalentPresent, 1) ||
        !TrySkip(payload, ref offset, flags, ElapsedTimePresent, 2) ||
        !TrySkip(payload, ref offset, flags, RemainingTimePresent, 2) ||
        !TrySkip(payload, ref offset, flags, ForceAndPowerPresent, 4) ||
        offset != payload.Length)
    {
      return false;
    }

    data = new FtmsTreadmillData(flags, speed, inclination, rampAngle);
    return true;
  }

  private static bool TrySkip(
    ReadOnlySpan<byte> payload,
    ref int offset,
    ushort flags,
    ushort flag,
    int length)
  {
    if ((flags & flag) == 0)
    {
      return true;
    }

    if (payload.Length - offset < length)
    {
      return false;
    }

    offset += length;
    return true;
  }

  private static bool TryReadUInt16(
    ReadOnlySpan<byte> payload,
    ref int offset,
    out ushort value)
  {
    if (payload.Length - offset < 2)
    {
      value = 0;
      return false;
    }

    value = BinaryPrimitives.ReadUInt16LittleEndian(payload[offset..]);
    offset += 2;
    return true;
  }

  private static bool TryReadInt16(
    ReadOnlySpan<byte> payload,
    ref int offset,
    out short value)
  {
    if (payload.Length - offset < 2)
    {
      value = 0;
      return false;
    }

    value = BinaryPrimitives.ReadInt16LittleEndian(payload[offset..]);
    offset += 2;
    return true;
  }
}
