using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Protocols.Omega;

public enum OmegaZTelemetryPreference
{
  Ftms,
  Vendor
}

public enum OmegaZVendorProtocolCandidate
{
  ParagonXCompatible
}

public enum OmegaZControlProtocolStatus
{
  HardwareValidationRequired,
  Verified
}

/// <summary>
/// Independently named compatibility choices for the Horizon Omega Z. The
/// defaults capture upstream field reports without enabling any BLE writes.
/// </summary>
public sealed record OmegaZCompatibilityProfile(
  string ProfileId,
  string AdvertisedNamePrefix,
  OmegaZTelemetryPreference TelemetryPreference,
  OmegaZVendorProtocolCandidate VendorProtocolCandidate,
  bool RequirePhysicalBeltMovementForRunningState,
  OmegaZControlProtocolStatus ControlProtocolStatus,
  TreadmillCapabilities Capabilities) : ITreadmillProtocol
{
  private static readonly Guid CyclingSpeedAndCadenceService =
    Guid.Parse("00001816-0000-1000-8000-00805f9b34fb");
  private static readonly Guid FitnessMachineService =
    Guid.Parse("00001826-0000-1000-8000-00805f9b34fb");

  public string ProtocolId => ProfileId;

  public string DisplayName => "Horizon Omega Z";

  public int MatchPriority => 100;

  public static OmegaZCompatibilityProfile Default { get; } = new(
    ProfileId: "horizon-omega-z",
    AdvertisedNamePrefix: "JFTMOmega Z",
    TelemetryPreference: OmegaZTelemetryPreference.Ftms,
    VendorProtocolCandidate: OmegaZVendorProtocolCandidate.ParagonXCompatible,
    RequirePhysicalBeltMovementForRunningState: true,
    ControlProtocolStatus: OmegaZControlProtocolStatus.HardwareValidationRequired,
    Capabilities: new TreadmillCapabilities());

  public bool CanHandle(TreadmillAdvertisementIdentity identity)
  {
    ArgumentNullException.ThrowIfNull(identity);

    if (identity.Name?.StartsWith(AdvertisedNamePrefix, StringComparison.OrdinalIgnoreCase) == true)
    {
      return true;
    }

    return string.IsNullOrWhiteSpace(identity.Name) &&
      identity.ServiceUuids.Contains(CyclingSpeedAndCadenceService) &&
      identity.ServiceUuids.Contains(FitnessMachineService);
  }
}
