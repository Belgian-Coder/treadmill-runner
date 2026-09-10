namespace TreadmillRunner.Protocols.Polar;

public readonly record struct PolarProtobufField(int Number, int WireType, ulong Varint, ReadOnlyMemory<byte> Bytes);

/// <summary>Bounded protobuf wire reader/writer for the small published Polar messages used here.</summary>
public static class PolarPftpProtobuf
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

  public static IReadOnlyList<PolarProtobufField> DecodeFields(ReadOnlySpan<byte> payload)
  {
    var fields = new List<PolarProtobufField>();
    int offset = 0;
    while (offset < payload.Length)
    {
      ulong key = ReadVarint(payload, ref offset);
      int number = checked((int)(key >> 3));
      if (number is < 1 or > 536_870_911) throw new FormatException("Invalid protobuf field number.");
      int wireType = (int)(key & 7);
      switch (wireType)
      {
        case 0:
          fields.Add(new(number, wireType, ReadVarint(payload, ref offset), ReadOnlyMemory<byte>.Empty));
          break;
        case 1:
          RequireRemaining(payload, offset, 8); offset += 8;
          break;
        case 2:
          int length = checked((int)ReadVarint(payload, ref offset));
          RequireRemaining(payload, offset, length);
          fields.Add(new(number, wireType, 0, payload.Slice(offset, length).ToArray()));
          offset += length;
          break;
        case 5:
          RequireRemaining(payload, offset, 4); offset += 4;
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
    if (number is < 1 or > 536_870_911) throw new ArgumentOutOfRangeException(nameof(number));
  }

  private static void RequireRemaining(ReadOnlySpan<byte> payload, int offset, int length)
  {
    if (length < 0 || length > payload.Length - offset) throw new FormatException("Truncated protobuf field.");
  }
}
