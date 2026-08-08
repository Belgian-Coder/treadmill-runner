using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class AcceptedTreadmillControlProfileTests
{
  [Fact]
  public void Exact_household_omega_profile_restores_verified_daily_controls()
  {
    DeviceEnrollment enrollment = Enrollment("OMEGA Z", "V10.23.17");

    Assert.True(AcceptedTreadmillControlProfile.Matches(enrollment));
    TreadmillCapabilities enabled = AcceptedTreadmillControlProfile.Enable(enrollment.Capabilities);

    Assert.True(enabled.CanStartRemotely);
    Assert.True(enabled.CanStopRemotely);
    Assert.True(enabled.CanSetSpeedRemotely);
    Assert.True(enabled.CanSetInclineRemotely);
    Assert.False(enabled.CanPauseRemotely);
    Assert.Equal(TreadmillCapabilityEvidence.HardwareVerified, enabled.SpeedRange!.Evidence);
    Assert.Equal(TreadmillCapabilityEvidence.HardwareVerified, enabled.InclineRange!.Evidence);
  }

  [Theory]
  [InlineData("OTHER", "V10.23.17")]
  [InlineData("OMEGA Z", "different")]
  public void Other_profiles_cannot_inherit_the_household_authorization(string model, string firmware)
  {
    Assert.False(AcceptedTreadmillControlProfile.Matches(Enrollment(model, firmware)));
  }

  [Fact]
  public void Owner_can_disable_all_motion_controls_without_enabling_raw_pause()
  {
    TreadmillCapabilities disabled = AcceptedTreadmillControlProfile.Disable(
      AcceptedTreadmillControlProfile.Enable(Enrollment("OMEGA Z", "V10.23.17").Capabilities));

    Assert.False(disabled.CanStartRemotely);
    Assert.False(disabled.CanStopRemotely);
    Assert.False(disabled.CanSetSpeedRemotely);
    Assert.False(disabled.CanSetInclineRemotely);
    Assert.False(disabled.CanPauseRemotely);
  }

  private static DeviceEnrollment Enrollment(string model, string firmware) => new(
    Guid.NewGuid(),
    DeviceRole.Treadmill,
    "A1B2C3D4E5F6",
    "horizon-omega-z",
    new string('a', 64),
    "Omega",
    model,
    firmware,
    TreadmillTelemetryMode.Ftms,
    new TreadmillCapabilities(
      ReportsSpeedTargetSupport: true,
      ReportsInclineTargetSupport: true,
      ReportsStandardStartResume: true,
      SpeedRange: new TreadmillOperatingRange(0.8m, 20m, 0.1m, TreadmillCapabilityEvidence.ProtocolReported),
      InclineRange: new TreadmillOperatingRange(0m, 12m, 0.1m, TreadmillCapabilityEvidence.ProtocolReported)),
    TreadmillCapabilityEvidence.PassivelyObserved,
    DateTimeOffset.UtcNow);
}
