using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class DatabaseIntegrityEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Status_and_explicit_idle_check_expose_visible_bounded_result()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    JsonElement initial = await client.GetFromJsonAsync<JsonElement>(
      "/api/operations/database/status");
    string initialState = initial.GetProperty("state").GetString()
      ?? throw new InvalidDataException("Database integrity state is missing.");
    Assert.Contains(
      initialState,
      new[] { "Healthy", "HealthyWithBackupWarning" });
    Assert.False(initial.GetProperty("recoveryRequired").GetBoolean());

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      "/api/operations/database/check",
      new { });
    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    JsonElement completed = await response.Content.ReadFromJsonAsync<JsonElement>();
    string completedState = completed.GetProperty("state").GetString()
      ?? throw new InvalidDataException("Completed database integrity state is missing.");
    Assert.Contains(
      completedState,
      new[] { "Healthy", "HealthyWithBackupWarning" });
    Assert.NotEqual(JsonValueKind.Null, completed.GetProperty("lastQuickCheckAtUtc").ValueKind);
    Assert.NotEqual(JsonValueKind.Null, completed.GetProperty("lastFullCheckAtUtc").ValueKind);
  }
}
