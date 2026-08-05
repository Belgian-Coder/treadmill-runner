using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Tests;

public sealed class DeviceEnrollmentTests
{
  [Fact]
  public void Treadmill_requires_mode_and_capabilities()
  {
    Assert.Throws<ArgumentException>(() => new DeviceEnrollment(
      Guid.NewGuid(), DeviceRole.Treadmill, "device", "omega", Fingerprint,
      "Omega", null, null, null, null, TreadmillCapabilityEvidence.Unknown, null));
  }

  [Fact]
  public void Heart_rate_rejects_treadmill_settings()
  {
    Assert.Throws<ArgumentException>(() => new DeviceEnrollment(
      Guid.NewGuid(), DeviceRole.HeartRate, "device", "heart-rate", Fingerprint,
      "Polar H10", null, null, TreadmillTelemetryMode.Ftms, new TreadmillCapabilities(),
      TreadmillCapabilityEvidence.Unknown, null));
  }

  [Fact]
  public void Preserves_local_identity_and_verification_evidence()
  {
    DateTimeOffset verified = DateTimeOffset.Parse("2026-08-03T18:00:00Z");
    var enrollment = new DeviceEnrollment(
      Guid.NewGuid(), DeviceRole.Treadmill, "A1B2C3D4E5F6", "horizon-omega-z", Fingerprint,
      "Horizon Omega Z", "Omega Z", "1.2.3", TreadmillTelemetryMode.Ftms,
      new TreadmillCapabilities(ReportsSpeedTargetSupport: true),
      TreadmillCapabilityEvidence.ProtocolReported, verified);

    Assert.Equal(TreadmillTelemetryMode.Ftms, enrollment.TelemetryMode);
    Assert.Equal(verified, enrollment.LastVerifiedAtUtc);
    Assert.False(enrollment.Capabilities!.CanStartRemotely);
  }

  private static string Fingerprint => new('a', 64);
}
