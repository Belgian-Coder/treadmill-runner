using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Core.Sessions;

public sealed record SessionHeartRateZoneSnapshot(
  int Number,
  string Name,
  ushort MinimumBpm,
  ushort MaximumBpm)
{
  public HeartRateZone ToHeartRateZone() => new(Number, Name, MinimumBpm, MaximumBpm);
}

public sealed record SessionProfileSnapshot(
  double WeightKilograms,
  ushort? MaximumHeartRateBpm,
  double? MaximumSpeedKph,
  IReadOnlyList<SessionHeartRateZoneSnapshot> HeartRateZones,
  SessionHeartRateControllerSnapshot? HeartRateController = null)
{
  public static SessionProfileSnapshot FromProfile(UserProfile profile)
  {
    ArgumentNullException.ThrowIfNull(profile);
    return new SessionProfileSnapshot(
      profile.WeightKilograms,
      profile.MaximumHeartRateBpm,
      profile.MaximumSpeedKph,
      profile.HeartRateZones
        .Select(static zone => new SessionHeartRateZoneSnapshot(
          zone.Number,
          zone.Name,
          zone.MinimumBpm,
          zone.MaximumBpm))
        .ToArray(),
      SessionHeartRateControllerSnapshot.FromSettings(profile.HeartRateController));
  }
}

public sealed record SessionHeartRateControllerSnapshot(
  double IncreaseStepKph,
  int IncreaseCooldownSeconds,
  double DecreaseStepKph,
  int DecreaseCooldownSeconds)
{
  public static SessionHeartRateControllerSnapshot FromSettings(HeartRateControllerSettings settings) =>
    new(
      settings.IncreaseStepKph,
      settings.IncreaseCooldownSeconds,
      settings.DecreaseStepKph,
      settings.DecreaseCooldownSeconds);
}

public sealed record SessionExecutionConfiguration(
  string Mode,
  string HeartRateController,
  SessionProfileSnapshot Profile,
  string? HeartRateSourceLabel = null,
  string? HeartRateSourceKind = null,
  string? HeartRateSourceFamily = null,
  SessionTreadmillSnapshot? Treadmill = null);

public sealed record SessionTreadmillSnapshot(
  string IdentityLabel,
  string ProtocolId,
  string TelemetryMode,
  string? ModelNumber,
  string? FirmwareRevision,
  TreadmillRunner.Core.Devices.TreadmillCapabilityEvidence Evidence,
  TreadmillRunner.Core.Devices.TreadmillCapabilities Capabilities,
  long ConnectionGeneration,
  Guid? EnrollmentId = null,
  string? IdentityFingerprint = null);
