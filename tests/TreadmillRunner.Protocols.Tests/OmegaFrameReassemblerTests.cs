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
  public void Reset_reports_truncated_frame_then_accepts_a_replacement()
  {
    var truncated = CreateFrame(64).AsSpan(0, 20).ToArray();
    var replacement = CreateFrame(2);
    var sut = new OmegaFrameReassembler();

    Assert.Empty(sut.Append(truncated));
    sut.Reset();
    var output = sut.Append(replacement);

    Assert.Equal(replacement, Assert.Single(output));
    var diagnostic = Assert.Single(sut.Diagnostics);
    Assert.Equal(OmegaFrameDiagnosticCode.TruncatedFrame, diagnostic.Code);
    Assert.Equal(74, diagnostic.ExpectedLength);
    Assert.Equal(20, diagnostic.ReceivedLength);
  }

  [Fact]
  public void Declared_payload_can_contain_the_header_marker()
  {
    var frame = CreateFrame(8);
    frame[10] = 0x55;
    frame[11] = 0xAA;
    var sut = new OmegaFrameReassembler();

    IReadOnlyList<byte[]> output = sut.Append(frame);

    Assert.Equal(frame, Assert.Single(output));
    Assert.Empty(sut.Diagnostics);
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

  [Fact]
  public void Rejects_declared_payload_that_exceeds_the_reassembly_bound()
  {
    var frame = CreateFrame(5000);
    var sut = new OmegaFrameReassembler();

    Assert.Empty(sut.Append(frame));
    var diagnostic = Assert.Single(sut.Diagnostics);
    Assert.Equal(OmegaFrameDiagnosticCode.LengthOutOfRange, diagnostic.Code);
    Assert.Equal(5010, diagnostic.ExpectedLength);
  }

  [Fact]
  public void Rejects_frame_with_invalid_terminator()
  {
    var frame = CreateFrame(2);
    frame[^1] = 0x00;
    var sut = new OmegaFrameReassembler();

    Assert.Empty(sut.Append(frame));
    Assert.Equal(OmegaFrameDiagnosticCode.InvalidTerminator, Assert.Single(sut.Diagnostics).Code);
  }

  [Fact]
  public void Recovers_when_the_next_frame_header_replaces_a_corrupt_terminator()
  {
    var corrupt = CreateFrame(2);
    corrupt[^2] = 0x55;
    corrupt[^1] = 0xAA;
    var replacement = CreateFrame(3);
    byte[] stream = corrupt.Concat(replacement.Skip(2)).ToArray();
    var sut = new OmegaFrameReassembler();

    IReadOnlyList<byte[]> output = sut.Append(stream);

    Assert.Equal(replacement, Assert.Single(output));
    Assert.Equal(OmegaFrameDiagnosticCode.InvalidTerminator, Assert.Single(sut.Diagnostics).Code);
  }

  private static byte[] CreateFrame(int payloadLength)
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
