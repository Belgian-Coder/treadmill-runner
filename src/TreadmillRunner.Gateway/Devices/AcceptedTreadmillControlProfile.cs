using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Gateway.Devices;

internal static class AcceptedTreadmillControlProfile
{
  private const string ProtocolId = "horizon-omega-z";
  private const string ModelNumber = "OMEGA Z";
  private const string FirmwareRevision = "V10.23.17";

  public static bool Matches(
    DeviceEnrollment enrollment,
    string? observedModel = null,
    string? observedFirmware = null) =>
    enrollment.Role == DeviceRole.Treadmill &&
    enrollment.TelemetryMode == TreadmillTelemetryMode.Ftms &&
    string.Equals(enrollment.ProtocolId, ProtocolId, StringComparison.Ordinal) &&
    string.Equals(observedModel ?? enrollment.ModelNumber, ModelNumber, StringComparison.OrdinalIgnoreCase) &&
    string.Equals(observedFirmware ?? enrollment.FirmwareRevision, FirmwareRevision, StringComparison.OrdinalIgnoreCase);

  public static TreadmillCapabilities Enable(TreadmillCapabilities? reported)
  {
    TreadmillCapabilities source = reported ?? new();
    return source with
    {
      CanSetSpeedRemotely = true,
      CanSetInclineRemotely = true,
      CanPauseRemotely = false,
      CanStopRemotely = true,
      CanStartRemotely = true,
      SpeedRange = Verified(source.SpeedRange, 0.8m, 20m, 0.1m),
      InclineRange = Verified(source.InclineRange, 0m, 12m, 0.1m),
    };
  }

  public static TreadmillCapabilities Disable(TreadmillCapabilities? current)
  {
    TreadmillCapabilities source = current ?? new();
    return source with
    {
      CanSetSpeedRemotely = false,
      CanSetInclineRemotely = false,
      CanPauseRemotely = false,
      CanStopRemotely = false,
      CanStartRemotely = false,
    };
  }

  private static TreadmillOperatingRange Verified(
    TreadmillOperatingRange? current,
    decimal minimum,
    decimal maximum,
    decimal increment) => new(
      current?.Minimum ?? minimum,
      current?.Maximum ?? maximum,
      current?.Increment ?? increment,
      TreadmillCapabilityEvidence.HardwareVerified);
}
