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

  [Theory]
  [InlineData(HttpStatusCode.OK, "<html>temporary proxy response</html>", "text/html")]
  [InlineData(HttpStatusCode.OK, "{}", "application/json")]
  [InlineData(HttpStatusCode.ServiceUnavailable, "{}", "application/json")]
  public async Task Invalid_version_responses_fail_closed_and_a_forced_check_recovers(
    HttpStatusCode statusCode,
    string body,
    string contentType)
  {
    var handler = new TransientVersionHandler(statusCode, body, contentType);
    using var client = new HttpClient(handler) { BaseAddress = new Uri("https://gateway.test/") };
    var runtime = new ClientRuntimeState();

    await runtime.CheckAsync(client, force: true);

    Assert.False(runtime.IsConnected);
    Assert.False(runtime.UpdateRequired);
    Assert.Null(runtime.ServerFingerprint);

    await runtime.CheckAsync(client, force: true);

    Assert.True(runtime.IsConnected);
    Assert.False(runtime.UpdateRequired);
    Assert.Equal(TreadmillRunner.Core.System.AppBuildInfo.Fingerprint, runtime.ServerFingerprint);
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

  private sealed class TransientVersionHandler(
    HttpStatusCode firstStatusCode,
    string firstBody,
    string firstContentType) : HttpMessageHandler
  {
    private int requestCount;
    public int RequestCount => requestCount;

    protected override Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken)
    {
      int attempt = Interlocked.Increment(ref requestCount);
      if (attempt == 1)
      {
        return Task.FromResult(new HttpResponseMessage(firstStatusCode)
        {
          Content = new StringContent(firstBody, Encoding.UTF8, firstContentType),
        });
      }

      return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
      {
        Content = new StringContent(
          $$"""{"releaseVersion":"test","buildFingerprint":"{{TreadmillRunner.Core.System.AppBuildInfo.Fingerprint}}","serviceStartedAtUtc":"2026-08-12T00:00:00Z"}""",
          Encoding.UTF8,
          "application/json"),
      });
    }
  }
}
