using Microsoft.Extensions.Configuration;
using TreadmillRunner.Gateway.Operations;

namespace TreadmillRunner.IntegrationTests;

public sealed class ApplicationMaintenanceStateTests : IDisposable
{
  private readonly string root = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.ApplicationMaintenanceStateTests",
    Guid.NewGuid().ToString("N"));

  [Fact]
  public void Persistent_update_evidence_blocks_mutations_across_process_lifetimes_until_cleanup()
  {
    string dataRoot = Path.Combine(root, "data");
    string planRoot = Path.Combine(dataRoot, "updates", "plans");
    string markerPath = Path.Combine(dataRoot, "updates", "service-maintenance.lock");
    string planPath = Path.Combine(planRoot, "pending-activation.json");
    Directory.CreateDirectory(planRoot);
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Updates:DataRoot"] = dataRoot,
        ["Updates:PlanRoot"] = planRoot,
      })
      .Build();

    File.WriteAllText(planPath, "pending");
    var afterQueuedRestart = new ApplicationMaintenanceState(configuration);
    Assert.True(afterQueuedRestart.IsActive);
    Assert.False(afterQueuedRestart.TryBeginMutation());
    Assert.False(afterQueuedRestart.TryBegin());

    File.Delete(planPath);
    File.WriteAllText(markerPath, "update");
    var promotedGateway = new ApplicationMaintenanceState(configuration);
    Assert.True(promotedGateway.IsActive);
    Assert.False(promotedGateway.TryBeginMutation());

    File.Delete(markerPath);
    Assert.False(promotedGateway.IsActive);
    Assert.True(promotedGateway.TryBeginMutation());
    promotedGateway.EndMutation();
  }

  public void Dispose()
  {
    if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
  }
}
