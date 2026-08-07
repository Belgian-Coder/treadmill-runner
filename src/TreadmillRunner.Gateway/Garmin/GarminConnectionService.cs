using System.Security.Cryptography;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Options;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminConnectionStatus(
  Guid ProfileId,
  bool ProviderConfigured,
  string SetupMessage,
  bool Connected,
  string? AccountLabel,
  DateTimeOffset? ConnectedAtUtc,
  DateTimeOffset? LastSyncAttemptAtUtc,
  DateTimeOffset? LastSyncSuccessAtUtc,
  string? LastError,
  int PendingItems,
  int FailedItems,
  int SyncedItems);

public sealed record GarminConnectStart(Uri AuthorizationUrl, DateTimeOffset ExpiresAtUtc);
public sealed record GarminSessionSyncResult(int Added);

public sealed class GarminConnectionService(
  IGarminStore store,
  IGarminProvider provider,
  GarminSyncCatalog catalog,
  GarminSyncWorker worker,
  IDataProtectionProvider dataProtectionProvider,
  IOptions<GarminOptions> options,
  TimeProvider timeProvider)
{
  private static readonly TimeSpan StateLifetime = TimeSpan.FromMinutes(10);
  private readonly IDataProtector _tokenProtector = dataProtectionProvider.CreateProtector("TreadmillRunner.Garmin.Tokens.v1");
  private readonly IDataProtector _stateProtector = dataProtectionProvider.CreateProtector("TreadmillRunner.Garmin.OAuthState.v1");

  public async Task<GarminConnectionStatus> GetStatusAsync(Guid profileId, CancellationToken cancellationToken)
  {
    GarminAccountLinkRecord? link = await store.FindLinkAsync(profileId, cancellationToken);
    GarminSyncQueueStatus queue = await store.GetQueueStatusAsync(profileId, cancellationToken);
    return new GarminConnectionStatus(
      profileId,
      provider.IsConfigured,
      provider.SetupMessage,
      link is not null,
      link?.AccountLabel,
      link?.ConnectedAtUtc,
      link?.LastSyncAttemptAtUtc,
      link?.LastSyncSuccessAtUtc,
      link?.LastSyncError,
      queue.Pending,
      queue.Failed,
      queue.Synced);
  }

  public async Task<GarminConnectStart> StartConnectAsync(Guid profileId, Uri requestBaseUri, CancellationToken cancellationToken)
  {
    if (!provider.IsConfigured) throw new InvalidOperationException(provider.SetupMessage);
    string state = Base64Url(RandomNumberGenerator.GetBytes(32));
    string verifier = Base64Url(RandomNumberGenerator.GetBytes(48));
    string challenge = Base64Url(SHA256.HashData(System.Text.Encoding.ASCII.GetBytes(verifier)));
    DateTimeOffset now = timeProvider.GetUtcNow();
    DateTimeOffset expires = now + StateLifetime;
    Uri redirectUri = ResolveCallbackUri(requestBaseUri);
    await store.SaveOAuthStateAsync(
      new GarminOAuthStateRecord(
        GarminStore.Hash(state),
        profileId,
        _stateProtector.Protect(verifier),
        redirectUri.ToString(),
        expires),
      now,
      cancellationToken);
    return new GarminConnectStart(provider.CreateAuthorizationUri(profileId, state, challenge, redirectUri), expires);
  }

  public async Task<Guid> CompleteConnectAsync(string code, string state, CancellationToken cancellationToken)
  {
    if (string.IsNullOrWhiteSpace(code) || code.Length > 4096 || string.IsNullOrWhiteSpace(state) || state.Length > 1024)
    {
      throw new InvalidOperationException("The Garmin authorization response is invalid.");
    }
    GarminOAuthStateRecord? saved = await store.ConsumeOAuthStateAsync(GarminStore.Hash(state), timeProvider.GetUtcNow(), cancellationToken);
    if (saved is null) throw new InvalidOperationException("The Garmin authorization request expired or was already used.");
    string verifier;
    try
    {
      verifier = _stateProtector.Unprotect(saved.ProtectedCodeVerifier);
    }
    catch (CryptographicException exception)
    {
      throw new InvalidOperationException("The Garmin authorization request cannot be verified.", exception);
    }

    GarminAuthorizationResult authorization = await provider.ExchangeCodeAsync(code, verifier, new Uri(saved.RedirectUri), cancellationToken);
    var link = new GarminAccountLinkRecord(
      Guid.NewGuid(),
      saved.UserProfileId,
      authorization.Subject,
      authorization.AccountLabel,
      _tokenProtector.Protect(authorization.AccessToken),
      authorization.RefreshToken is null ? null : _tokenProtector.Protect(authorization.RefreshToken),
      authorization.ExpiresAtUtc,
      authorization.Scopes,
      timeProvider.GetUtcNow(),
      null,
      null,
      null,
      1);
    await store.ConnectAsync(link, timeProvider.GetUtcNow(), cancellationToken);
    return saved.UserProfileId;
  }

  public async Task<GarminSessionSyncResult> EnqueueSessionAsync(
    Guid profileId,
    DateOnly date,
    Guid workoutRevisionId,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<GarminSyncDocument> documents = await catalog.BuildSessionAsync(
      profileId, date, workoutRevisionId, cancellationToken);
    int added = await store.EnqueueAsync(profileId, documents, timeProvider.GetUtcNow(), cancellationToken);
    worker.Wake();
    return new GarminSessionSyncResult(added);
  }

  public Task<bool> DisconnectAsync(Guid profileId, CancellationToken cancellationToken) =>
    store.DisconnectAsync(profileId, cancellationToken);

  internal string UnprotectAccessToken(string protectedValue) => _tokenProtector.Unprotect(protectedValue);
  internal string? UnprotectRefreshToken(string? protectedValue) => protectedValue is null ? null : _tokenProtector.Unprotect(protectedValue);
  internal string ProtectToken(string value) => _tokenProtector.Protect(value);

  private Uri ResolveCallbackUri(Uri requestBaseUri)
  {
    if (Uri.TryCreate(options.Value.CallbackUri, UriKind.Absolute, out Uri? configured) && configured.Scheme == Uri.UriSchemeHttps) return configured;
    if (provider is ConfiguredGarminProvider)
    {
      throw new InvalidOperationException("Garmin requires an exact configured HTTPS callback URI.");
    }
    return new Uri(requestBaseUri, "/api/integrations/garmin/callback");
  }

  private static string Base64Url(byte[] value) => Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');
}
