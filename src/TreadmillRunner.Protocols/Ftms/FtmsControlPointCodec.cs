using System.Buffers.Binary;

namespace TreadmillRunner.Protocols.Ftms;

public enum FtmsControlPointOpCode : byte
{
  RequestControl = 0x00,
  SetTargetSpeed = 0x02,
  SetTargetInclination = 0x03,
  StartOrResume = 0x07,
  StopOrPause = 0x08,
  ResponseCode = 0x80,
}

public enum FtmsControlPointResultCode : byte
{
  Success = 0x01,
  OpCodeNotSupported = 0x02,
  InvalidParameter = 0x03,
  OperationFailed = 0x04,
  ControlNotPermitted = 0x05,
}

public readonly record struct FtmsControlPointResponse(
  FtmsControlPointOpCode RequestOpCode,
  FtmsControlPointResultCode ResultCode)
{
  public bool IsSuccess => ResultCode == FtmsControlPointResultCode.Success;
}

public static class FtmsControlPointCodec
{
  private const byte StopParameter = 0x01;
  private const byte PauseParameter = 0x02;

  public static byte[] EncodeRequestControl() => [(byte)FtmsControlPointOpCode.RequestControl];

  public static byte[] EncodeStartOrResume() => [(byte)FtmsControlPointOpCode.StartOrResume];

  public static byte[] EncodeStop() => [(byte)FtmsControlPointOpCode.StopOrPause, StopParameter];

  public static byte[] EncodePause() => [(byte)FtmsControlPointOpCode.StopOrPause, PauseParameter];

  public static byte[] EncodeTargetSpeed(double speedKph)
  {
    if (!double.IsFinite(speedKph) || speedKph < 0 || speedKph > ushort.MaxValue / 100d)
    {
      throw new ArgumentOutOfRangeException(nameof(speedKph));
    }

    ushort raw = checked((ushort)Math.Round(speedKph * 100d, MidpointRounding.AwayFromZero));
    byte[] payload = [(byte)FtmsControlPointOpCode.SetTargetSpeed, 0, 0];
    BinaryPrimitives.WriteUInt16LittleEndian(payload.AsSpan(1), raw);
    return payload;
  }

  public static byte[] EncodeTargetInclination(double inclinePercent)
  {
    if (!double.IsFinite(inclinePercent) ||
        inclinePercent < short.MinValue / 10d ||
        inclinePercent > short.MaxValue / 10d)
    {
      throw new ArgumentOutOfRangeException(nameof(inclinePercent));
    }

    short raw = checked((short)Math.Round(inclinePercent * 10d, MidpointRounding.AwayFromZero));
    byte[] payload = [(byte)FtmsControlPointOpCode.SetTargetInclination, 0, 0];
    BinaryPrimitives.WriteInt16LittleEndian(payload.AsSpan(1), raw);
    return payload;
  }

  public static bool TryParseResponse(
    ReadOnlySpan<byte> payload,
    out FtmsControlPointResponse response)
  {
    if (payload.Length != 3 || payload[0] != (byte)FtmsControlPointOpCode.ResponseCode)
    {
      response = default;
      return false;
    }

    if (!Enum.IsDefined(typeof(FtmsControlPointOpCode), payload[1]) ||
        payload[1] == (byte)FtmsControlPointOpCode.ResponseCode ||
        !Enum.IsDefined(typeof(FtmsControlPointResultCode), payload[2]))
    {
      response = default;
      return false;
    }

    response = new FtmsControlPointResponse(
      (FtmsControlPointOpCode)payload[1],
      (FtmsControlPointResultCode)payload[2]);
    return true;
  }
}
