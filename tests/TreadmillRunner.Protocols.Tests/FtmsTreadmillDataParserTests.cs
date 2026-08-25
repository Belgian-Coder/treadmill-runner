using TreadmillRunner.Protocols.Ftms;

namespace TreadmillRunner.Protocols.Tests;

public sealed class FtmsTreadmillDataParserTests
{
  [Fact]
  public void Parses_instantaneous_speed_and_signed_inclination()
  {
    byte[] payload =
    [
      0x08, 0x00,
      0x39, 0x03,
      0xF1, 0xFF,
      0x17, 0x00,
    ];

    Assert.True(FtmsTreadmillDataParser.TryParse(payload, out FtmsTreadmillData? data));
    Assert.NotNull(data);
    Assert.Equal(8.25, data.InstantaneousSpeedKph);
    Assert.Equal(-1.5, data.InclinationPercent);
    Assert.Equal(2.3, data.RampAngleDegrees);
  }

  [Fact]
  public void Skips_optional_fields_before_inclination_without_misaligning()
  {
    byte[] payload =
    [
      0x0E, 0x00,
      0x20, 0x03,
      0x10, 0x03,
      0x34, 0x12, 0x00,
      0x19, 0x00,
      0x00, 0x00,
    ];

    Assert.True(FtmsTreadmillDataParser.TryParse(payload, out FtmsTreadmillData? data));
    Assert.NotNull(data);
    Assert.Equal(8.0, data.InstantaneousSpeedKph);
    Assert.Equal(2.5, data.InclinationPercent);
  }

  [Fact]
  public void More_data_record_can_carry_inclination_without_speed()
  {
    byte[] payload = [0x09, 0x00, 0x0F, 0x00, 0x00, 0x00];

    Assert.True(FtmsTreadmillDataParser.TryParse(payload, out FtmsTreadmillData? data));
    Assert.NotNull(data);
    Assert.Null(data.InstantaneousSpeedKph);
    Assert.Equal(1.5, data.InclinationPercent);
  }

  [Fact]
  public void Parses_all_standard_read_only_treadmill_fields()
  {
    byte[] payload =
    [
      0xFE, 0x1F,
      0x10, 0x0E, // instantaneous speed 36.00 km/h
      0x20, 0x0E, // average speed 36.16 km/h
      0x34, 0x12, 0x00, // total distance 4660 m
      0x0A, 0x00, 0x14, 0x00, // inclination 1%, ramp 2 degrees
      0x64, 0x00, 0x32, 0x00, // positive/negative elevation 10/5 m
      0x78, 0x00, // instantaneous pace 120 s/500 m
      0x90, 0x00, // average pace 144 s/500 m
      0x2C, 0x01, 0x58, 0x02, 0x0A, // energy 300 kcal, 600 kcal/h, 10 kcal/min
      0x90, // heart rate 144 bpm
      0x32, // MET 5.0
      0x2C, 0x01, // elapsed 300 s
      0x3C, 0x00, // remaining 60 s
      0xF4, 0x01, 0x2C, 0x01, // force 500 N, power 300 W
    ];

    Assert.True(FtmsTreadmillDataParser.TryParse(payload, out FtmsTreadmillData? data));
    Assert.NotNull(data);
    Assert.Equal(36, data.InstantaneousSpeedKph);
    Assert.Equal(36.16, data.AverageSpeedKph);
    Assert.Equal((uint)4660, data.TotalDistanceMeters);
    Assert.Equal(10, data.PositiveElevationGainMeters);
    Assert.Equal(5, data.NegativeElevationGainMeters);
    Assert.Equal((ushort)120, data.InstantaneousPaceSecondsPer500Meters);
    Assert.Equal((ushort)144, data.AveragePaceSecondsPer500Meters);
    Assert.Equal((ushort)300, data.TotalEnergyKilocalories);
    Assert.Equal((ushort)600, data.EnergyPerHourKilocalories);
    Assert.Equal((byte)10, data.EnergyPerMinuteKilocalories);
    Assert.Equal((ushort)144, data.HeartRateBpm);
    Assert.Equal(5, data.MetabolicEquivalent);
    Assert.Equal(TimeSpan.FromSeconds(300), data.ElapsedTime);
    Assert.Equal(TimeSpan.FromSeconds(60), data.RemainingTime);
    Assert.Equal((short)500, data.ForceNewtons);
    Assert.Equal((short)300, data.PowerWatts);
  }

  [Theory]
  [InlineData("080039")]
  [InlineData("00200000")]
  [InlineData("0000390300")]
  public void Rejects_truncated_oversized_and_reserved_flag_payloads(string hex)
  {
    byte[] payload = Convert.FromHexString(hex);
    Assert.False(FtmsTreadmillDataParser.TryParse(payload, out FtmsTreadmillData? data));
    Assert.Null(data);
  }
}
