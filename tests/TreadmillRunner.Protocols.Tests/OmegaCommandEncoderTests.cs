using TreadmillRunner.Protocols.Omega;

namespace TreadmillRunner.Protocols.Tests;

public sealed class OmegaCommandEncoderTests
{
  [Fact]
  public void Encodes_speed_10_kph_golden_frame()
  {
    var frame = OmegaCommandEncoder.EncodeSpeed(10.0);

    Assert.Equal(
        Convert.FromHexString("55AA000003050300169B6400010D0A"),
        frame);
  }

  [Fact]
  public void Encodes_incline_5_percent_golden_frame()
  {
    var frame = OmegaCommandEncoder.EncodeIncline(5.0);

    Assert.Equal(
        Convert.FromHexString("55AA000003060200F87E32000D0A"),
        frame);
  }

  [Fact]
  public void Encodes_pause_and_stop_golden_frames()
  {
    Assert.Equal(
        Convert.FromHexString("55AA00000303000000000D0A"),
        OmegaCommandEncoder.EncodePause());
    Assert.Equal(
        Convert.FromHexString("55AA00000214000000000D0A"),
        OmegaCommandEncoder.EncodeStop());
  }

  [Fact]
  public void Does_not_expose_remote_start_encoder()
  {
    Assert.DoesNotContain(
        typeof(OmegaCommandEncoder).GetMethods(),
        method => method.Name.Contains("Start", StringComparison.OrdinalIgnoreCase) ||
                  method.Name.Contains("Resume", StringComparison.OrdinalIgnoreCase));
  }

  [Theory]
  [InlineData(double.NaN)]
  [InlineData(double.PositiveInfinity)]
  [InlineData(-0.1)]
  [InlineData(6553.6)]
  public void Rejects_unencodable_speed(double speedKph)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => OmegaCommandEncoder.EncodeSpeed(speedKph));
  }
}
