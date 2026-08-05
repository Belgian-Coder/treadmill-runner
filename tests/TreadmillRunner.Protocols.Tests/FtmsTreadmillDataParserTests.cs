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
