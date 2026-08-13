using System.Net;
using System.Text;
using TreadmillRunner.Web.Runtime;

namespace TreadmillRunner.IntegrationTests;

public sealed class ClientRuntimeStateTests
{
  [Fact]
  public async Task Version_checks_are_single_flight_and_forced_recovery_bypasses_freshness()
  {
    var handler = new CountingVersionHandler();
    using var client = new HttpClient(handler) { BaseAddress = new Uri("https://gateway.test/") };
    var runtime = new ClientRuntimeState();

    await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => runtime.CheckAsync(client)));

    Assert.Equal(1, handler.RequestCount);
    Assert.True(runtime.IsConnected);

    await runtime.CheckAsync(client, force: true);

    Assert.Equal(2, handler.RequestCount);
  }

  private sealed class CountingVersionHandler : HttpMessageHandler
  {
    private int requestCount;
    public int RequestCount => requestCount;

    protected override async Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken)
    {
      Interlocked.Increment(ref requestCount);
      await Task.Delay(25, cancellationToken);
      return new HttpResponseMessage(HttpStatusCode.OK)
      {
        Content = new StringContent(
          $$"""{"releaseVersion":"test","buildFingerprint":"{{ClientRuntimeStateHeaderFingerprint}}","serviceStartedAtUtc":"2026-08-12T00:00:00Z"}""",
          Encoding.UTF8,
          "application/json"),
      };
    }

    private static string ClientRuntimeStateHeaderFingerprint =>
      TreadmillRunner.Core.System.AppBuildInfo.Fingerprint;
  }
}
