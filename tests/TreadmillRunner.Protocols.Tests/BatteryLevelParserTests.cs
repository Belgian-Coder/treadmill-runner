using TreadmillRunner.Protocols.HeartRate;

namespace TreadmillRunner.Protocols.Tests;

public sealed class BatteryLevelParserTests
{
  [Theory]
  [InlineData(0)]
  [InlineData(1)]
  [InlineData(99)]
  [InlineData(100)]
  public void Parses_valid_percentage(byte expected)
  {
    Assert.True(BatteryLevelParser.TryParse([expected], out byte actual));
    Assert.Equal(expected, actual);
  }

  [Fact]
  public void Rejects_malformed_or_out_of_range_payload()
  {
    Assert.False(BatteryLevelParser.TryParse([], out _));
    Assert.False(BatteryLevelParser.TryParse([50, 51], out _));
    Assert.False(BatteryLevelParser.TryParse([101], out _));
    Assert.False(BatteryLevelParser.TryParse([255], out _));
  }
}
