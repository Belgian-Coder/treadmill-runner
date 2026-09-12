using System.Buffers.Binary;

namespace TreadmillRunner.PolarH10Simulator;

/// <summary>
/// UUIDs and small wire codecs used by the simulator. These are project-authored
/// values based on the public Bluetooth Heart Rate Service and Polar PFTP UUIDs.
/// </summary>
public static class PolarH10SimulatorUuids
{
  public static readonly Guid HeartRateService = Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");
  public static readonly Guid HeartRateMeasurement = Guid.Parse("00002a37-0000-1000-8000-00805f9b34fb");
  public static readonly Guid BatteryService = Guid.Parse("0000180f-0000-1000-8000-00805f9b34fb");
  public static readonly Guid BatteryLevel = Guid.Parse("00002a19-0000-1000-8000-00805f9b34fb");

  public static readonly Guid PolarPftpService = Guid.Parse("0000feee-0000-1000-8000-00805f9b34fb");
  public static readonly Guid PolarPftpMtu = Guid.Parse("fb005c51-02e7-f387-1cad-8acd2d8df0c8");
  public static readonly Guid PolarPftpDeviceToHost = Guid.Parse("fb005c52-02e7-f387-1cad-8acd2d8df0c8");
  public static readonly Guid PolarPftpHostToDevice = Guid.Parse("fb005c53-02e7-f387-1cad-8acd2d8df0c8");
}

public enum PolarH10SimulatorCharacteristic
{
  Mtu,
  DeviceToHost,
  HostToDevice,
  HeartRate,
}

public enum PolarH10SimulatorFrameStatus : byte
{
  ResponseOrError = 0,
  Last = 1,
  More = 3,
}

public readonly record struct PolarH10SimulatorFrame(
  byte Sequence,
  PolarH10SimulatorFrameStatus Status,
  bool Next,
  ReadOnlyMemory<byte> Payload);

public sealed class PolarH10SimulatorProtocolException(ushort code, string message)
  : IOException(message)
{
  public ushort Code { get; } = code;
}

/// <summary>Minimal PFTP framing and RFC-60/protobuf helpers owned by the simulator.</summary>
public static class PolarH10SimulatorFrameCodec
{
  public const int DefaultFrameSize = 20;
  public const int MaximumFrameSize = 509;
  public const int MaximumMessageBytes = 64 * 1024;

  public static IReadOnlyList<byte[]> EncodeRequest(ReadOnlySpan<byte> message, int frameSize, ref byte sequence)
    => Encode(message, frameSize, ref sequence, PolarH10SimulatorFrameStatus.Last);

  public static IReadOnlyList<byte[]> EncodeResponse(ReadOnlySpan<byte> message, int frameSize)
  {
    byte sequence = 0;
    return Encode(message, frameSize, ref sequence, PolarH10SimulatorFrameStatus.Last);
  }

  public static byte[] EncodeError(ushort code)
  {
    byte[] packet = new byte[3];
    packet[0] = ComposeHeader(0, PolarH10SimulatorFrameStatus.ResponseOrError, false);
    BinaryPrimitives.WriteUInt16LittleEndian(packet.AsSpan(1), code);
    return packet;
  }

  public static PolarH10SimulatorFrame Decode(ReadOnlySpan<byte> packet)
  {
    if (packet.Length == 0)
      throw new FormatException("A Polar PFTP frame must contain a header.");
    byte header = packet[0];
    PolarH10SimulatorFrameStatus status = (PolarH10SimulatorFrameStatus)((header >> 1) & 0x03);
    if (status == (PolarH10SimulatorFrameStatus)2)
      throw new FormatException("Polar PFTP reserved frame status was received.");
    return new((byte)(header >> 4), status, (header & 1) != 0, packet[1..].ToArray());
  }

  public static byte[] Reassemble(
    IEnumerable<ReadOnlyMemory<byte>> packets,
    byte expectedFirstSequence = 0,
    int maximumBytes = MaximumMessageBytes)
  {
    if (maximumBytes < 0) throw new ArgumentOutOfRangeException(nameof(maximumBytes));
    using var output = new MemoryStream();
    bool received = false;
    byte expected = expectedFirstSequence;
    bool complete = false;

    foreach (ReadOnlyMemory<byte> packet in packets)
    {
      PolarH10SimulatorFrame frame = Decode(packet.Span);
      if (frame.Sequence != (received ? expected : expectedFirstSequence))
        throw new FormatException("Polar PFTP sequence is not contiguous.");
      if (frame.Next != received)
        throw new FormatException("Polar PFTP continuation bit is out of sync.");

      if (frame.Status == PolarH10SimulatorFrameStatus.ResponseOrError)
      {
        if (frame.Payload.Length < 2)
          throw new FormatException("Polar PFTP response omitted its status code.");
        ushort code = BinaryPrimitives.ReadUInt16LittleEndian(frame.Payload.Span);
        if (code != 0) throw new PolarH10SimulatorProtocolException(code, $"Polar PFTP remote error {code}.");
        WriteBounded(output, frame.Payload[2..].Span, maximumBytes);
      }
      else
      {
        WriteBounded(output, frame.Payload.Span, maximumBytes);
      }

      received = true;
      expected = (byte)((frame.Sequence + 1) & 0x0f);
      complete = frame.Status is PolarH10SimulatorFrameStatus.Last or PolarH10SimulatorFrameStatus.ResponseOrError;
      if (complete) break;
    }

    if (!complete) throw new FormatException("Polar PFTP response ended before its final frame.");
    return output.ToArray();
  }

  public static byte ComposeHeader(byte sequence, PolarH10SimulatorFrameStatus status, bool next)
  {
    if (sequence > 15) throw new ArgumentOutOfRangeException(nameof(sequence));
    if (status is < PolarH10SimulatorFrameStatus.ResponseOrError or > PolarH10SimulatorFrameStatus.More)
      throw new ArgumentOutOfRangeException(nameof(status));
    return (byte)((sequence << 4) | ((byte)status << 1) | (next ? 1 : 0));
  }

  public static byte[] EncodeQuery(ushort queryId, ReadOnlySpan<byte> parameters = default)
  {
    byte[] result = new byte[2 + parameters.Length];
    result[0] = (byte)queryId;
    result[1] = (byte)((queryId >> 8) | 0x80);
    parameters.CopyTo(result.AsSpan(2));
    return result;
  }

  public static ushort ReadQueryId(ReadOnlySpan<byte> message)
  {
    if (message.Length < 2 || (message[1] & 0x80) == 0)
      throw new FormatException("Malformed RFC-60 query header.");
    return (ushort)(message[0] | ((message[1] & 0x7f) << 8));
  }

  public static byte[] EncodeOperation(ReadOnlySpan<byte> operation, ReadOnlySpan<byte> data = default)
  {
    if (operation.Length > 0x7fff) throw new ArgumentOutOfRangeException(nameof(operation));
    byte[] result = new byte[2 + operation.Length + data.Length];
    BinaryPrimitives.WriteUInt16LittleEndian(result, checked((ushort)operation.Length));
    operation.CopyTo(result.AsSpan(2));
    data.CopyTo(result.AsSpan(2 + operation.Length));
    return result;
  }

  public static (ReadOnlyMemory<byte> Operation, ReadOnlyMemory<byte> Data) DecodeOperation(ReadOnlyMemory<byte> message)
  {
    if (message.Length < 2) throw new FormatException("Malformed RFC-60 operation envelope.");
    int length = BinaryPrimitives.ReadUInt16LittleEndian(message.Span);
    if (length > message.Length - 2) throw new FormatException("RFC-60 operation exceeds message bounds.");
    return (message.Slice(2, length), message[(2 + length)..]);
  }

  private static IReadOnlyList<byte[]> Encode(
    ReadOnlySpan<byte> message,
    int frameSize,
    ref byte sequence,
    PolarH10SimulatorFrameStatus finalStatus)
  {
    if (frameSize is < 2 or > MaximumFrameSize) throw new ArgumentOutOfRangeException(nameof(frameSize));
    if (message.Length > MaximumMessageBytes) throw new ArgumentOutOfRangeException(nameof(message));
    int payloadSize = frameSize - 1;
    int packetCount = Math.Max(1, (message.Length + payloadSize - 1) / payloadSize);
    var packets = new byte[packetCount][];
    for (int index = 0; index < packetCount; index++)
    {
      int offset = index * payloadSize;
      int length = Math.Min(payloadSize, message.Length - offset);
      byte[] packet = new byte[length + 1];
      packet[0] = ComposeHeader(sequence, index == packetCount - 1 ? finalStatus : PolarH10SimulatorFrameStatus.More, index > 0);
      message.Slice(offset, length).CopyTo(packet.AsSpan(1));
      packets[index] = packet;
      sequence = (byte)((sequence + 1) & 0x0f);
    }
    return packets;
  }

  private static void WriteBounded(Stream output, ReadOnlySpan<byte> value, int maximumBytes)
  {
    if (value.Length > maximumBytes - output.Length)
      throw new InvalidOperationException("Polar PFTP message exceeds its bounded size limit.");
    output.Write(value);
  }
}

public readonly record struct PolarH10SimulatorProtobufField(
  int Number,
  int WireType,
  ulong Varint,
  ReadOnlyMemory<byte> Bytes);

public static class PolarH10SimulatorProtobuf
{
  public static byte[] EncodeBytesField(int number, ReadOnlySpan<byte> value)
  {
    ValidateNumber(number);
    using var stream = new MemoryStream();
    WriteVarint(stream, ((ulong)number << 3) | 2);
    WriteVarint(stream, (ulong)value.Length);
    stream.Write(value);
    return stream.ToArray();
  }

  public static byte[] EncodePackedVarintsField(int number, IEnumerable<ulong> values)
  {
    using var packed = new MemoryStream();
    foreach (ulong value in values) WriteVarint(packed, value);
    return EncodeBytesField(number, packed.ToArray());
  }

  public static byte[] EncodeFields(params (int Number, ulong Value)[] fields)
  {
    using var stream = new MemoryStream();
    foreach ((int number, ulong value) in fields)
    {
      ValidateNumber(number);
      WriteVarint(stream, (ulong)number << 3);
      WriteVarint(stream, value);
    }
    return stream.ToArray();
  }

  public static IReadOnlyList<PolarH10SimulatorProtobufField> DecodeFields(ReadOnlySpan<byte> payload)
  {
    if (payload.Length > PolarH10SimulatorFrameCodec.MaximumMessageBytes)
      throw new FormatException("Protobuf payload exceeds the simulator limit.");
    var fields = new List<PolarH10SimulatorProtobufField>();
    int offset = 0;
    while (offset < payload.Length)
    {
      ulong key = ReadVarint(payload, ref offset);
      int number = checked((int)(key >> 3));
      ValidateNumber(number);
      int wireType = (int)(key & 7);
      switch (wireType)
      {
        case 0:
          fields.Add(new(number, wireType, ReadVarint(payload, ref offset), ReadOnlyMemory<byte>.Empty));
          break;
        case 1:
          RequireRemaining(payload, offset, 8);
          offset += 8;
          break;
        case 2:
          int length = checked((int)ReadVarint(payload, ref offset));
          RequireRemaining(payload, offset, length);
          fields.Add(new(number, wireType, 0, payload.Slice(offset, length).ToArray()));
          offset += length;
          break;
        case 5:
          RequireRemaining(payload, offset, 4);
          offset += 4;
          break;
        default:
          throw new FormatException("Unsupported protobuf wire type.");
      }
    }
    return fields;
  }

  public static ulong ReadVarint(ReadOnlySpan<byte> payload, ref int offset)
  {
    ulong value = 0;
    for (int shift = 0; shift < 64; shift += 7)
    {
      if (offset >= payload.Length) throw new FormatException("Truncated protobuf varint.");
      byte current = payload[offset++];
      value |= (ulong)(current & 0x7f) << shift;
      if ((current & 0x80) == 0) return value;
    }
    throw new FormatException("Protobuf varint is too long.");
  }

  private static void WriteVarint(Stream stream, ulong value)
  {
    while (value >= 0x80)
    {
      stream.WriteByte((byte)((value & 0x7f) | 0x80));
      value >>= 7;
    }
    stream.WriteByte((byte)value);
  }

  private static void ValidateNumber(int number)
  {
    if (number is < 1 or > 536_870_911) throw new FormatException("Invalid protobuf field number.");
  }

  private static void RequireRemaining(ReadOnlySpan<byte> payload, int offset, int length)
  {
    if (length < 0 || length > payload.Length - offset) throw new FormatException("Truncated protobuf field.");
  }
}
