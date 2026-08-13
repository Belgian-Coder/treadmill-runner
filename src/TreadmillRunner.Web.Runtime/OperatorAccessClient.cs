using System.Net;
using System.Net.Http.Json;

namespace TreadmillRunner.Web.Runtime;

public sealed class OperatorAccessClient(HttpClient http)
{
  public Task<OperatorStatusContract?> GetStatusAsync(CancellationToken cancellationToken = default) =>
    http.GetFromJsonAsync<OperatorStatusContract>("api/operator/status", cancellationToken);

  public async Task<OperatorLoginContract> LoginAsync(string passphrase, CancellationToken cancellationToken = default)
  {
    using HttpResponseMessage response = await http.PostAsJsonAsync("api/operator/login", new { passphrase }, cancellationToken);
    if (response.StatusCode == HttpStatusCode.TooManyRequests) return new(false, null, null, true);
    if (!response.IsSuccessStatusCode) return new(false, null, null, false);
    OperatorLoginResponseContract? login = await response.Content.ReadFromJsonAsync<OperatorLoginResponseContract>(cancellationToken);
    return login is null
      ? new OperatorLoginContract(false, null, null, false)
      : new OperatorLoginContract(true, login.Token, login.ExpiresAtUtc, false);
  }

  public async Task LogoutAsync(CancellationToken cancellationToken = default)
  {
    using HttpResponseMessage response = await http.PostAsJsonAsync("api/operator/logout", new { }, cancellationToken);
    response.EnsureSuccessStatusCode();
  }

  private sealed record OperatorLoginResponseContract(string Token, DateTimeOffset ExpiresAtUtc);
}

public sealed record OperatorStatusContract(bool Enabled, bool Authenticated, DateTimeOffset? ExpiresAtUtc);
public sealed record OperatorLoginContract(bool Succeeded, string? Token, DateTimeOffset? ExpiresAtUtc, bool RateLimited);
