using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Live;

public sealed record LiveSnapshot(
    DateTimeOffset CapturedAt,
    DeviceConnectionState TreadmillConnectionState,
    DeviceConnectionState HeartRateConnectionState,
    SessionState SessionState,
    double SpeedKph,
    double InclinePercent,
    ushort? HeartRateBpm,
    TimeSpan Elapsed,
    double DistanceKilometers,
    TimeSpan? Pace,
    TimeSpan TelemetryAge,
    string? HeartRateDeviceName = null,
    TimeSpan? HeartRateTelemetryAge = null,
    Guid? HeartRateEnrollmentId = null,
    HeartRateDeviceKind? HeartRateDeviceKind = null,
    HeartRateDeviceFamily? HeartRateDeviceFamily = null,
    long HeartRateSelectionGeneration = 0,
    string? HeartRateSelectionReason = null,
    string? TreadmillIdentityLabel = null,
    string? TreadmillProtocolId = null,
    string? TreadmillTelemetryMode = null,
    string? TreadmillModelNumber = null,
    string? TreadmillFirmwareRevision = null,
    TreadmillCapabilityEvidence TreadmillEvidence = TreadmillCapabilityEvidence.Unknown,
    TreadmillCapabilities? TreadmillCapabilities = null,
    long TreadmillConnectionGeneration = 0,
    byte? HeartRateBatteryPercent = null,
    DateTimeOffset? HeartRateBatteryObservedAt = null);
