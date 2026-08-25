namespace TreadmillRunner.Core.Devices;

public sealed record TreadmillTelemetry(
    DateTimeOffset ObservedAt,
    double SpeedKph,
    double InclinePercent,
    DateTimeOffset? SpeedObservedAt = null,
    DateTimeOffset? InclineObservedAt = null,
    double? AverageSpeedKph = null,
    uint? TotalDistanceMeters = null,
    double? PositiveElevationGainMeters = null,
    double? NegativeElevationGainMeters = null,
    ushort? InstantaneousPaceSecondsPer500Meters = null,
    ushort? AveragePaceSecondsPer500Meters = null,
    ushort? TotalEnergyKilocalories = null,
    ushort? EnergyPerHourKilocalories = null,
    byte? EnergyPerMinuteKilocalories = null,
    ushort? HeartRateBpm = null,
    double? MetabolicEquivalent = null,
    TimeSpan? ElapsedTime = null,
    TimeSpan? RemainingTime = null,
    short? ForceNewtons = null,
    short? PowerWatts = null);
