using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Options;
using TreadmillRunner.Gateway.Diagnostics;

namespace TreadmillRunner.Gateway.Security;

public sealed class OperatorAccessService(
  IOptionsMonitor<OperatorAccessOptions> options,
  TimeProvider timeProvider,
  OperationalTelemetry telemetry)
{
  private const int MaximumSessions = 32;
  private const int MaximumPeers = 128;
  private readonly ConcurrentDictionary<string, OperatorSession> sessions = new(StringComparer.Ordinal);
  private readonly ConcurrentDictionary<string, FailureWindow> failures = new(StringComparer.Ordinal);

  public bool Enabled => options.CurrentValue.Enabled;

  public OperatorLoginResult Login(string? passphrase, string peer)
  {
    OperatorAccessOptions current = options.CurrentValue;
    if (!current.Enabled) return new(false, null, null, "Operator access is disabled.", false);

    DateTimeOffset now = timeProvider.GetUtcNow();
    Prune(now);
    if (IsLimited(peer, current, now))
    {
      telemetry.RecordAuthentication("limited");
      return new(false, null, null, "Too many failed attempts. Wait before trying again.", true);
    }

    if (!Verify(passphrase, current.SecretHash))
    {
      RecordFailure(peer, current, now);
      telemetry.RecordAuthentication("failed");
      return new(false, null, null, "The passphrase is invalid.", false);
    }

    failures.TryRemove(peer, out _);
    if (sessions.Count >= MaximumSessions)
    {
      OperatorSession? oldest = sessions.Values.MinBy(static session => session.ExpiresAtUtc);
      if (oldest is not null) sessions.TryRemove(oldest.Token, out _);
    }
    string token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32));
    DateTimeOffset expiresAtUtc = now.AddMinutes(current.SessionMinutes);
    sessions[token] = new OperatorSession(token, expiresAtUtc);
    telemetry.RecordAuthentication("succeeded");
    return new(true, token, expiresAtUtc, null, false);
  }

  public bool IsAuthenticated(string? authorizationHeader, out DateTimeOffset? expiresAtUtc)
  {
    expiresAtUtc = null;
    if (!Enabled) return false;
    string? token = ReadBearerToken(authorizationHeader);
    if (token is null || !sessions.TryGetValue(token, out OperatorSession? session)) return false;
    if (session.ExpiresAtUtc <= timeProvider.GetUtcNow())
    {
      sessions.TryRemove(token, out _);
      return false;
    }
    expiresAtUtc = session.ExpiresAtUtc;
    return true;
  }

  public void Logout(string? authorizationHeader)
  {
    string? token = ReadBearerToken(authorizationHeader);
    if (token is not null) sessions.TryRemove(token, out _);
  }

  internal static bool TryParseSecretHash(
    string? value,
    out int iterations,
    out byte[] salt,
    out byte[] expected)
  {
    iterations = 0;
    salt = [];
    expected = [];
    if (string.IsNullOrWhiteSpace(value)) return false;
    string[] parts = value.Split('$', StringSplitOptions.None);
    if (parts.Length != 4 || !string.Equals(parts[0], "pbkdf2-sha256", StringComparison.Ordinal) ||
        !int.TryParse(parts[1], out iterations) || iterations is < 100_000 or > 2_000_000)
    {
      return false;
    }
    try
    {
      salt = Convert.FromBase64String(parts[2]);
      expected = Convert.FromBase64String(parts[3]);
      return salt.Length is >= 16 and <= 64 && expected.Length == 32;
    }
    catch (FormatException)
    {
      return false;
    }
  }

  private static bool Verify(string? passphrase, string? encodedHash)
  {
    if (string.IsNullOrEmpty(passphrase) || passphrase.Length > 512 ||
        !TryParseSecretHash(encodedHash, out int iterations, out byte[] salt, out byte[] expected))
    {
      return false;
    }
    byte[] actual = Rfc2898DeriveBytes.Pbkdf2(
      Encoding.UTF8.GetBytes(passphrase), salt, iterations, HashAlgorithmName.SHA256, expected.Length);
    return CryptographicOperations.FixedTimeEquals(actual, expected);
  }

  private bool IsLimited(string peer, OperatorAccessOptions current, DateTimeOffset now) =>
    failures.TryGetValue(peer, out FailureWindow? window) &&
    window.StartedAtUtc.AddMinutes(current.FailureWindowMinutes) > now &&
    window.Count >= current.MaximumFailedAttempts;

  private void RecordFailure(string peer, OperatorAccessOptions current, DateTimeOffset now)
  {
    if (failures.Count >= MaximumPeers && !failures.ContainsKey(peer))
    {
      KeyValuePair<string, FailureWindow> oldest = failures.MinBy(static pair => pair.Value.StartedAtUtc);
      failures.TryRemove(oldest.Key, out _);
    }
    failures.AddOrUpdate(
      peer,
      _ => new FailureWindow(now, 1),
      (_, window) => window.StartedAtUtc.AddMinutes(current.FailureWindowMinutes) <= now
        ? new FailureWindow(now, 1)
        : window with { Count = window.Count + 1 });
  }

  private void Prune(DateTimeOffset now)
  {
    foreach ((string token, OperatorSession session) in sessions)
    {
      if (session.ExpiresAtUtc <= now) sessions.TryRemove(token, out _);
    }
  }

  private static string? ReadBearerToken(string? value)
  {
    const string prefix = "Bearer ";
    if (string.IsNullOrWhiteSpace(value) || !value.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return null;
    string token = value[prefix.Length..].Trim();
    return token.Length is >= 32 and <= 128 ? token : null;
  }

  private sealed record OperatorSession(string Token, DateTimeOffset ExpiresAtUtc);
  private sealed record FailureWindow(DateTimeOffset StartedAtUtc, int Count);
}

public sealed record OperatorLoginResult(
  bool Succeeded,
  string? Token,
  DateTimeOffset? ExpiresAtUtc,
  string? Error,
  bool RateLimited);
