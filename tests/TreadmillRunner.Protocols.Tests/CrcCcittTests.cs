using TreadmillRunner.Protocols.Omega;

namespace TreadmillRunner.Protocols.Tests;

public sealed class CrcCcittTests
{
  [Fact]
  public void Matches_canonical_check_value()
  {
    Assert.Equal(0x29B1, CrcCcitt.Compute("123456789"u8));
  }

  [Theory]
  [InlineData("640001", 0x9B16)]
  [InlineData("3200", 0x7EF8)]
  public void Matches_omega_payload_vectors(string hex, ushort expected)
  {
    Assert.Equal(expected, CrcCcitt.Compute(Convert.FromHexString(hex)));
  }
}
