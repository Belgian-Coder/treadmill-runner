using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using TreadmillRunner.Core.System;
using TreadmillRunner.Gateway.Security;

namespace TreadmillRunner.IntegrationTests;

public sealed class OperatorAccessEndpointTests(PlanningGatewayFactory factory) : IClassFixture<PlanningGatewayFactory>
{
  private const string Passphrase = "correct horse battery staple";

  [Fact]
  public async Task Enabled_operator_access_keeps_reads_open_and_requires_a_short_lived_bearer_for_mutations()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = Configure(factory);
    using HttpClient client = application.CreateClient();

    OperatorStatusResponse? initial = await client.GetFromJsonAsync<OperatorStatusResponse>("/api/operator/status");
    Assert.Equal(new OperatorStatusResponse(true, false, null), initial);
    using HttpResponseMessage read = await client.GetAsync("/api/system/version");
    Assert.Equal(HttpStatusCode.OK, read.StatusCode);
    using HttpResponseMessage denied = await client.PostAsJsonAsync("/api/updates/check", new { });
    Assert.Equal(HttpStatusCode.Unauthorized, denied.StatusCode);
    Assert.Contains("OperatorAccessRequired", await denied.Content.ReadAsStringAsync(), StringComparison.Ordinal);

    using HttpResponseMessage invalid = await client.PostAsJsonAsync("/api/operator/login", new { passphrase = "incorrect passphrase" });
    Assert.Equal(HttpStatusCode.Unauthorized, invalid.StatusCode);
    using HttpResponseMessage loginResponse = await client.PostAsJsonAsync("/api/operator/login", new { passphrase = Passphrase });
    Assert.Equal(HttpStatusCode.OK, loginResponse.StatusCode);
    OperatorLoginResponse? login = await loginResponse.Content.ReadFromJsonAsync<OperatorLoginResponse>();
    Assert.NotNull(login);
    Assert.True(login.ExpiresAtUtc > DateTimeOffset.UtcNow);

    client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", login.Token);
    client.DefaultRequestHeaders.Add("X-TreadmillRunner-Client-Build", AppBuildInfo.Fingerprint);
    OperatorStatusResponse? unlocked = await client.GetFromJsonAsync<OperatorStatusResponse>("/api/operator/status");
    Assert.True(unlocked?.Authenticated);
    using HttpResponseMessage authorized = await client.PostAsJsonAsync("/api/updates/check", new { });
    Assert.NotEqual(HttpStatusCode.Unauthorized, authorized.StatusCode);

    using HttpResponseMessage logout = await client.PostAsJsonAsync("/api/operator/logout", new { });
    Assert.Equal(HttpStatusCode.NoContent, logout.StatusCode);
    using HttpResponseMessage deniedAgain = await client.PostAsJsonAsync("/api/updates/check", new { });
    Assert.Equal(HttpStatusCode.Unauthorized, deniedAgain.StatusCode);
  }

  [Fact]
  public async Task Failed_operator_logins_are_rate_limited_per_peer()
  {
    using WebApplicationFactory<TreadmillRunner.Gateway.Program> application = Configure(factory);
    using HttpClient client = application.CreateClient();

    for (var attempt = 0; attempt < 3; attempt++)
    {
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/operator/login", new { passphrase = "wrong" });
      Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }
    using HttpResponseMessage limited = await client.PostAsJsonAsync("/api/operator/login", new { passphrase = "wrong" });
    Assert.Equal(HttpStatusCode.TooManyRequests, limited.StatusCode);
  }

  private static WebApplicationFactory<TreadmillRunner.Gateway.Program> Configure(PlanningGatewayFactory source)
  {
    IReadOnlyDictionary<string, string?> values = new Dictionary<string, string?>
    {
      ["OperatorAccess:Enabled"] = "true",
      ["OperatorAccess:SecretHash"] = CreateHash(Passphrase),
      ["OperatorAccess:SessionMinutes"] = "5",
      ["OperatorAccess:MaximumFailedAttempts"] = "3",
      ["OperatorAccess:FailureWindowMinutes"] = "5",
    };
    return source.WithWebHostBuilder(builder =>
      builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(values)));
  }

  private static string CreateHash(string passphrase)
  {
    byte[] salt = RandomNumberGenerator.GetBytes(16);
    const int iterations = 100_000;
    byte[] hash = Rfc2898DeriveBytes.Pbkdf2(
      Encoding.UTF8.GetBytes(passphrase), salt, iterations, HashAlgorithmName.SHA256, 32);
    return $"pbkdf2-sha256${iterations}${Convert.ToBase64String(salt)}${Convert.ToBase64String(hash)}";
  }
}
