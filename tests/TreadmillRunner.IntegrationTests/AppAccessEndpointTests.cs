using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using TreadmillRunner.Gateway.Operations;

namespace TreadmillRunner.IntegrationTests;

public sealed class AppAccessEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Configured_local_address_is_returned_and_qr_is_generated_without_using_request_host()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> configured = Configure(factory, new Dictionary<string, string?>
    {
      ["Gateway:PublicUrl"] = "https://treadmillrunner.home:5443/app",
      ["Gateway:Urls"] = "http://127.0.0.1:5180",
    });
    using HttpClient client = configured.CreateClient();
    client.DefaultRequestHeaders.Host = "untrusted.example:9999";

    AppAccessView? view = await client.GetFromJsonAsync<AppAccessView>("/api/operations/access");

    Assert.NotNull(view);
    Assert.True(view.Available);
    AppAccessCandidate candidate = Assert.Single(view.Candidates);
    Assert.Equal("https://treadmillrunner.home:5443/app/", candidate.Url);
    Assert.Equal(candidate.Id, view.PreferredCandidateId);
    Assert.True(candidate.IsSecure);
    Assert.DoesNotContain("untrusted.example", candidate.Url, StringComparison.OrdinalIgnoreCase);

    using HttpResponseMessage qr = await client.GetAsync($"/api/operations/access/qr/{candidate.Id}");
    Assert.Equal(HttpStatusCode.OK, qr.StatusCode);
    Assert.Equal("image/svg+xml", qr.Content.Headers.ContentType?.MediaType);
    Assert.Contains("no-store", qr.Headers.CacheControl?.ToString() ?? string.Empty, StringComparison.OrdinalIgnoreCase);
    string svg = await qr.Content.ReadAsStringAsync();
    Assert.Contains("<svg", svg, StringComparison.Ordinal);

    using HttpResponseMessage unknown = await client.GetAsync("/api/operations/access/qr/not-a-candidate");
    Assert.Equal(HttpStatusCode.NotFound, unknown.StatusCode);
  }

  [Fact]
  public async Task Loopback_only_listener_does_not_offer_an_unusable_phone_address()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> configured = Configure(factory, new Dictionary<string, string?>
    {
      ["Gateway:PublicUrl"] = "http://localhost:5180",
      ["Gateway:Urls"] = "http://127.0.0.1:5180",
      ["ASPNETCORE_URLS"] = "http://127.0.0.1:5180",
    });
    using HttpClient client = configured.CreateClient();

    AppAccessView? view = await client.GetFromJsonAsync<AppAccessView>("/api/operations/access");

    Assert.NotNull(view);
    Assert.False(view.Available);
    Assert.Null(view.PreferredCandidateId);
    Assert.Empty(view.Candidates);
  }

  [Fact]
  public async Task Public_hostname_and_public_ip_are_not_offered_as_private_lan_qr_targets()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> configured = Configure(factory, new Dictionary<string, string?>
    {
      ["Gateway:PublicUrl"] = "https://example.com/app",
      ["Gateway:Urls"] = "https://203.0.113.10:5443",
      ["ASPNETCORE_URLS"] = "http://127.0.0.1:5180",
    });
    using HttpClient client = configured.CreateClient();

    AppAccessView? view = await client.GetFromJsonAsync<AppAccessView>("/api/operations/access");

    Assert.NotNull(view);
    Assert.False(view.Available);
    Assert.Empty(view.Candidates);
  }

  [Fact]
  public async Task Structured_kestrel_https_endpoint_is_offered_and_takes_precedence_over_legacy_urls()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> configured = Configure(factory, new Dictionary<string, string?>
    {
      ["Gateway:PublicUrl"] = "https://treadmillrunner.home:5443/",
      ["Gateway:Urls"] = "http://127.0.0.1:5180",
      ["Kestrel:Endpoints:Https:Url"] = "https://0.0.0.0:5443",
      ["Kestrel:Endpoints:Https:Protocols"] = "Http1AndHttp2AndHttp3",
    });
    using HttpClient client = configured.CreateClient();

    AppAccessView? view = await client.GetFromJsonAsync<AppAccessView>("/api/operations/access");

    Assert.NotNull(view);
    Assert.True(view.Available);
    AppAccessCandidate candidate = Assert.Single(view.Candidates,
      candidate => string.Equals(candidate.Url, "https://treadmillrunner.home:5443/", StringComparison.Ordinal));
    Assert.True(candidate.IsSecure);
    Assert.Equal("https://0.0.0.0:5443", Assert.Single(GatewayListenerConfiguration.GetListenUrls(configured.Services.GetRequiredService<IConfiguration>())));
  }

  private static WebApplicationFactory<TreadmillRunner.Gateway.Program> Configure(
    PlanningGatewayFactory source,
    IReadOnlyDictionary<string, string?> values) => source.WithWebHostBuilder(builder =>
      builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(values)));
}
