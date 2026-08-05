using TreadmillRunner.Core.Devices;
using TreadmillRunner.Protocols.Ftms;

namespace TreadmillRunner.Protocols.Tests;

public sealed class FtmsCapabilityParserTests
{
  [Fact]
  public void Parses_target_setting_feature_bits_without_promoting_them_to_verified_control()
  {
    byte[] payload = [
      0x00, 0x00, 0x00, 0x00,
      0x03, 0x00, 0x00, 0x00,
    ];

    var parsed = FtmsCapabilityParser.TryParseFeatures(payload, out var features);

    Assert.True(parsed);
    Assert.True(features.SupportsSpeedTarget);
    Assert.True(features.SupportsInclinationTarget);
    Assert.Equal(0x00000003u, features.TargetSettingFeatures);

    var capabilities = features.ToUnverifiedCapabilities(hasControlPoint: true);
    Assert.True(capabilities.ReportsSpeedTargetSupport);
    Assert.True(capabilities.ReportsInclineTargetSupport);
    Assert.True(capabilities.ReportsStandardStartResume);
    Assert.False(capabilities.CanSetSpeedRemotely);
    Assert.False(capabilities.CanSetInclineRemotely);
    Assert.False(capabilities.CanStartRemotely);
  }

  [Theory]
  [InlineData(0)]
  [InlineData(7)]
  [InlineData(9)]
  public void Rejects_feature_payloads_that_are_not_exactly_eight_octets(int length)
  {
    Assert.False(FtmsCapabilityParser.TryParseFeatures(new byte[length], out _));
  }

  [Fact]
  public void Parses_supported_speed_range_in_kilometres_per_hour()
  {
    byte[] payload = [0x32, 0x00, 0x08, 0x07, 0x0A, 0x00];

    var parsed = FtmsCapabilityParser.TryParseSupportedSpeedRange(payload, out var range);

    Assert.True(parsed);
    Assert.Equal(0.5m, range.Minimum);
    Assert.Equal(18m, range.Maximum);
    Assert.Equal(0.1m, range.Increment);
    Assert.Equal(TreadmillCapabilityEvidence.ProtocolReported, range.Evidence);
  }

  [Fact]
  public void Parses_signed_supported_inclination_range_in_percent()
  {
    byte[] payload = [0xE2, 0xFF, 0x96, 0x00, 0x05, 0x00];

    var parsed = FtmsCapabilityParser.TryParseSupportedInclinationRange(payload, out var range);

    Assert.True(parsed);
    Assert.Equal(-3m, range.Minimum);
    Assert.Equal(15m, range.Maximum);
    Assert.Equal(0.5m, range.Increment);
    Assert.Equal(TreadmillCapabilityEvidence.ProtocolReported, range.Evidence);
  }

  [Theory]
  [InlineData(5)]
  [InlineData(7)]
  public void Rejects_range_payloads_that_are_not_exactly_six_octets(int length)
  {
    Assert.False(FtmsCapabilityParser.TryParseSupportedSpeedRange(new byte[length], out _));
    Assert.False(FtmsCapabilityParser.TryParseSupportedInclinationRange(new byte[length], out _));
  }

  [Fact]
  public void Rejects_inverted_or_zero_increment_ranges()
  {
    byte[] invertedSpeed = [0xD0, 0x07, 0xE8, 0x03, 0x0A, 0x00];
    byte[] zeroInclineIncrement = [0x00, 0x00, 0x64, 0x00, 0x00, 0x00];

    Assert.False(FtmsCapabilityParser.TryParseSupportedSpeedRange(invertedSpeed, out _));
    Assert.False(FtmsCapabilityParser.TryParseSupportedInclinationRange(zeroInclineIncrement, out _));
  }
}
