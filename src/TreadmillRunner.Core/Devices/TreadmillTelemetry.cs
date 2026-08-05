namespace TreadmillRunner.Core.Devices;

public sealed record TreadmillTelemetry(
    DateTimeOffset ObservedAt,
    double SpeedKph,
    double InclinePercent);
