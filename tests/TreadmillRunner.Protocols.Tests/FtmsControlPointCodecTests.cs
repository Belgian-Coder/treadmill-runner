using TreadmillRunner.Protocols.Ftms;

namespace TreadmillRunner.Protocols.Tests;

public sealed class FtmsControlPointCodecTests
{
  [Fact]
  public void Encodes_request_control_start_and_stop_golden_payloads()
  {
    Assert.Equal(Convert.FromHexString("00"), FtmsControlPointCodec.EncodeRequestControl());
    Assert.Equal(Convert.FromHexString("07"), FtmsControlPointCodec.EncodeStartOrResume());
    Assert.Equal(Convert.FromHexString("0801"), FtmsControlPointCodec.EncodeStop());
    Assert.Equal(Convert.FromHexString("0802"), FtmsControlPointCodec.EncodePause());
    Assert.Equal(Convert.FromHexString("026400"), FtmsControlPointCodec.EncodeTargetSpeed(1.0));
    Assert.Equal(Convert.FromHexString("031900"), FtmsControlPointCodec.EncodeTargetInclination(2.5));
  }

  [Theory]
  [InlineData(double.NaN)]
  [InlineData(double.PositiveInfinity)]
  [InlineData(-0.1)]
  [InlineData(655.36)]
  public void Target_speed_rejects_values_outside_the_wire_range(double value)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => FtmsControlPointCodec.EncodeTargetSpeed(value));
  }

  [Theory]
  [InlineData(double.NaN)]
  [InlineData(double.PositiveInfinity)]
  [InlineData(-3276.9)]
  [InlineData(3276.8)]
  public void Target_inclination_rejects_values_outside_the_wire_range(double value)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => FtmsControlPointCodec.EncodeTargetInclination(value));
  }

  [Theory]
  [InlineData("800001", FtmsControlPointOpCode.RequestControl, FtmsControlPointResultCode.Success)]
  [InlineData("800705", FtmsControlPointOpCode.StartOrResume, FtmsControlPointResultCode.ControlNotPermitted)]
  [InlineData("800804", FtmsControlPointOpCode.StopOrPause, FtmsControlPointResultCode.OperationFailed)]
  public void Parses_response_code(
    string hex,
    FtmsControlPointOpCode expectedRequest,
    FtmsControlPointResultCode expectedResult)
  {
    Assert.True(FtmsControlPointCodec.TryParseResponse(Convert.FromHexString(hex), out var response));
    Assert.Equal(expectedRequest, response.RequestOpCode);
    Assert.Equal(expectedResult, response.ResultCode);
  }

  [Theory]
  [InlineData("")]
  [InlineData("8000")]
  [InlineData("810001")]
  [InlineData("800000")]
  [InlineData("800006")]
  [InlineData("80000100")]
  public void Rejects_malformed_response(string hex)
  {
    Assert.False(FtmsControlPointCodec.TryParseResponse(Convert.FromHexString(hex), out _));
  }
}
