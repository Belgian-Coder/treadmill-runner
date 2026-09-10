namespace TreadmillRunner.Protocols.Polar;

public static class PolarPftpRfc60Codec
{
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
    return (ushort)(message[0] | ((message[1] & 0x7F) << 8));
  }

  public static byte[] EncodeOperation(ReadOnlySpan<byte> operation, ReadOnlySpan<byte> data = default)
  {
    if (operation.Length > 0x7FFF) throw new ArgumentOutOfRangeException(nameof(operation));
    byte[] result = new byte[2 + operation.Length + data.Length];
    result[0] = (byte)operation.Length;
    result[1] = (byte)(operation.Length >> 8);
    operation.CopyTo(result.AsSpan(2));
    data.CopyTo(result.AsSpan(2 + operation.Length));
    return result;
  }

  public static (ReadOnlyMemory<byte> Operation, ReadOnlyMemory<byte> Data) DecodeOperation(ReadOnlyMemory<byte> message)
  {
    if (message.Length < 2) throw new FormatException("Malformed RFC-60 operation envelope.");
    int length = message.Span[0] | (message.Span[1] << 8);
    if (length > message.Length - 2) throw new FormatException("RFC-60 operation exceeds message bounds.");
    return (message.Slice(2, length), message.Slice(2 + length));
  }
}
