using TreadmillRunner.Protocols.Omega;

namespace TreadmillRunner.Protocols.Tests;

public sealed class OmegaStatusDecoderTests
{
  [Fact]
  public void Decodes_vendor_status_speed_and_incline()
  {
    var frame = new byte[40];
    frame[0] = 0x55;
    frame[1] = 0xAA;
    frame[5] = 0x17;
    frame[6] = 30;
    frame[^2] = 0x0D;
    frame[^1] = 0x0A;
    frame[24] = 0x6D;
    frame[25] = 0x02; // 6.21 mph
    frame[30] = 53;   // 5.3%

    var decoded = OmegaStatusDecoder.TryDecode(frame, out var status);

    Assert.True(decoded);
    Assert.InRange(status!.SpeedKph, 9.993, 9.997);
    Assert.Equal(5.3, status.InclinePercent);
  }

  [Fact]
  public void Rejects_non_status_or_short_frames()
  {
    Assert.False(OmegaStatusDecoder.TryDecode(new byte[30], out _));

    var wrongCommand = new byte[31];
    wrongCommand[0] = 0x55;
    wrongCommand[1] = 0xAA;
    wrongCommand[5] = 0x16;
    Assert.False(OmegaStatusDecoder.TryDecode(wrongCommand, out _));
  }

  [Fact]
  public void Rejects_status_frame_with_mismatched_declared_length_or_terminator()
  {
    var frame = new byte[40];
    frame[0] = 0x55;
    frame[1] = 0xAA;
    frame[5] = 0x17;
    frame[6] = 30;
    frame[^2] = 0x0D;
    frame[^1] = 0x00;

    Assert.False(OmegaStatusDecoder.TryDecode(frame, out _));

    frame[^1] = 0x0A;
    frame[6] = 29;
    Assert.False(OmegaStatusDecoder.TryDecode(frame, out _));
  }
}
