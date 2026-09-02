using System.Net;
using System.Net.Http.Json;

namespace TreadmillRunner.IntegrationTests;

public sealed class ControlLeaseEndpointContractTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Acquire_rejects_a_blank_holder_as_a_client_error()
  {
    using HttpClient client = factory.CreateClient();

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      "/api/live/lease/acquire",
      new { holderId = "" });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    Assert.Contains("control lease holder", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Heartbeat_rejects_a_blank_holder_as_a_client_error()
  {
    using HttpClient client = factory.CreateClient();
    DateTimeOffset now = DateTimeOffset.UtcNow;

    using HttpResponseMessage response = await client.PostAsJsonAsync(
      "/api/live/lease/heartbeat",
      new
      {
        id = Guid.NewGuid(),
        holderId = "",
        acquiredAt = now,
        expiresAt = now.AddSeconds(15),
      });

    Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    Assert.Contains("control lease holder", await response.Content.ReadAsStringAsync(), StringComparison.OrdinalIgnoreCase);
  }
}
