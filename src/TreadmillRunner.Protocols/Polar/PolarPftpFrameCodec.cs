namespace TreadmillRunner.Protocols.Polar;

public enum PolarPftpFrameStatus : byte
{
  ResponseOrError = 0,
  Last = 1,
  More = 3,
}

public readonly record struct PolarPftpFrame(
  byte Sequence,
  PolarPftpFrameStatus Status,
  bool Next,
  ReadOnlyMemory<byte> Payload);

public static class PolarPftpFrameCodec
{
  public static IReadOnlyList<byte[]> EncodeRequest(
    ReadOnlySpan<byte> message,
    int mtu,
    ref byte sequence)
  {
    ValidateMtu(mtu);
    int payloadSize = mtu - 1;
    int packetCount = Math.Max(1, (message.Length + payloadSize - 1) / payloadSize);
    var packets = new byte[packetCount][];
    for (int index = 0; index < packetCount; index++)
    {
      int offset = index * payloadSize;
      int length = Math.Min(payloadSize, message.Length - offset);
      byte[] packet = new byte[length + 1];
      PolarPftpFrameStatus status = index == packetCount - 1
        ? PolarPftpFrameStatus.Last
        : PolarPftpFrameStatus.More;
      packet[0] = ComposeHeader(sequence, status, index > 0);
      message.Slice(offset, length).CopyTo(packet.AsSpan(1));
      packets[index] = packet;
      sequence = (byte)((sequence + 1) & 0x0F);
    }
    return packets;
  }

  public static PolarPftpFrame Decode(ReadOnlySpan<byte> packet)
  {
    if (packet.Length < PolarPftpConstants.MinimumFrameSize)
      throw new FormatException("A Polar PFTP frame must contain a header.");
    byte header = packet[0];
    byte sequence = (byte)(header >> 4);
    PolarPftpFrameStatus status = (PolarPftpFrameStatus)((header >> 1) & 0x03);
    if (status == (PolarPftpFrameStatus)2)
      throw new FormatException("Polar PFTP reserved frame status was received.");
    return new PolarPftpFrame(sequence, status, (header & 1) != 0, packet[1..].ToArray());
  }

  public static byte[] Reassemble(
    IEnumerable<ReadOnlyMemory<byte>> packets,
    byte expectedFirstSequence = 0,
    int maximumBytes = int.MaxValue)
  {
    if (maximumBytes < 0) throw new ArgumentOutOfRangeException(nameof(maximumBytes));
    using var output = new MemoryStream();
    byte expected = expectedFirstSequence;
    bool received = false;
    bool complete = false;
    foreach (ReadOnlyMemory<byte> packet in packets)
    {
      PolarPftpFrame frame = Decode(packet.Span);
      if (received && frame.Sequence != expected)
        throw new FormatException($"Polar PFTP sequence gap: expected {expected}, received {frame.Sequence}.");
      if (!received && frame.Sequence != expectedFirstSequence)
        throw new FormatException($"Polar PFTP sequence mismatch: expected {expectedFirstSequence}, received {frame.Sequence}.");
      bool expectedNext = received;
      if (frame.Next != expectedNext)
        throw new FormatException("Polar PFTP continuation bit is out of sync.");
      if (frame.Status == PolarPftpFrameStatus.ResponseOrError)
      {
        if (frame.Payload.Length < 2) throw new FormatException("Polar PFTP response omitted its status code.");
        ushort code = (ushort)(frame.Payload.Span[0] | (frame.Payload.Span[1] << 8));
        if (code != 0) throw new PolarPftpProtocolException(code);
      }
      else
      {
        if (frame.Payload.Length > maximumBytes - output.Length)
          throw new InvalidOperationException("Polar PFTP response exceeds its bounded size limit.");
        output.Write(frame.Payload.Span);
      }
      received = true;
      expected = (byte)((frame.Sequence + 1) & 0x0F);
      complete = frame.Status is PolarPftpFrameStatus.Last or PolarPftpFrameStatus.ResponseOrError;
      if (complete) break;
    }
    if (!complete)
      throw new FormatException("Polar PFTP response ended before its final frame.");
    return output.ToArray();
  }

  public static byte ComposeHeader(byte sequence, PolarPftpFrameStatus status, bool next)
  {
    if (sequence > 15) throw new ArgumentOutOfRangeException(nameof(sequence));
    if (status is < PolarPftpFrameStatus.ResponseOrError or > PolarPftpFrameStatus.More)
      throw new ArgumentOutOfRangeException(nameof(status));
    return (byte)((sequence << 4) | ((byte)status << 1) | (next ? 1 : 0));
  }

  private static void ValidateMtu(int mtu)
  {
    if (mtu < 2 || mtu > PolarPftpConstants.MaximumFrameSize)
      throw new ArgumentOutOfRangeException(nameof(mtu));
  }
}

public sealed class PolarPftpProtocolException(ushort code) : IOException($"Polar PFTP remote error {code}.")
{
  public ushort Code { get; } = code;
}
