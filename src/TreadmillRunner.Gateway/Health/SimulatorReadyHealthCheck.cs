using Microsoft.Extensions.Diagnostics.HealthChecks;
using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.Gateway.Health;

public sealed class SimulatorReadyHealthCheck(ILiveSnapshotSource snapshots) : IHealthCheck
{
  public Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default)
  {
    var snapshot = snapshots.Current;
    return Task.FromResult(HealthCheckResult.Healthy("Simulator live telemetry is available.", new Dictionary<string, object>
    {
      ["capturedAt"] = snapshot.CapturedAt,
    }));
  }
}
