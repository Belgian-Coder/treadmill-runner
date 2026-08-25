using Microsoft.Extensions.Diagnostics.HealthChecks;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.Gateway.Health;

public sealed class BleDiagnosticHealthCheck(IReadOnlyDeviceCoordinator devices) : IHealthCheck
{
  private static readonly TimeSpan FreshnessLimit = TimeSpan.FromSeconds(5);

  public Task<HealthCheckResult> CheckHealthAsync(
    HealthCheckContext context,
    CancellationToken cancellationToken = default)
  {
    DeviceTelemetrySnapshot snapshot = devices.Current;
    var data = new Dictionary<string, object>
    {
      ["capturedAt"] = snapshot.CapturedAt,
      ["treadmillState"] = snapshot.Treadmill.State.ToString(),
      ["treadmillGeneration"] = snapshot.Treadmill.ConnectionGeneration,
      ["heartRateState"] = snapshot.HeartRate.State.ToString(),
      ["heartRateGeneration"] = snapshot.HeartRate.ConnectionGeneration,
      ["treadmillAgeMilliseconds"] = snapshot.TreadmillAge?.TotalMilliseconds ?? -1,
      ["treadmillSpeedAgeMilliseconds"] = snapshot.TreadmillSpeedAge?.TotalMilliseconds ?? -1,
      ["treadmillInclineAgeMilliseconds"] = snapshot.TreadmillInclineAge?.TotalMilliseconds ?? -1,
      ["heartRateAgeMilliseconds"] = snapshot.HeartRateAge?.TotalMilliseconds ?? -1,
    };
    if (snapshot.Treadmill.DisplayName is null)
      return Task.FromResult(HealthCheckResult.Degraded("No treadmill is enrolled.", data: data));
    if (snapshot.Treadmill.State != DeviceConnectionState.Ready)
      return Task.FromResult(HealthCheckResult.Degraded("The enrolled treadmill BLE connection is not ready.", data: data));
    if (snapshot.TreadmillSpeedAge is not { } treadmillSpeedAge || treadmillSpeedAge > FreshnessLimit)
      return Task.FromResult(HealthCheckResult.Degraded("The enrolled treadmill is not providing fresh speed telemetry.", data: data));
    if (snapshot.HeartRate.DisplayName is null)
      return Task.FromResult(HealthCheckResult.Degraded("No heart-rate sensor is enrolled or selected.", data: data));
    if (snapshot.HeartRate.State != DeviceConnectionState.Ready)
      return Task.FromResult(HealthCheckResult.Degraded("The selected heart-rate sensor BLE connection is not ready.", data: data));
    if (snapshot.HeartRateAge is not { } heartRateAge || heartRateAge > FreshnessLimit || snapshot.HeartRateBpm is null)
      return Task.FromResult(HealthCheckResult.Degraded("The selected heart-rate sensor is not providing fresh telemetry.", data: data));
    return Task.FromResult(HealthCheckResult.Healthy("Treadmill and heart-rate BLE telemetry are both ready and fresh.", data));
  }
}
