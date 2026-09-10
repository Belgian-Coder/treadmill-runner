using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Protocols.Tests;

public sealed class PolarPftpFrameCodecTests
{
  [Fact]
  public void Fragments_with_four_bit_wrapping_sequence_numbers()
  {
    byte sequence = 14;
    IReadOnlyList<byte[]> packets = PolarPftpFrameCodec.EncodeRequest(new byte[7], 4, ref sequence);
    Assert.Equal(14, packets[0][0] >> 4);
    Assert.Equal(15, packets[1][0] >> 4);
    Assert.Equal(0, packets[2][0] >> 4);
    Assert.Equal(1, sequence);
  }

  [Fact]
  public void Reassembles_only_contiguous_frames()
  {
    byte sequence = 0;
    IReadOnlyList<byte[]> packets = PolarPftpFrameCodec.EncodeRequest("abcde"u8, 4, ref sequence);
    Assert.Equal("abcde", System.Text.Encoding.ASCII.GetString(
      PolarPftpFrameCodec.Reassemble(packets.Select(packet => (ReadOnlyMemory<byte>)packet))));
  }

  [Fact]
  public void Rejects_sequence_gaps_and_reserved_status()
  {
    Assert.Throws<FormatException>(() => PolarPftpFrameCodec.Reassemble(
      [new byte[] { 0x05, 1 }, new byte[] { 0x25, 2 }]));
    Assert.Throws<FormatException>(() => PolarPftpFrameCodec.Decode([0x04]));
  }

  [Fact]
  public void Encodes_rfc60_query_and_bounded_operation()
  {
    Assert.Equal(new byte[] { 14, 0x80, 7 }, PolarPftpRfc60Codec.EncodeQuery(14, [7]));
    byte[] operationHeader = PolarPftpProtobuf.EncodeFields((1, 0))
      .Concat(PolarPftpProtobuf.EncodeBytesField(2, "/"u8)).ToArray();
    byte[] encoded = PolarPftpRfc60Codec.EncodeOperation(operationHeader);
    Assert.Equal(new byte[] { 5, 0, 8, 0, 0x12, 1, 0x2f }, encoded);
    (ReadOnlyMemory<byte> operation, ReadOnlyMemory<byte> data) =
      PolarPftpRfc60Codec.DecodeOperation(encoded);
    Assert.Equal(operationHeader, operation.ToArray());
    Assert.Empty(data.ToArray());
  }
}
