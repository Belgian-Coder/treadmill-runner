using TreadmillRunner.Protocols.Omega;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Protocols.Tests;

public sealed class OmegaZCompatibilityProfileTests
{
  private static readonly Guid CyclingSpeedAndCadence =
    Guid.Parse("00001816-0000-1000-8000-00805f9b34fb");
  private static readonly Guid FitnessMachine =
    Guid.Parse("00001826-0000-1000-8000-00805f9b34fb");

  [Fact]
  public void Default_profile_preserves_evidence_backed_Omega_Z_compatibility_choices()
  {
    var profile = OmegaZCompatibilityProfile.Default;

    Assert.Equal("horizon-omega-z", profile.ProfileId);
    Assert.Equal("horizon-omega-z", ((ITreadmillProtocol)profile).ProtocolId);
    Assert.Equal("JFTMOmega Z", profile.AdvertisedNamePrefix);
    Assert.Equal(OmegaZTelemetryPreference.Ftms, profile.TelemetryPreference);
    Assert.Equal(OmegaZVendorProtocolCandidate.ParagonXCompatible, profile.VendorProtocolCandidate);
    Assert.True(profile.RequirePhysicalBeltMovementForRunningState);
  }

  [Theory]
  [InlineData("JFTMOmega Z")]
  [InlineData("jftmomega z")]
  [InlineData("JFTMOmega Z 1234")]
  public void Default_profile_matches_Omega_Z_advertisements(string advertisedName)
  {
    var identity = new TreadmillAdvertisementIdentity(advertisedName, []);

    Assert.True(OmegaZCompatibilityProfile.Default.CanHandle(identity));
  }

  [Fact]
  public void Default_profile_matches_the_observed_anonymous_dual_service_signature()
  {
    var identity = new TreadmillAdvertisementIdentity(
      Name: null,
      [CyclingSpeedAndCadence, FitnessMachine]);

    Assert.True(OmegaZCompatibilityProfile.Default.CanHandle(identity));
  }

  [Theory]
  [InlineData("Future Treadmill", true, true)]
  [InlineData(null, false, true)]
  [InlineData(null, true, false)]
  public void Default_profile_rejects_named_unknown_or_incomplete_service_signatures(
    string? advertisedName,
    bool includeCyclingSpeedAndCadence,
    bool includeFitnessMachine)
  {
    var services = new List<Guid>();
    if (includeCyclingSpeedAndCadence) services.Add(CyclingSpeedAndCadence);
    if (includeFitnessMachine) services.Add(FitnessMachine);
    var identity = new TreadmillAdvertisementIdentity(advertisedName, services);

    Assert.False(OmegaZCompatibilityProfile.Default.CanHandle(identity));
  }

  [Fact]
  public void Registry_resolves_an_adapter_without_knowing_its_concrete_type()
  {
    var registry = new TreadmillProtocolRegistry([OmegaZCompatibilityProfile.Default]);

    var match = registry.Resolve(new TreadmillAdvertisementIdentity("JFTMOmega Z", []));

    Assert.Same(OmegaZCompatibilityProfile.Default, match);
    Assert.Null(registry.Resolve(new TreadmillAdvertisementIdentity("Future Domyos", [])));
  }

  [Fact]
  public void Default_profile_does_not_enable_unverified_remote_control()
  {
    var profile = OmegaZCompatibilityProfile.Default;

    Assert.False(profile.Capabilities.CanSetSpeedRemotely);
    Assert.False(profile.Capabilities.CanSetInclineRemotely);
    Assert.False(profile.Capabilities.CanPauseRemotely);
    Assert.False(profile.Capabilities.CanStopRemotely);
    Assert.False(profile.Capabilities.CanStartRemotely);
    Assert.Equal(OmegaZControlProtocolStatus.HardwareValidationRequired, profile.ControlProtocolStatus);
  }
}
