using System.Buffers.Binary;

namespace TreadmillRunner.Protocols.Ftms;

public sealed record FtmsTreadmillData(
  ushort Flags,
  double? InstantaneousSpeedKph,
  double? InclinationPercent,
  double? RampAngleDegrees,
  double? AverageSpeedKph = null,
  uint? TotalDistanceMeters = null,
  double? PositiveElevationGainMeters = null,
  double? NegativeElevationGainMeters = null,
  ushort? InstantaneousPaceSecondsPer500Meters = null,
  ushort? AveragePaceSecondsPer500Meters = null,
  ushort? TotalEnergyKilocalories = null,
  ushort? EnergyPerHourKilocalories = null,
  byte? EnergyPerMinuteKilocalories = null,
  ushort? HeartRateBpm = null,
  double? MetabolicEquivalent = null,
  TimeSpan? ElapsedTime = null,
  TimeSpan? RemainingTime = null,
  short? ForceNewtons = null,
  short? PowerWatts = null);

/// <summary>
/// Parses the Bluetooth SIG FTMS Treadmill Data characteristic and exposes all
/// standard read-only treadmill fields represented by its flags. Every
/// flag-indicated field is length-checked so later values cannot be decoded at
/// the wrong offset.
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
    double? averageSpeed = null;
    uint? totalDistance = null;
    double? inclination = null;
    double? rampAngle = null;
    double? positiveElevationGain = null;
    double? negativeElevationGain = null;
    ushort? instantaneousPace = null;
    ushort? averagePace = null;
    ushort? totalEnergy = null;
    ushort? energyPerHour = null;
    byte? energyPerMinute = null;
    ushort? heartRate = null;
    double? metabolicEquivalent = null;
    TimeSpan? elapsedTime = null;
    TimeSpan? remainingTime = null;
    short? force = null;
    short? power = null;

    if ((flags & MoreData) == 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawSpeed)) return false;
      speed = rawSpeed / 100d;
    }

    if ((flags & AverageSpeedPresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawAverageSpeed)) return false;
      averageSpeed = rawAverageSpeed / 100d;
    }

    if ((flags & TotalDistancePresent) != 0)
    {
      if (!TryReadUInt24(payload, ref offset, out uint rawTotalDistance)) return false;
      totalDistance = rawTotalDistance;
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

    if ((flags & ElevationGainPresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawPositiveElevationGain) ||
          !TryReadUInt16(payload, ref offset, out ushort rawNegativeElevationGain)) return false;
      positiveElevationGain = rawPositiveElevationGain / 10d;
      negativeElevationGain = rawNegativeElevationGain / 10d;
    }

    if ((flags & InstantaneousPacePresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawInstantaneousPace)) return false;
      instantaneousPace = rawInstantaneousPace;
    }

    if ((flags & AveragePacePresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawAveragePace)) return false;
      averagePace = rawAveragePace;
    }

    if ((flags & ExpendedEnergyPresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawTotalEnergy) ||
          !TryReadUInt16(payload, ref offset, out ushort rawEnergyPerHour) ||
          !TryReadByte(payload, ref offset, out byte rawEnergyPerMinute)) return false;
      totalEnergy = rawTotalEnergy;
      energyPerHour = rawEnergyPerHour;
      energyPerMinute = rawEnergyPerMinute;
    }

    if ((flags & HeartRatePresent) != 0)
    {
      if (!TryReadByte(payload, ref offset, out byte rawHeartRate)) return false;
      heartRate = rawHeartRate;
    }

    if ((flags & MetabolicEquivalentPresent) != 0)
    {
      if (!TryReadByte(payload, ref offset, out byte rawMetabolicEquivalent)) return false;
      metabolicEquivalent = rawMetabolicEquivalent / 10d;
    }

    if ((flags & ElapsedTimePresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawElapsedTime)) return false;
      elapsedTime = TimeSpan.FromSeconds(rawElapsedTime);
    }

    if ((flags & RemainingTimePresent) != 0)
    {
      if (!TryReadUInt16(payload, ref offset, out ushort rawRemainingTime)) return false;
      remainingTime = TimeSpan.FromSeconds(rawRemainingTime);
    }

    if ((flags & ForceAndPowerPresent) != 0)
    {
      if (!TryReadInt16(payload, ref offset, out short rawForce) ||
          !TryReadInt16(payload, ref offset, out short rawPower)) return false;
      force = rawForce;
      power = rawPower;
    }

    if (offset != payload.Length)
    {
      return false;
    }

    data = new FtmsTreadmillData(
      flags,
      speed,
      inclination,
      rampAngle,
      averageSpeed,
      totalDistance,
      positiveElevationGain,
      negativeElevationGain,
      instantaneousPace,
      averagePace,
      totalEnergy,
      energyPerHour,
      energyPerMinute,
      heartRate,
      metabolicEquivalent,
      elapsedTime,
      remainingTime,
      force,
      power);
    return true;
  }

  private static bool TryReadByte(
    ReadOnlySpan<byte> payload,
    ref int offset,
    out byte value)
  {
    if (payload.Length - offset < 1)
    {
      value = 0;
      return false;
    }

    value = payload[offset++];
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

  private static bool TryReadUInt24(
    ReadOnlySpan<byte> payload,
    ref int offset,
    out uint value)
  {
    if (payload.Length - offset < 3)
    {
      value = 0;
      return false;
    }

    value = (uint)(payload[offset] | (payload[offset + 1] << 8) | (payload[offset + 2] << 16));
    offset += 3;
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
