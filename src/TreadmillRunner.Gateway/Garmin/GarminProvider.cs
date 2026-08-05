using System.Net.Http.Headers;
using System.Text.Json;
using Microsoft.AspNetCore.WebUtilities;
using Microsoft.Extensions.Options;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminAuthorizationResult(
  string Subject,
  string AccountLabel,
  string AccessToken,
  string? RefreshToken,
  DateTimeOffset? ExpiresAtUtc,
  string Scopes);

public sealed record GarminPublishResult(string? RemoteId);

public interface IGarminProvider
{
  bool IsConfigured { get; }
  bool SupportsSafeRetry { get; }
  string SetupMessage { get; }
  Uri CreateAuthorizationUri(Guid profileId, string state, string codeChallenge, Uri redirectUri);
  Task<GarminAuthorizationResult> ExchangeCodeAsync(string code, string codeVerifier, Uri redirectUri, CancellationToken cancellationToken);
  Task<GarminAuthorizationResult> RefreshAsync(string refreshToken, CancellationToken cancellationToken);
  Task<GarminPublishResult> PublishAsync(string kind, string payloadJson, string accessToken, string idempotencyKey, CancellationToken cancellationToken);
}

public sealed class DisabledGarminProvider : IGarminProvider
{
  public bool IsConfigured => false;
  public bool SupportsSafeRetry => false;
  public string SetupMessage => "Garmin setup is required. Add approved Garmin Connect Developer Program credentials and Training API configuration on the server.";

  public Uri CreateAuthorizationUri(Guid profileId, string state, string codeChallenge, Uri redirectUri) => throw NotConfigured();
  public Task<GarminAuthorizationResult> ExchangeCodeAsync(string code, string codeVerifier, Uri redirectUri, CancellationToken cancellationToken) => throw NotConfigured();
  public Task<GarminAuthorizationResult> RefreshAsync(string refreshToken, CancellationToken cancellationToken) => throw NotConfigured();
  public Task<GarminPublishResult> PublishAsync(string kind, string payloadJson, string accessToken, string idempotencyKey, CancellationToken cancellationToken) => throw NotConfigured();

  private static InvalidOperationException NotConfigured() => new("Garmin Connect is not configured.");
}

public sealed class MockGarminProvider(TimeProvider timeProvider) : IGarminProvider
{
  public bool IsConfigured => true;
  public bool SupportsSafeRetry => true;
  public string SetupMessage => "Mock Garmin provider is active for deterministic development testing.";

  public Uri CreateAuthorizationUri(Guid profileId, string state, string codeChallenge, Uri redirectUri)
  {
    string url = QueryHelpers.AddQueryString(redirectUri.ToString(), new Dictionary<string, string?>
    {
      ["code"] = $"mock-{profileId:N}",
      ["state"] = state,
    });
    return new Uri(url);
  }

  public Task<GarminAuthorizationResult> ExchangeCodeAsync(
    string code,
    string codeVerifier,
    Uri redirectUri,
    CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    if (!code.StartsWith("mock-", StringComparison.Ordinal) || codeVerifier.Length < 43)
    {
      throw new InvalidOperationException("The mock Garmin authorization response is invalid.");
    }
    string subject = code[5..];
    return Task.FromResult(new GarminAuthorizationResult(
      subject,
      $"Garmin test account {subject[..Math.Min(8, subject.Length)]}",
      $"mock-access-{subject}",
      $"mock-refresh-{subject}",
      timeProvider.GetUtcNow().AddHours(1),
      "training"));
  }

  public Task<GarminAuthorizationResult> RefreshAsync(string refreshToken, CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    string subject = refreshToken.Replace("mock-refresh-", string.Empty, StringComparison.Ordinal);
    return Task.FromResult(new GarminAuthorizationResult(
      subject,
      $"Garmin test account {subject[..Math.Min(8, subject.Length)]}",
      $"mock-access-{subject}",
      refreshToken,
      timeProvider.GetUtcNow().AddHours(1),
      "training"));
  }

  public Task<GarminPublishResult> PublishAsync(
    string kind,
    string payloadJson,
    string accessToken,
    string idempotencyKey,
    CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    if (!accessToken.StartsWith("mock-access-", StringComparison.Ordinal) || string.IsNullOrWhiteSpace(payloadJson))
    {
      throw new InvalidOperationException("The mock Garmin publish request is invalid.");
    }
    return Task.FromResult(new GarminPublishResult($"mock-{kind.ToLowerInvariant()}-{idempotencyKey[..12]}"));
  }
}

// Garmin supplies the detailed Training API contract only to approved program partners.
// Production publishing stays structurally unavailable until an independently implemented,
// fixture-tested contract adapter is registered here; configuration alone cannot bypass it.
public interface IGarminTrainingContractAdapter
{
  bool IsApproved { get; }
  bool SupportsSafeRetry { get; }
  HttpRequestMessage CreatePublishRequest(string kind, string canonicalPayloadJson, string accessToken, string idempotencyKey, GarminOptions options);
  Task<GarminPublishResult> ReadPublishResponseAsync(HttpResponseMessage response, CancellationToken cancellationToken);
}

public sealed class UnavailableGarminTrainingContractAdapter : IGarminTrainingContractAdapter
{
  public bool IsApproved => false;
  public bool SupportsSafeRetry => false;

  public HttpRequestMessage CreatePublishRequest(string kind, string canonicalPayloadJson, string accessToken, string idempotencyKey, GarminOptions options) =>
    throw new InvalidOperationException("The approved Garmin Training API contract adapter is not installed.");

  public Task<GarminPublishResult> ReadPublishResponseAsync(HttpResponseMessage response, CancellationToken cancellationToken) =>
    throw new InvalidOperationException("The approved Garmin Training API contract adapter is not installed.");
}

public sealed class ConfiguredGarminProvider(
  IHttpClientFactory httpClientFactory,
  IOptionsMonitor<GarminOptions> options,
  IGarminTrainingContractAdapter contractAdapter,
  TimeProvider timeProvider) : IGarminProvider
{
  private GarminOptions Options => options.CurrentValue;

  public bool IsConfigured =>
    Options.ApprovedTrainingContract &&
    contractAdapter.IsApproved &&
    IsHttps(Options.AuthorizationEndpoint) &&
    IsHttps(Options.TokenEndpoint) &&
    IsHttps(Options.IdentityEndpoint) &&
    IsHttps(Options.WorkoutEndpoint) &&
    IsHttps(Options.TrainingPlanEndpoint) &&
    IsHttps(Options.CalendarEndpoint) &&
    IsHttps(Options.CallbackUri) &&
    !string.IsNullOrWhiteSpace(Options.ClientId) &&
    !string.IsNullOrWhiteSpace(Options.ClientSecret);

  public bool SupportsSafeRetry => IsConfigured && contractAdapter.SupportsSafeRetry;

  public string SetupMessage => IsConfigured
    ? "Garmin Connect Developer Program configuration is ready."
    : "Garmin setup is incomplete. An approved, fixture-tested Garmin Training API contract adapter, exact HTTPS callback, endpoints, and credentials are required.";

  public Uri CreateAuthorizationUri(Guid profileId, string state, string codeChallenge, Uri redirectUri)
  {
    EnsureConfigured();
    EnsureRegisteredCallback(redirectUri);
    string url = QueryHelpers.AddQueryString(Options.AuthorizationEndpoint!, new Dictionary<string, string?>
    {
      ["response_type"] = "code",
      ["client_id"] = Options.ClientId,
      ["redirect_uri"] = redirectUri.ToString(),
      ["scope"] = Options.Scope,
      ["state"] = state,
      ["code_challenge"] = codeChallenge,
      ["code_challenge_method"] = "S256",
    });
    return new Uri(url);
  }

  public async Task<GarminAuthorizationResult> ExchangeCodeAsync(
    string code,
    string codeVerifier,
    Uri redirectUri,
    CancellationToken cancellationToken)
  {
    EnsureConfigured();
    EnsureRegisteredCallback(redirectUri);
    using var content = new FormUrlEncodedContent(new Dictionary<string, string>
    {
      ["grant_type"] = "authorization_code",
      ["code"] = code,
      ["redirect_uri"] = redirectUri.ToString(),
      ["client_id"] = Options.ClientId!,
      ["client_secret"] = Options.ClientSecret!,
      ["code_verifier"] = codeVerifier,
    });
    TokenResponse token = await SendTokenAsync(content, cancellationToken);
    return await ReadIdentityAsync(token, cancellationToken);
  }

  public async Task<GarminAuthorizationResult> RefreshAsync(string refreshToken, CancellationToken cancellationToken)
  {
    EnsureConfigured();
    using var content = new FormUrlEncodedContent(new Dictionary<string, string>
    {
      ["grant_type"] = "refresh_token",
      ["refresh_token"] = refreshToken,
      ["client_id"] = Options.ClientId!,
      ["client_secret"] = Options.ClientSecret!,
    });
    TokenResponse token = await SendTokenAsync(content, cancellationToken);
    if (string.IsNullOrWhiteSpace(token.RefreshToken)) token = token with { RefreshToken = refreshToken };
    return await ReadIdentityAsync(token, cancellationToken);
  }

  public async Task<GarminPublishResult> PublishAsync(
    string kind,
    string payloadJson,
    string accessToken,
    string idempotencyKey,
    CancellationToken cancellationToken)
  {
    EnsureConfigured();
    using HttpRequestMessage request = contractAdapter.CreatePublishRequest(kind, payloadJson, accessToken, idempotencyKey, Options);
    using HttpClient httpClient = httpClientFactory.CreateClient("GarminConnect");
    using HttpResponseMessage response = await httpClient.SendAsync(request, cancellationToken);
    return await contractAdapter.ReadPublishResponseAsync(response, cancellationToken);
  }

  private async Task<TokenResponse> SendTokenAsync(HttpContent content, CancellationToken cancellationToken)
  {
    using HttpClient httpClient = httpClientFactory.CreateClient("GarminConnect");
    using HttpResponseMessage response = await httpClient.PostAsync(Options.TokenEndpoint, content, cancellationToken);
    response.EnsureSuccessStatusCode();
    using JsonDocument json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
    JsonElement root = json.RootElement;
    string access = root.GetProperty("access_token").GetString() ?? throw new InvalidOperationException("Garmin token response omitted access_token.");
    string? refresh = root.TryGetProperty("refresh_token", out JsonElement refreshElement) ? refreshElement.GetString() : null;
    string scope = root.TryGetProperty("scope", out JsonElement scopeElement) ? scopeElement.GetString() ?? Options.Scope : Options.Scope;
    int? expires = root.TryGetProperty("expires_in", out JsonElement expiry) && expiry.TryGetInt32(out int seconds) ? seconds : null;
    return new TokenResponse(access, refresh, scope, expires);
  }

  private async Task<GarminAuthorizationResult> ReadIdentityAsync(TokenResponse token, CancellationToken cancellationToken)
  {
    using var request = new HttpRequestMessage(HttpMethod.Get, Options.IdentityEndpoint);
    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token.AccessToken);
    using HttpClient httpClient = httpClientFactory.CreateClient("GarminConnect");
    using HttpResponseMessage response = await httpClient.SendAsync(request, cancellationToken);
    response.EnsureSuccessStatusCode();
    using JsonDocument json = JsonDocument.Parse(await response.Content.ReadAsStringAsync(cancellationToken));
    JsonElement root = json.RootElement;
    string subject = ReadRequired(root, "sub", "userId", "id");
    string label = ReadOptional(root, "name", "displayName", "username") ?? $"Garmin account {subject[..Math.Min(8, subject.Length)]}";
    return new GarminAuthorizationResult(
      subject,
      label,
      token.AccessToken,
      token.RefreshToken,
      token.ExpiresInSeconds is { } seconds ? timeProvider.GetUtcNow().AddSeconds(seconds) : null,
      token.Scope);
  }

  private static string ReadRequired(JsonElement root, params string[] names) =>
    ReadOptional(root, names) ?? throw new InvalidOperationException("Garmin identity response omitted a stable account identifier.");

  private static string? ReadOptional(JsonElement root, params string[] names)
  {
    foreach (string name in names)
    {
      if (root.TryGetProperty(name, out JsonElement value) && !string.IsNullOrWhiteSpace(value.ToString())) return value.ToString();
    }
    return null;
  }

  private void EnsureConfigured()
  {
    if (!IsConfigured) throw new InvalidOperationException(SetupMessage);
  }

  private void EnsureRegisteredCallback(Uri redirectUri)
  {
    var registered = new Uri(Options.CallbackUri!, UriKind.Absolute);
    if (registered != redirectUri)
    {
      throw new InvalidOperationException("The Garmin callback URI does not match the registered HTTPS callback.");
    }
  }

  private static bool IsHttps(string? value) => Uri.TryCreate(value, UriKind.Absolute, out Uri? uri) && uri.Scheme == Uri.UriSchemeHttps;

  private sealed record TokenResponse(string AccessToken, string? RefreshToken, string Scope, int? ExpiresInSeconds);
}
