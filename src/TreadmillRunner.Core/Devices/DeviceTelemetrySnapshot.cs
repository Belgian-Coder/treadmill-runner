namespace TreadmillRunner.Core.Devices;

public sealed record DeviceConnectionSnapshot(
  DeviceRole Role,
  DeviceConnectionState State,
  long ConnectionGeneration,
  string? DisplayName,
  string? ProtocolId,
  string? TelemetryMode,
  DateTimeOffset? LastObservedAt,
  string? Fault,
  string? ModelNumber = null,
  string? FirmwareRevision = null,
  TreadmillCapabilityEvidence Evidence = TreadmillCapabilityEvidence.Unknown,
  TreadmillCapabilities? Capabilities = null);

public sealed record DeviceTelemetrySnapshot(
  DateTimeOffset CapturedAt,
  DeviceConnectionSnapshot Treadmill,
  DeviceConnectionSnapshot HeartRate,
  TreadmillTelemetry? TreadmillTelemetry,
  ushort? HeartRateBpm,
  DateTimeOffset? HeartRateObservedAt,
  TreadmillCapabilities? ReportedCapabilities,
  IReadOnlyList<HeartRateSourceSnapshot>? HeartRateSources = null,
  Guid? SelectedHeartRateEnrollmentId = null,
  HeartRateDeviceKind? SelectedHeartRateDeviceKind = null,
  HeartRateDeviceFamily? SelectedHeartRateDeviceFamily = null,
  long HeartRateSelectionGeneration = 0,
  string? HeartRateSelectionReason = null,
  byte? SelectedHeartRateBatteryPercent = null,
  DateTimeOffset? SelectedHeartRateBatteryObservedAt = null,
  HeartRateSignalQuality SelectedHeartRateQuality = HeartRateSignalQuality.Unavailable,
  HeartRateContactState SelectedHeartRateContactState = HeartRateContactState.Unknown)
{
  public TimeSpan? TreadmillAge => TreadmillTelemetry is null
    ? null
    : AgeOrNull(CapturedAt - TreadmillTelemetry.ObservedAt);

  public TimeSpan? TreadmillSpeedAge => TreadmillTelemetry is { SpeedObservedAt: { } observedAt }
    ? AgeOrNull(CapturedAt - observedAt)
    : null;

  public TimeSpan? TreadmillInclineAge => TreadmillTelemetry is { InclineObservedAt: { } observedAt }
    ? AgeOrNull(CapturedAt - observedAt)
    : null;

  public TimeSpan? HeartRateAge => HeartRateObservedAt is null
    ? null
    : AgeOrNull(CapturedAt - HeartRateObservedAt.Value);

  private static TimeSpan? AgeOrNull(TimeSpan value) =>
    value < TimeSpan.Zero ? null : value;
}
