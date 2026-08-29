using System.Security.Cryptography;
using System.Text;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record GarminOAuthStateRecord(
  string StateHash,
  Guid UserProfileId,
  string ProtectedCodeVerifier,
  string RedirectUri,
  DateTimeOffset ExpiresAtUtc);

public sealed record GarminAccountLinkRecord(
  Guid Id,
  Guid UserProfileId,
  string ProviderSubject,
  string AccountLabel,
  string ProtectedAccessToken,
  string? ProtectedRefreshToken,
  DateTimeOffset? AccessTokenExpiresAtUtc,
  string Scopes,
  DateTimeOffset ConnectedAtUtc,
  DateTimeOffset? LastSyncAttemptAtUtc,
  DateTimeOffset? LastSyncSuccessAtUtc,
  string? LastSyncError,
  int Version);

public sealed record GarminSyncDocument(
  string Kind,
  Guid SourceId,
  string SourceVersion,
  string PayloadJson);

public sealed record GarminSyncItemRecord(
  Guid Id,
  Guid UserProfileId,
  Guid AccountLinkId,
  string Kind,
  Guid SourceId,
  string SourceVersion,
  string IdempotencyKey,
  string PayloadJson,
  int AttemptCount);

public sealed record GarminSyncQueueStatus(int Pending, int Failed, int Synced);

public interface IGarminStore
{
  Task SaveOAuthStateAsync(GarminOAuthStateRecord state, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminOAuthStateRecord?> ConsumeOAuthStateAsync(string stateHash, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminAccountLinkRecord?> FindLinkAsync(Guid userProfileId, CancellationToken cancellationToken = default);
  Task<GarminAccountLinkRecord> ConnectAsync(GarminAccountLinkRecord link, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task UpdateTokensAsync(Guid linkId, string protectedAccessToken, string? protectedRefreshToken, DateTimeOffset? expiresAtUtc, string scopes, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> DisconnectAsync(Guid userProfileId, CancellationToken cancellationToken = default);
  Task<int> EnqueueAsync(Guid userProfileId, IReadOnlyList<GarminSyncDocument> documents, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<int> ResetRetryableTerminalFailuresAsync(Guid userProfileId, int maximumAttempts, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminSyncItemRecord?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, int maximumAttempts, CancellationToken cancellationToken = default);
  Task MarkSyncedAsync(Guid itemId, string? remoteId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkFailedAsync(Guid itemId, string error, DateTimeOffset availableAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkTerminalFailureAsync(Guid itemId, string error, int maximumAttempts, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminSyncQueueStatus> GetQueueStatusAsync(Guid userProfileId, CancellationToken cancellationToken = default);
}

public sealed class GarminStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IGarminStore
{
  public async Task SaveOAuthStateAsync(
    GarminOAuthStateRecord state,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(state);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    bool profileExists = await context.UserProfiles.AsNoTracking()
      .AnyAsync(profile => profile.Id == state.UserProfileId && !profile.IsArchived, cancellationToken);
    if (!profileExists)
    {
      throw new KeyNotFoundException($"Profile {state.UserProfileId} was not found.");
    }

    GarminOAuthStateEntity[] stale = (await context.GarminOAuthStates.ToArrayAsync(cancellationToken))
      .Where(candidate => candidate.UserProfileId == state.UserProfileId || candidate.ExpiresAtUtc <= nowUtc)
      .ToArray();
    context.GarminOAuthStates.RemoveRange(stale);
    if (stale.Length > 0) await context.SaveChangesAsync(cancellationToken);
    context.GarminOAuthStates.Add(new GarminOAuthStateEntity
    {
      StateHash = state.StateHash,
      UserProfileId = state.UserProfileId,
      ProtectedCodeVerifier = state.ProtectedCodeVerifier,
      RedirectUri = state.RedirectUri,
      CreatedAtUtc = nowUtc,
      ExpiresAtUtc = state.ExpiresAtUtc,
    });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
  }

  public async Task<GarminOAuthStateRecord?> ConsumeOAuthStateAsync(
    string stateHash,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    GarminOAuthStateEntity? entity = await context.GarminOAuthStates
      .SingleOrDefaultAsync(candidate => candidate.StateHash == stateHash, cancellationToken);
    if (entity is null || entity.ExpiresAtUtc <= nowUtc)
    {
      if (entity is not null)
      {
        context.GarminOAuthStates.Remove(entity);
        await context.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
      }
      return null;
    }

    var result = new GarminOAuthStateRecord(
      entity.StateHash,
      entity.UserProfileId,
      entity.ProtectedCodeVerifier,
      entity.RedirectUri,
      entity.ExpiresAtUtc);
    context.GarminOAuthStates.Remove(entity);
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return result;
  }

  public async Task<GarminAccountLinkRecord?> FindLinkAsync(
    Guid userProfileId,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminAccountLinkEntity? entity = await context.GarminAccountLinks.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.UserProfileId == userProfileId, cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<GarminAccountLinkRecord> ConnectAsync(
    GarminAccountLinkRecord link,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(link);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    bool profileExists = await context.UserProfiles.AsNoTracking()
      .AnyAsync(profile => profile.Id == link.UserProfileId && !profile.IsArchived, cancellationToken);
    if (!profileExists)
    {
      throw new KeyNotFoundException($"Profile {link.UserProfileId} was not found.");
    }

    GarminAccountLinkEntity? current = await context.GarminAccountLinks
      .SingleOrDefaultAsync(candidate => candidate.UserProfileId == link.UserProfileId, cancellationToken);
    GarminAccountLinkEntity? otherOwner = await context.GarminAccountLinks.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.ProviderSubject == link.ProviderSubject && candidate.UserProfileId != link.UserProfileId, cancellationToken);
    if (otherOwner is not null)
    {
      throw new InvalidOperationException("That Garmin account is already connected to another runner profile.");
    }
    if (current is not null && !string.Equals(current.ProviderSubject, link.ProviderSubject, StringComparison.Ordinal))
    {
      throw new InvalidOperationException("Disconnect the current Garmin account before linking a different account.");
    }

    if (current is null)
    {
      current = new GarminAccountLinkEntity
      {
        Id = link.Id,
        UserProfileId = link.UserProfileId,
        ConnectedAtUtc = nowUtc,
        Version = 1,
      };
      context.GarminAccountLinks.Add(current);
    }
    else
    {
      current.Version++;
    }

    current.ProviderSubject = link.ProviderSubject;
    current.AccountLabel = link.AccountLabel;
    current.ProtectedAccessToken = link.ProtectedAccessToken;
    current.ProtectedRefreshToken = link.ProtectedRefreshToken;
    current.AccessTokenExpiresAtUtc = link.AccessTokenExpiresAtUtc;
    current.Scopes = link.Scopes;
    current.UpdatedAtUtc = nowUtc;
    current.LastSyncError = null;
    await context.SaveChangesAsync(cancellationToken);
    return Map(current);
  }

  public async Task UpdateTokensAsync(
    Guid linkId,
    string protectedAccessToken,
    string? protectedRefreshToken,
    DateTimeOffset? expiresAtUtc,
    string scopes,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    int updated = await context.GarminAccountLinks.Where(link => link.Id == linkId)
      .ExecuteUpdateAsync(setters => setters
        .SetProperty(link => link.ProtectedAccessToken, protectedAccessToken)
        .SetProperty(link => link.ProtectedRefreshToken, protectedRefreshToken)
        .SetProperty(link => link.AccessTokenExpiresAtUtc, expiresAtUtc)
        .SetProperty(link => link.Scopes, scopes)
        .SetProperty(link => link.UpdatedAtUtc, nowUtc)
        .SetProperty(link => link.Version, link => link.Version + 1), cancellationToken);
    if (updated != 1)
    {
      throw new KeyNotFoundException($"Garmin account link {linkId} was not found.");
    }
  }

  public async Task<bool> DisconnectAsync(Guid userProfileId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    await context.GarminOAuthStates.Where(state => state.UserProfileId == userProfileId)
      .ExecuteDeleteAsync(cancellationToken);
    int removed = await context.GarminAccountLinks.Where(link => link.UserProfileId == userProfileId)
      .ExecuteDeleteAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return removed == 1;
  }

  public async Task<int> EnqueueAsync(
    Guid userProfileId,
    IReadOnlyList<GarminSyncDocument> documents,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(documents);
    if (documents.Count == 0) return 0;
    string[] keys = documents.Select(document => CreateIdempotencyKey(userProfileId, document)).ToArray();
    for (var attempt = 0; attempt < 2; attempt++)
    {
      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      GarminAccountLinkEntity? link = await context.GarminAccountLinks.AsNoTracking()
        .SingleOrDefaultAsync(candidate => candidate.UserProfileId == userProfileId, cancellationToken);
      if (link is null) return 0;
      HashSet<string> existing = await context.GarminSyncItems.AsNoTracking()
        .Where(item => keys.Contains(item.IdempotencyKey))
        .Select(item => item.IdempotencyKey)
        .ToHashSetAsync(cancellationToken);
      foreach ((GarminSyncDocument document, string key) in documents.Zip(keys))
      {
        if (!existing.Add(key)) continue;
        context.GarminSyncItems.Add(new GarminSyncItemEntity
        {
          Id = Guid.NewGuid(),
          UserProfileId = userProfileId,
          GarminAccountLinkId = link.Id,
          Kind = document.Kind,
          SourceId = document.SourceId,
          SourceVersion = document.SourceVersion,
          IdempotencyKey = key,
          PayloadJson = document.PayloadJson,
          Status = "Pending",
          AvailableAtUtc = nowUtc,
          CreatedAtUtc = nowUtc,
          UpdatedAtUtc = nowUtc,
        });
      }
      try
      {
        return await context.SaveChangesAsync(cancellationToken);
      }
      catch (DbUpdateException exception) when (
        attempt == 0 && exception.InnerException is SqliteException { SqliteErrorCode: 19 })
      {
        // The worker and an explicit Sync now may race. Re-read once after the
        // winning insert commits, then enqueue only any remaining documents.
      }
    }
    return 0;
  }

  public async Task<int> ResetRetryableTerminalFailuresAsync(
    Guid userProfileId,
    int maximumAttempts,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    int reset = await context.GarminSyncItems
      .Where(item => item.UserProfileId == userProfileId && item.Status == "Failed" && item.AttemptCount >= maximumAttempts &&
        (item.LastError == "Garmin Connect is temporarily unavailable." ||
         item.LastError == "The Garmin request timed out." ||
         item.LastError == "Garmin synchronization could not be completed. Reconnect the account if the problem continues."))
      .ExecuteUpdateAsync(setters => setters
        .SetProperty(item => item.Status, "Pending")
        .SetProperty(item => item.AttemptCount, 0)
        .SetProperty(item => item.LastError, (string?)null)
        .SetProperty(item => item.AvailableAtUtc, nowUtc)
        .SetProperty(item => item.LeaseExpiresAtUtc, (DateTimeOffset?)null)
        .SetProperty(item => item.UpdatedAtUtc, nowUtc), cancellationToken);
    if (reset > 0)
    {
      await context.GarminAccountLinks.Where(link => link.UserProfileId == userProfileId)
        .ExecuteUpdateAsync(setters => setters
          .SetProperty(link => link.LastSyncError, (string?)null)
          .SetProperty(link => link.UpdatedAtUtc, nowUtc), cancellationToken);
    }
    return reset;
  }

  public async Task<GarminSyncItemRecord?> LeaseNextAsync(
    DateTimeOffset nowUtc,
    TimeSpan leaseDuration,
    int maximumAttempts,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await context.Database.ExecuteSqlInterpolatedAsync($"""
      UPDATE GarminSyncItems
      SET Status = 'Failed',
          AvailableAtUtc = {nowUtc},
          LeaseExpiresAtUtc = NULL,
          UpdatedAtUtc = {nowUtc}
      WHERE Status = 'InFlight'
        AND LeaseExpiresAtUtc IS NOT NULL
        AND julianday(LeaseExpiresAtUtc) <= julianday({nowUtc})
      """, cancellationToken);

    for (var contentionAttempt = 0; contentionAttempt < 3; contentionAttempt++)
    {
      GarminSyncItemEntity? item = await context.GarminSyncItems
        .FromSqlInterpolated($"""
          SELECT *
          FROM GarminSyncItems
          WHERE (Status = 'Pending' OR Status = 'Failed')
            AND AttemptCount < {maximumAttempts}
            AND julianday(AvailableAtUtc) <= julianday({nowUtc})
          ORDER BY julianday(AvailableAtUtc), julianday(CreatedAtUtc), Id
          LIMIT 1
          """)
        .AsNoTracking()
        .SingleOrDefaultAsync(cancellationToken);
      if (item is null) return null;

      int nextAttempt = item.AttemptCount + 1;
      DateTimeOffset leaseExpiresAtUtc = nowUtc + leaseDuration;
      int changed = await context.Database.ExecuteSqlInterpolatedAsync($"""
        UPDATE GarminSyncItems
        SET Status = 'InFlight',
            AttemptCount = {nextAttempt},
            LeaseExpiresAtUtc = {leaseExpiresAtUtc},
            UpdatedAtUtc = {nowUtc}
        WHERE Id = {item.Id}
          AND (Status = 'Pending' OR Status = 'Failed')
          AND AttemptCount = {item.AttemptCount}
        """, cancellationToken);
      if (changed == 0) continue;

      await context.Database.ExecuteSqlInterpolatedAsync($"""
        UPDATE GarminAccountLinks
        SET LastSyncAttemptAtUtc = {nowUtc}, UpdatedAtUtc = {nowUtc}
        WHERE Id = {item.GarminAccountLinkId}
        """, cancellationToken);
      item.Status = "InFlight";
      item.AttemptCount = nextAttempt;
      item.LeaseExpiresAtUtc = leaseExpiresAtUtc;
      item.UpdatedAtUtc = nowUtc;
      return Map(item);
    }
    return null;
  }

  public async Task MarkSyncedAsync(Guid itemId, string? remoteId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminSyncItemEntity? item = await context.GarminSyncItems
      .SingleOrDefaultAsync(candidate => candidate.Id == itemId && candidate.Status == "InFlight", cancellationToken);
    if (item is null) return; // The profile may have disconnected while the provider request was in flight.
    item.Status = "Synced";
    item.RemoteId = remoteId;
    item.LastError = null;
    item.LeaseExpiresAtUtc = null;
    item.UpdatedAtUtc = nowUtc;
    GarminAccountLinkEntity? link = await context.GarminAccountLinks.SingleOrDefaultAsync(candidate => candidate.Id == item.GarminAccountLinkId, cancellationToken);
    if (link is null) return;
    link.LastSyncSuccessAtUtc = nowUtc;
    link.LastSyncError = null;
    link.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkFailedAsync(
    Guid itemId,
    string error,
    DateTimeOffset availableAtUtc,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminSyncItemEntity? item = await context.GarminSyncItems
      .SingleOrDefaultAsync(candidate => candidate.Id == itemId && candidate.Status == "InFlight", cancellationToken);
    if (item is null) return; // Disconnect removes the account and its queue atomically.
    string bounded = error.Length <= 1000 ? error : error[..1000];
    item.Status = "Failed";
    item.LastError = bounded;
    item.AvailableAtUtc = availableAtUtc;
    item.LeaseExpiresAtUtc = null;
    item.UpdatedAtUtc = nowUtc;
    GarminAccountLinkEntity? link = await context.GarminAccountLinks.SingleOrDefaultAsync(candidate => candidate.Id == item.GarminAccountLinkId, cancellationToken);
    if (link is null) return;
    link.LastSyncError = bounded;
    link.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkTerminalFailureAsync(
    Guid itemId,
    string error,
    int maximumAttempts,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminSyncItemEntity? item = await context.GarminSyncItems
      .SingleOrDefaultAsync(candidate => candidate.Id == itemId && candidate.Status == "InFlight", cancellationToken);
    if (item is null) return;
    string bounded = error.Length <= 1000 ? error : error[..1000];
    item.Status = "Failed";
    item.AttemptCount = Math.Max(item.AttemptCount, maximumAttempts);
    item.LastError = bounded;
    item.AvailableAtUtc = nowUtc;
    item.LeaseExpiresAtUtc = null;
    item.UpdatedAtUtc = nowUtc;
    GarminAccountLinkEntity? link = await context.GarminAccountLinks.SingleOrDefaultAsync(candidate => candidate.Id == item.GarminAccountLinkId, cancellationToken);
    if (link is null) return;
    link.LastSyncError = bounded;
    link.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<GarminSyncQueueStatus> GetQueueStatusAsync(Guid userProfileId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var counts = await context.GarminSyncItems.AsNoTracking()
      .Where(item => item.UserProfileId == userProfileId)
      .GroupBy(item => item.Status)
      .Select(group => new { Status = group.Key, Count = group.Count() })
      .ToDictionaryAsync(item => item.Status, item => item.Count, cancellationToken);
    return new GarminSyncQueueStatus(
      counts.GetValueOrDefault("Pending") + counts.GetValueOrDefault("InFlight"),
      counts.GetValueOrDefault("Failed"),
      counts.GetValueOrDefault("Synced"));
  }

  public static string Hash(string value) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));

  private static string CreateIdempotencyKey(Guid userProfileId, GarminSyncDocument document) =>
    Hash($"garmin|{userProfileId:N}|{document.Kind}|{document.SourceId:N}|{document.SourceVersion}");

  private static GarminAccountLinkRecord Map(GarminAccountLinkEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.ProviderSubject,
    entity.AccountLabel,
    entity.ProtectedAccessToken,
    entity.ProtectedRefreshToken,
    entity.AccessTokenExpiresAtUtc,
    entity.Scopes,
    entity.ConnectedAtUtc,
    entity.LastSyncAttemptAtUtc,
    entity.LastSyncSuccessAtUtc,
    entity.LastSyncError,
    entity.Version);

  private static GarminSyncItemRecord Map(GarminSyncItemEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.GarminAccountLinkId,
    entity.Kind,
    entity.SourceId,
    entity.SourceVersion,
    entity.IdempotencyKey,
    entity.PayloadJson,
    entity.AttemptCount);
}
