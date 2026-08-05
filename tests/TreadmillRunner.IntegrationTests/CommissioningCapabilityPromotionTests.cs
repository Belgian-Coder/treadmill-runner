using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class CommissioningCapabilityPromotionTests
{
  [Theory]
  [InlineData(TreadmillCommandKind.Start)]
  [InlineData(TreadmillCommandKind.SetSpeed)]
  [InlineData(TreadmillCommandKind.SetIncline)]
  [InlineData(TreadmillCommandKind.Pause)]
  [InlineData(TreadmillCommandKind.Stop)]
  public void Promotes_only_the_confirmed_command_capability(TreadmillCommandKind kind)
  {
    var original = new TreadmillCapabilities(
      SpeedRange: new TreadmillOperatingRange(0.8m, 20m, 0.1m, TreadmillCapabilityEvidence.ProtocolReported),
      InclineRange: new TreadmillOperatingRange(0m, 12m, 0.5m, TreadmillCapabilityEvidence.ProtocolReported));

    TreadmillCapabilities promoted = CommissioningCapabilityPromotion.Promote(original, kind);

    Assert.Equal(kind == TreadmillCommandKind.Start, promoted.CanStartRemotely);
    Assert.Equal(kind == TreadmillCommandKind.SetSpeed, promoted.CanSetSpeedRemotely);
    Assert.Equal(kind == TreadmillCommandKind.SetIncline, promoted.CanSetInclineRemotely);
    Assert.Equal(kind == TreadmillCommandKind.Pause, promoted.CanPauseRemotely);
    Assert.Equal(kind == TreadmillCommandKind.Stop, promoted.CanStopRemotely);
    Assert.Same(original.SpeedRange, promoted.SpeedRange);
    Assert.Same(original.InclineRange, promoted.InclineRange);
  }
}
