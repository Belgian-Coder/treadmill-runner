using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;

namespace TreadmillRunner.Core.Tests;

public sealed class ContractsTests
{
  [Fact]
  public void Treadmill_capabilities_are_safe_by_default()
  {
    var capabilities = new TreadmillCapabilities();

    Assert.False(capabilities.CanSetSpeedRemotely);
    Assert.False(capabilities.CanSetInclineRemotely);
    Assert.False(capabilities.CanPauseRemotely);
    Assert.False(capabilities.CanStopRemotely);
    Assert.False(capabilities.CanStartRemotely);
    Assert.False(capabilities.ReportsSpeedTargetSupport);
    Assert.False(capabilities.ReportsInclineTargetSupport);
    Assert.False(capabilities.ReportsStandardStartResume);
    Assert.Null(capabilities.SpeedRange);
    Assert.Null(capabilities.InclineRange);
  }

  [Fact]
  public void Operating_range_preserves_source_evidence_and_bounds_values()
  {
    var range = new TreadmillOperatingRange(
      minimum: 0.5m,
      maximum: 18m,
      increment: 0.1m,
      TreadmillCapabilityEvidence.ProtocolReported);

    Assert.True(range.Contains(12.3m));
    Assert.False(range.Contains(18.1m));
    Assert.Equal(0.5m, range.Clamp(-1m));
    Assert.Equal(18m, range.Clamp(21m));
    Assert.Equal(TreadmillCapabilityEvidence.ProtocolReported, range.Evidence);
  }

  [Theory]
  [InlineData(10, 5, 1)]
  [InlineData(0, 10, 0)]
  public void Operating_range_rejects_invalid_bounds(
    double minimum,
    double maximum,
    double increment)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new TreadmillOperatingRange(
      (decimal)minimum,
      (decimal)maximum,
      (decimal)increment,
      TreadmillCapabilityEvidence.ProtocolReported));
  }

  [Fact]
  public void Live_snapshot_contract_has_stable_namespace()
  {
    Assert.Equal("TreadmillRunner.Core.Live", typeof(LiveSnapshot).Namespace);
  }

  [Fact]
  public void Core_contracts_do_not_reference_WinRT()
  {
    var references = typeof(TreadmillCapabilities).Assembly.GetReferencedAssemblies();

    Assert.DoesNotContain(references, reference =>
      reference.Name?.StartsWith("Windows", StringComparison.OrdinalIgnoreCase) == true ||
      reference.Name?.Contains("WinRT", StringComparison.OrdinalIgnoreCase) == true);
  }
}
