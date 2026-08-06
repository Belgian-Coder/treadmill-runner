namespace TreadmillRunner.Core.Devices;

public enum TreadmillMaintenanceState
{
  SetupRequired,
  Current,
  DueByDate,
  DueByDistance,
  DueByDateAndDistance,
}

public sealed record TreadmillMaintenancePolicy(
  Guid Id,
  Guid DeviceEnrollmentId,
  string DeviceDisplayName,
  int IntervalMonths,
  double DistanceIntervalKilometers,
  int Version,
  DateTimeOffset UpdatedAtUtc);

public sealed record TreadmillMaintenanceEvent(
  Guid Id,
  Guid PolicyId,
  Guid OperationId,
  DateTimeOffset PerformedAtUtc,
  double AppDistanceBaselineKilometers,
  string? Note,
  DateTimeOffset CreatedAtUtc);

public sealed record TreadmillMaintenanceSnapshot(
  TreadmillMaintenancePolicy Policy,
  TreadmillMaintenanceState State,
  bool IsDue,
  double AppTrackedHardwareDistanceKilometers,
  DateTimeOffset? NextDueAtUtc,
  double? NextDueDistanceKilometers,
  double? RemainingKilometers,
  TreadmillMaintenanceEvent? LastEvent,
  IReadOnlyList<TreadmillMaintenanceEvent> Events,
  string UsageNotice);
