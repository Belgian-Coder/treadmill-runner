using System.Collections.Concurrent;
using Microsoft.AspNetCore.DataProtection;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminActivityConnectResult(string State, Guid? ChallengeId, string? AccountLabel, string Message);

public sealed class GarminActivityConnectionService(
  IGarminActivityAdapter adapter,
  IGarminActivityUploadStore store,
  IDataProtectionProvider dataProtectionProvider,
  TimeProvider timeProvider) : IAsyncDisposable
{
  private readonly IDataProtector _protector = dataProtectionProvider.CreateProtector("TreadmillRunner.GarminActivityUpload.TokenStore.v1");
  private readonly ConcurrentDictionary<Guid, PendingChallenge> _challenges = [];
  private readonly SemaphoreSlim _challengeGate = new(1, 1);
  private const int MaximumPendingChallenges = 4;

  public string Unprotect(string protectedTokens) => _protector.Unprotect(protectedTokens);
  public string Protect(string tokens) => _protector.Protect(tokens);

  public Task<GarminActivityConnectResult> BeginAsync(Guid profileId, string email, string password, bool enabled, CancellationToken cancellationToken) =>
    BeginAsync(profileId, email, password, enabled, GarminWatchActivityHandling.PreferWatch, cancellationToken);

  public async Task<GarminActivityConnectResult> BeginAsync(Guid profileId, string email, string password, bool enabled, string watchActivityHandling, CancellationToken cancellationToken)
  {
    await _challengeGate.WaitAsync(cancellationToken);
    try
    {
      await CleanupExpiredAsync();
      foreach ((Guid existingId, PendingChallenge existing) in _challenges)
      {
        if (existing.ProfileId != profileId || !_challenges.TryRemove(existingId, out PendingChallenge? removed)) continue;
        await DisposeChallengeAsync(removed);
      }
      if (_challenges.Count >= MaximumPendingChallenges)
        throw new InvalidOperationException("Too many Garmin verification requests are already pending; complete or wait for one to expire.");

      IGarminAdapterConnectProcess process = await adapter.BeginConnectAsync(email, password, cancellationToken);
      GarminAdapterMessage first;
      try { first = await process.ReadAsync(cancellationToken); }
      catch { await process.DisposeAsync(); throw; }
      if (string.Equals(first.State, "mfa-required", StringComparison.OrdinalIgnoreCase))
      {
        Guid challengeId = Guid.NewGuid();
        var expiryCancellation = new CancellationTokenSource();
        DateTimeOffset expiresAt = timeProvider.GetUtcNow().AddMinutes(5);
        _challenges[challengeId] = new(profileId, enabled, watchActivityHandling, process, expiresAt, expiryCancellation);
        _ = ExpireAsync(challengeId, expiresAt, expiryCancellation.Token);
        return new("MfaRequired", challengeId, null, "Enter the current Garmin verification code. The password is held only by the isolated login process and is never persisted.");
      }
      await using (process)
      {
        return await CompleteAsync(profileId, enabled, watchActivityHandling, first, cancellationToken);
      }
    }
    finally { _challengeGate.Release(); }
  }

  public async Task<GarminActivityConnectResult> CompleteMfaAsync(Guid profileId, Guid challengeId, string code, CancellationToken cancellationToken)
  {
    await _challengeGate.WaitAsync(cancellationToken);
    PendingChallenge challenge;
    try
    {
      await CleanupExpiredAsync();
      if (!_challenges.TryRemove(challengeId, out challenge!)) throw new KeyNotFoundException("The Garmin verification request expired; start again.");
    }
    finally { _challengeGate.Release(); }
    challenge.ExpiryCancellation.Cancel();
    challenge.ExpiryCancellation.Dispose();
    await using (challenge.Process)
    {
      if (challenge.ProfileId != profileId) throw new KeyNotFoundException("The Garmin verification request does not belong to this runner.");
      GarminAdapterMessage message = await challenge.Process.CompleteMfaAsync(code.Trim(), cancellationToken);
      return await CompleteAsync(challenge.ProfileId, challenge.Enabled, challenge.WatchActivityHandling, message, cancellationToken);
    }
  }

  private async Task<GarminActivityConnectResult> CompleteAsync(Guid profileId, bool enabled, string watchActivityHandling, GarminAdapterMessage message, CancellationToken cancellationToken)
  {
    if (!string.Equals(message.State, "connected", StringComparison.OrdinalIgnoreCase) ||
        string.IsNullOrWhiteSpace(message.TokenStore) || string.IsNullOrWhiteSpace(message.AccountLabel))
      return new("Failed", null, null, message.Message ?? "Garmin authentication failed.");
    GarminActivityUploadAccount account = await store.ConnectAsync(
      profileId, message.AccountLabel, _protector.Protect(message.TokenStore), enabled, watchActivityHandling, timeProvider.GetUtcNow(), cancellationToken);
    return new("Connected", null, account.AccountLabel, enabled
      ? "Connected. Completed-activity upload is enabled for this runner."
      : "Connected. Completed-activity upload remains disabled until explicitly enabled.");
  }

  private async Task CleanupExpiredAsync()
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    foreach ((Guid id, PendingChallenge challenge) in _challenges)
      if (challenge.ExpiresAtUtc <= now && _challenges.TryRemove(id, out PendingChallenge? removed))
      {
        await DisposeChallengeAsync(removed);
      }
  }

  private static async Task DisposeChallengeAsync(PendingChallenge challenge)
  {
    challenge.ExpiryCancellation.Cancel();
    challenge.ExpiryCancellation.Dispose();
    await challenge.Process.DisposeAsync();
  }

  private async Task ExpireAsync(Guid challengeId, DateTimeOffset expiresAtUtc, CancellationToken cancellationToken)
  {
    try
    {
      TimeSpan delay = expiresAtUtc - timeProvider.GetUtcNow();
      if (delay > TimeSpan.Zero) await Task.Delay(delay, timeProvider, cancellationToken);
      if (_challenges.TryRemove(challengeId, out PendingChallenge? removed))
      {
        removed.ExpiryCancellation.Dispose();
        await removed.Process.DisposeAsync();
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
  }

  public async ValueTask DisposeAsync()
  {
    foreach ((_, PendingChallenge challenge) in _challenges)
      await DisposeChallengeAsync(challenge);
    _challenges.Clear();
    _challengeGate.Dispose();
  }

  private sealed record PendingChallenge(
    Guid ProfileId,
    bool Enabled,
    string WatchActivityHandling,
    IGarminAdapterConnectProcess Process,
    DateTimeOffset ExpiresAtUtc,
    CancellationTokenSource ExpiryCancellation);
}
