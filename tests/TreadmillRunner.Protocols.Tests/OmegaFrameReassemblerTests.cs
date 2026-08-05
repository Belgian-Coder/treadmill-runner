using TreadmillRunner.Protocols.Omega;

namespace TreadmillRunner.Protocols.Tests;

public sealed class OmegaFrameReassemblerTests
{
  [Fact]
  public void Reassembles_74_byte_frame_from_20_20_20_14_fragments()
  {
    var frame = CreateFrame(64);
    var sut = new OmegaFrameReassembler();
    var output = new List<byte[]>();

    output.AddRange(sut.Append(frame.AsSpan(0, 20)));
    output.AddRange(sut.Append(frame.AsSpan(20, 20)));
    output.AddRange(sut.Append(frame.AsSpan(40, 20)));
    output.AddRange(sut.Append(frame.AsSpan(60, 14)));

    Assert.Equal(frame, Assert.Single(output));
    Assert.Empty(sut.Diagnostics);
  }

  [Fact]
  public void New_header_reports_truncated_frame_and_restarts()
  {
    var truncated = CreateFrame(64).AsSpan(0, 20).ToArray();
    var replacement = CreateFrame(2);
    var sut = new OmegaFrameReassembler();

    Assert.Empty(sut.Append(truncated));
    var output = sut.Append(replacement);

    Assert.Equal(replacement, Assert.Single(output));
    var diagnostic = Assert.Single(sut.Diagnostics);
    Assert.Equal(OmegaFrameDiagnosticCode.TruncatedFrame, diagnostic.Code);
    Assert.Equal(74, diagnostic.ExpectedLength);
    Assert.Equal(20, diagnostic.ReceivedLength);
  }

  [Fact]
  public void Accepts_short_first_fragment_and_coalesced_frames()
  {
    var first = CreateFrame(2);
    var second = CreateFrame(3);
    var bytes = first.Concat(second).ToArray();
    var sut = new OmegaFrameReassembler();

    Assert.Empty(sut.Append(bytes.AsSpan(0, 4)));
    var output = sut.Append(bytes.AsSpan(4));

    Assert.Collection(output,
        frame => Assert.Equal(first, frame),
        frame => Assert.Equal(second, frame));
  }

  private static byte[] CreateFrame(ushort payloadLength)
  {
    var frame = new byte[payloadLength + 10];
    frame[0] = 0x55;
    frame[1] = 0xAA;
    frame[5] = 0x17;
    frame[6] = (byte)payloadLength;
    frame[7] = (byte)(payloadLength >> 8);
    frame[^2] = 0x0D;
    frame[^1] = 0x0A;
    return frame;
  }
}
