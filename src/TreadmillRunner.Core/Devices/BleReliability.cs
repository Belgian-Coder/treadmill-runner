namespace TreadmillRunner.Core.Devices;

public enum BleReliabilityFailureKind
{
  NativeDisconnected,
  TelemetrySilent,
  NotificationEnded,
  GattTimeout,
  InvalidTelemetry,
  RequiredCharacteristicMissing,
  AdapterUnavailable,
}

public sealed record BleReliabilityIncident(
  Guid Id,
  Guid DeviceEnrollmentId,
  DeviceRole Role,
  string DeviceDisplayName,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset? RecoveredAtUtc,
  long FirstConnectionGeneration,
  long? RecoveredConnectionGeneration,
  int FailedAttemptCount,
  BleReliabilityFailureKind FailureKind,
  string LastSanitizedFault,
  double MaximumReconnectDelaySeconds)
{
  public TimeSpan? RecoveryDuration => RecoveredAtUtc is null
    ? null
    : RecoveredAtUtc.Value - StartedAtUtc;
}
