using System.Buffers.Binary;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Protocols.Ftms;

public readonly record struct FtmsReportedFeatures(
  uint MachineFeatures,
  uint TargetSettingFeatures)
{
  private const uint SpeedTargetSettingSupported = 1u << 0;
  private const uint InclinationTargetSettingSupported = 1u << 1;

  public bool SupportsSpeedTarget =>
    (TargetSettingFeatures & SpeedTargetSettingSupported) != 0;

  public bool SupportsInclinationTarget =>
    (TargetSettingFeatures & InclinationTargetSettingSupported) != 0;

  public TreadmillCapabilities ToUnverifiedCapabilities(
    bool hasControlPoint,
    TreadmillOperatingRange? speedRange = null,
    TreadmillOperatingRange? inclineRange = null) => new(
      ReportsSpeedTargetSupport: SupportsSpeedTarget,
      ReportsInclineTargetSupport: SupportsInclinationTarget,
      ReportsStandardStartResume: hasControlPoint,
      SpeedRange: speedRange,
      InclineRange: inclineRange);
}

public static class FtmsCapabilityParser
{
  private const int FeatureLength = 8;
  private const int RangeLength = 6;

  public static bool TryParseFeatures(
    ReadOnlySpan<byte> payload,
    out FtmsReportedFeatures features)
  {
    if (payload.Length != FeatureLength)
    {
      features = default;
      return false;
    }

    features = new FtmsReportedFeatures(
      BinaryPrimitives.ReadUInt32LittleEndian(payload[..4]),
      BinaryPrimitives.ReadUInt32LittleEndian(payload[4..]));
    return true;
  }

  public static bool TryParseSupportedSpeedRange(
    ReadOnlySpan<byte> payload,
    out TreadmillOperatingRange range)
  {
    if (payload.Length != RangeLength)
    {
      range = null!;
      return false;
    }

    var minimum = BinaryPrimitives.ReadUInt16LittleEndian(payload[..2]) / 100m;
    var maximum = BinaryPrimitives.ReadUInt16LittleEndian(payload[2..4]) / 100m;
    var increment = BinaryPrimitives.ReadUInt16LittleEndian(payload[4..]) / 100m;

    return TryCreateRange(minimum, maximum, increment, out range);
  }

  public static bool TryParseSupportedInclinationRange(
    ReadOnlySpan<byte> payload,
    out TreadmillOperatingRange range)
  {
    if (payload.Length != RangeLength)
    {
      range = null!;
      return false;
    }

    var minimum = BinaryPrimitives.ReadInt16LittleEndian(payload[..2]) / 10m;
    var maximum = BinaryPrimitives.ReadInt16LittleEndian(payload[2..4]) / 10m;
    var increment = BinaryPrimitives.ReadUInt16LittleEndian(payload[4..]) / 10m;

    return TryCreateRange(minimum, maximum, increment, out range);
  }

  private static bool TryCreateRange(
    decimal minimum,
    decimal maximum,
    decimal increment,
    out TreadmillOperatingRange range)
  {
    try
    {
      range = new TreadmillOperatingRange(
        minimum,
        maximum,
        increment,
        TreadmillCapabilityEvidence.ProtocolReported);
      return true;
    }
    catch (ArgumentOutOfRangeException)
    {
      range = null!;
      return false;
    }
  }
}
