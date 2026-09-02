using System.Security.Cryptography;
using System.Text;
using System.Globalization;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Infrastructure.Persistence;

public static class GarminWatchActivityHandling
{
  public const string PreferWatch = "PreferWatch";
  public const string MergeAndReplace = "MergeAndReplace";

  public static bool IsValid(string value) => value is PreferWatch or MergeAndReplace;
}

public sealed record GarminActivityUploadAccount(
  Guid Id,
  Guid UserProfileId,
  string AccountLabel,
  string ProtectedTokenStore,
  bool Enabled,
  string WatchActivityHandling,
  string State,
  DateTimeOffset ConnectedAtUtc,
  DateTimeOffset? UploadFromUtc,
  DateTimeOffset? LastUploadSuccessAtUtc,
  string? LastError,
  int Version);

public sealed record GarminActivityUploadJob(
  Guid Id,
  Guid UserProfileId,
  Guid AccountId,
  Guid WorkoutSessionId,
  string Status,
  int AttemptCount,
  DateTimeOffset? LeaseExpiresAtUtc,
  string? RemoteId,
  string OperationPhase,
  string? MatchedRemoteId,
  string? ReplacementRemoteId,
  string? MatchEvidence,
  string? FailureKind,
  bool CanRetry,
  DateTimeOffset? RetryAtUtc,
  string? WorkoutTitle,
  DateTimeOffset? StartedAtUtc,
  double? DurationSeconds,
  string? LastError,
  DateTimeOffset UpdatedAtUtc,
  DateTimeOffset? AcknowledgedAtUtc);

public sealed record GarminActivityUploadStatus(
  Guid ProfileId,
  bool Connected,
  bool Enabled,
  string? WatchActivityHandling,
  string? AccountLabel,
  string State,
  int Pending,
  int Confirmed,
  int FoundInGarmin,
  int Failed,
  int Unknown,
  int ReviewRequired,
  DateTimeOffset? LastSuccessAtUtc,
  string? LastError,
  int? Version);

public interface IGarminActivityUploadStore
{
  Task<GarminActivityUploadStatus> GetStatusAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<GarminActivityUploadJob>> ListJobsAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob?> FindBySessionAsync(Guid sessionId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<GarminActivityUploadJob>> ListIncompleteReplacementJobsAsync(CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount?> FindAccountAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount> ConnectAsync(Guid profileId, string accountLabel, string protectedTokenStore, bool enabled, string watchActivityHandling, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount> ConnectAsync(Guid profileId, string accountLabel, string protectedTokenStore, bool enabled, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    ConnectAsync(profileId, accountLabel, protectedTokenStore, enabled, GarminWatchActivityHandling.PreferWatch, nowUtc, cancellationToken);
  Task<GarminActivityUploadAccount> SetSettingsAsync(Guid profileId, bool enabled, string watchActivityHandling, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> DisconnectAsync(Guid profileId, int expectedVersion, CancellationToken cancellationToken = default);
  Task<int> ReconcileCompletedSessionsAsync(DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> EnqueueSystemTestAsync(Guid profileId, Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> ReprocessLegacyConfirmedForMergeAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default);
  Task MarkConfirmedAsync(Guid jobId, string? remoteId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task RecordHistoricalReconciliationAsync(Guid jobId, string remoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkUploadStartedAsync(Guid jobId, string operationPhase, DateTimeOffset expectedLeaseExpiresAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkReplacementUploadStartedAsync(Guid jobId, string matchedRemoteId, string evidence, DateTimeOffset expectedLeaseExpiresAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkWatchFoundAsync(Guid jobId, string remoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkReplacementUploadedAsync(Guid jobId, string matchedRemoteId, string replacementRemoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkReplacementAwaitingResolutionAsync(Guid jobId, string matchedRemoteId, string evidence, string error, string? protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteReplacementResolutionCheckAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkReplacementResolvedAsync(Guid jobId, string matchedRemoteId, string replacementRemoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task ContinueReplacementDuplicateCleanupAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteReplacementDuplicateCleanupAsync(Guid jobId, bool originalStillExists, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task<bool> ResumeIncompleteReplacementAsync(Guid jobId, string matchedRemoteId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkOriginalAwaitingResolutionAsync(Guid jobId, string error, string? protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteOriginalResolutionCheckAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkOriginalResolvedAsync(Guid jobId, string originalRemoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkLocalAwaitingResolutionAsync(Guid jobId, string error, string? protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteLocalResolutionCheckAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkLocalResolvedAsync(Guid jobId, string localRemoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task ContinueGeneratedCopyCleanupAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteUndoAsync(Guid jobId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkOriginalDeletedAwaitingResyncAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task CompleteResyncCheckAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkReplacementUncertainAsync(Guid jobId, string matchedRemoteId, string evidence, string error, string? protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkFailedAsync(Guid jobId, string error, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkRejectedAsync(Guid jobId, string failureKind, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkProviderUnavailableAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkUnknownAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task MarkReviewRequiredAsync(
    Guid jobId,
    string? candidateRemoteId,
    string evidence,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null);
  Task<bool> RetryFailedAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> DismissAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> AcknowledgeFoundInGarminAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> RetryUnknownVerifiedAbsentAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> StartHistoricalRecoveryAsync(
    Guid jobId,
    Guid profileId,
    string action,
    string matchedRemoteId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
}

public sealed class GarminActivityUploadStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IGarminActivityUploadStore
{
  // Historical recovery is a guarded, user-confirmed mutation.  Keep the
  // receipt check and job update in one process-wide critical section so two
  // concurrent requests with the same operation ID cannot both observe the
  // missing receipt and race SQLite's single-writer boundary.  The second
  // request re-enters the normal receipt replay path after the first commits.
  private static readonly SemaphoreSlim HistoricalRecoveryGate = new(1, 1);

  public Task<GarminActivityUploadAccount> ConnectAsync(Guid profileId, string accountLabel, string protectedTokenStore, bool enabled, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    ConnectAsync(profileId, accountLabel, protectedTokenStore, enabled, GarminWatchActivityHandling.PreferWatch, nowUtc, cancellationToken);

  public async Task<IReadOnlyList<GarminActivityUploadJob>> ListJobsAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity[] jobs = await context.GarminActivityUploadJobs
      .FromSqlInterpolated($"SELECT * FROM GarminActivityUploadJobs WHERE UserProfileId = {profileId} ORDER BY CreatedAtUtc DESC LIMIT 25")
      .AsNoTracking()
      .ToArrayAsync(cancellationToken);
    Guid[] sessionIds = jobs.Select(item => item.WorkoutSessionId).ToArray();
    Dictionary<Guid, WorkoutSessionEntity> sessions = await context.WorkoutSessions.AsNoTracking()
      .Where(item => sessionIds.Contains(item.Id))
      .ToDictionaryAsync(item => item.Id, cancellationToken);
    return Array.AsReadOnly(jobs.Select(item => sessions.TryGetValue(item.WorkoutSessionId, out WorkoutSessionEntity? session)
      ? Map(item, session)
      : Map(item)).ToArray());
  }

  public async Task<GarminActivityUploadJob?> FindBySessionAsync(
    Guid sessionId,
    CancellationToken cancellationToken = default)
  {
    if (sessionId == Guid.Empty) throw new ArgumentException("A session ID is required.", nameof(sessionId));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? entity = await context.GarminActivityUploadJobs
      .FromSqlInterpolated($"SELECT * FROM GarminActivityUploadJobs WHERE WorkoutSessionId = {sessionId} ORDER BY UpdatedAtUtc DESC, Id DESC LIMIT 1")
      .AsNoTracking()
      .SingleOrDefaultAsync(cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<IReadOnlyList<GarminActivityUploadJob>> ListIncompleteReplacementJobsAsync(
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity[] entities = await context.GarminActivityUploadJobs
      .AsNoTracking()
      .Where(item => item.Account.Enabled && item.Account.State == "Connected" &&
        item.Account.WatchActivityHandling == GarminWatchActivityHandling.MergeAndReplace &&
        item.RemoteId == null && item.ReplacementRemoteId == null &&
        item.Status == "Unknown" && item.OperationPhase == "ReplacementUpload" && item.MatchedRemoteId != null)
      .OrderBy(item => item.Id)
      .Take(25)
      .ToArrayAsync(cancellationToken);
    return Array.AsReadOnly(entities.Select(entity => Map(entity)).ToArray());
  }

  public async Task<GarminActivityUploadStatus> GetStatusAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity? account = await context.GarminActivityUploadAccounts.AsNoTracking()
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (account is null || account.State == "Disconnected") return new(profileId, false, false, null, null, "Disconnected", 0, 0, 0, 0, 0, 0, null, null, account?.Version);
    var counts = await context.GarminActivityUploadJobs.AsNoTracking()
      .Where(item => item.UserProfileId == profileId)
      .GroupBy(item => item.Status)
      .Select(group => new { Status = group.Key, Count = group.Count() })
      .ToDictionaryAsync(item => item.Status, item => item.Count, cancellationToken);
    int Count(string status) => counts.GetValueOrDefault(status);
    return new(
      profileId,
      true,
      account.Enabled,
      account.WatchActivityHandling,
      account.AccountLabel,
      account.State,
      Count("Pending") + Count("InFlight"),
      Count("Confirmed"),
      Count("FoundInGarmin"),
      Count("Failed"),
      Count("Unknown"),
      Count("ReviewRequired"),
      account.LastUploadSuccessAtUtc,
      account.LastError,
      account.Version);
  }

  public async Task<GarminActivityUploadAccount?> FindAccountAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity? entity = await context.GarminActivityUploadAccounts.AsNoTracking()
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<GarminActivityUploadAccount> ConnectAsync(
    Guid profileId,
    string accountLabel,
    string protectedTokenStore,
    bool enabled,
    string watchActivityHandling,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (!GarminWatchActivityHandling.IsValid(watchActivityHandling))
      throw new ArgumentException("A supported watch activity handling mode is required.", nameof(watchActivityHandling));
    accountLabel = accountLabel.Trim();
    if (accountLabel.Length is < 1 or > 160) throw new ArgumentOutOfRangeException(nameof(accountLabel));
    if (string.IsNullOrWhiteSpace(protectedTokenStore) || protectedTokenStore.Length > 32768) throw new ArgumentOutOfRangeException(nameof(protectedTokenStore));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    bool profileExists = await context.UserProfiles.AnyAsync(item => item.Id == profileId && !item.IsArchived, cancellationToken);
    if (!profileExists) throw new KeyNotFoundException("The runner profile does not exist or is archived.");
    GarminActivityUploadAccountEntity? entity = await context.GarminActivityUploadAccounts
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (entity is null)
    {
      entity = new GarminActivityUploadAccountEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = profileId,
        AccountLabel = accountLabel,
        ProtectedTokenStore = protectedTokenStore,
        Enabled = enabled,
        WatchActivityHandling = watchActivityHandling,
        State = "Connected",
        ConnectedAtUtc = nowUtc,
        UploadFromUtc = enabled ? nowUtc : null,
        UpdatedAtUtc = nowUtc,
        Version = 1,
      };
      context.GarminActivityUploadAccounts.Add(entity);
    }
    else
    {
      entity.AccountLabel = accountLabel;
      entity.ProtectedTokenStore = protectedTokenStore;
      entity.Enabled = enabled;
      entity.WatchActivityHandling = watchActivityHandling;
      if (enabled && entity.UploadFromUtc is null) entity.UploadFromUtc = nowUtc;
      entity.State = "Connected";
      entity.LastError = null;
      entity.UpdatedAtUtc = nowUtc;
      entity.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task<GarminActivityUploadAccount> SetSettingsAsync(Guid profileId, bool enabled, string watchActivityHandling, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    if (!GarminWatchActivityHandling.IsValid(watchActivityHandling))
      throw new ArgumentException("A supported watch activity handling mode is required.", nameof(watchActivityHandling));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity entity = await context.GarminActivityUploadAccounts
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("No unsupported Garmin upload account is connected for this runner.");
    EnsureVersion(entity.Version, expectedVersion);
    if (enabled && !entity.Enabled) entity.UploadFromUtc = nowUtc;
    entity.Enabled = enabled;
    entity.WatchActivityHandling = watchActivityHandling;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public Task<GarminActivityUploadAccount> SetEnabledAsync(Guid profileId, bool enabled, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    SetSettingsAsync(profileId, enabled, GarminWatchActivityHandling.PreferWatch, expectedVersion, nowUtc, cancellationToken);

  public async Task<bool> DisconnectAsync(Guid profileId, int expectedVersion, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using Microsoft.EntityFrameworkCore.Storage.IDbContextTransaction transaction = await context.Database.BeginTransactionAsync(System.Data.IsolationLevel.Serializable, cancellationToken);
    GarminActivityUploadAccountEntity? entity = await context.GarminActivityUploadAccounts
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (entity is null) return false;
    EnsureVersion(entity.Version, expectedVersion);
    await context.GarminActivityUploadAccounts
      .Where(item => item.Id == entity.Id && item.Version == expectedVersion)
      .ExecuteUpdateAsync(setters => setters.SetProperty(item => item.UpdatedAtUtc, item => item.UpdatedAtUtc), cancellationToken);
    if (await context.GarminActivityUploadJobs.AnyAsync(item => item.GarminActivityUploadAccountId == entity.Id && item.Status == "InFlight", cancellationToken))
      throw new InvalidOperationException("An activity upload has already started and cannot be cancelled safely. Wait for its outcome, then disconnect.");
    context.GarminActivityUploadAccounts.Remove(entity);
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return true;
  }

  public async Task<int> ReconcileCompletedSessionsAsync(DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity[] accounts = await context.GarminActivityUploadAccounts.AsNoTracking()
      .Where(item => item.Enabled && item.State == "Connected" && item.UploadFromUtc != null)
      .ToArrayAsync(cancellationToken);
    var candidates = new List<(Guid Session, Guid Profile, Guid Account, DateTimeOffset EndedAt)>();
    foreach (GarminActivityUploadAccountEntity account in accounts)
    {
      int remaining = 100 - candidates.Count;
      if (remaining <= 0) break;
      string watermark = account.UploadFromUtc!.Value.ToString("yyyy-MM-dd HH:mm:ss.fffffffzzz", CultureInfo.InvariantCulture);
      WorkoutSessionEntity[] sessions = await context.WorkoutSessions
        .FromSqlInterpolated($"SELECT s.* FROM WorkoutSessions AS s WHERE s.UserProfileId = {account.UserProfileId} AND s.SessionOrigin <> 'SystemTest' AND s.StartedAtUtc IS NOT NULL AND s.EndedAtUtc IS NOT NULL AND s.EndedAtUtc >= {watermark} AND (s.State = 'Completed' OR s.State = 'Stopped') AND NOT EXISTS (SELECT 1 FROM GarminActivityUploadJobs AS j WHERE j.WorkoutSessionId = s.Id) ORDER BY s.EndedAtUtc LIMIT {remaining}")
        .AsNoTracking()
        .ToArrayAsync(cancellationToken);
      candidates.AddRange(sessions.Select(session => (session.Id, session.UserProfileId, account.Id, session.EndedAtUtc!.Value)));
    }
    foreach (var candidate in candidates)
    {
      string keyMaterial = $"garmin-fit-v1|{candidate.Profile:D}|{candidate.Session:D}";
      context.GarminActivityUploadJobs.Add(new GarminActivityUploadJobEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = candidate.Profile,
        GarminActivityUploadAccountId = candidate.Account,
        WorkoutSessionId = candidate.Session,
        IdempotencyKey = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(keyMaterial))),
        Status = "Pending",
        OperationPhase = "WatchSearch",
        AvailableAtUtc = candidate.EndedAt.AddMinutes(5),
        CreatedAtUtc = nowUtc,
        UpdatedAtUtc = nowUtc,
      });
    }
    try { return await context.SaveChangesAsync(cancellationToken); }
    catch (DbUpdateException exception) when (exception.InnerException is SqliteException { SqliteExtendedErrorCode: 2067 }) { return 0; }
  }

  public async Task<GarminActivityUploadJob> EnqueueSystemTestAsync(
    Guid profileId,
    Guid sessionId,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? existing = await context.GarminActivityUploadJobs
      .SingleOrDefaultAsync(item => item.WorkoutSessionId == sessionId, cancellationToken);
    if (existing is not null)
    {
      WorkoutSessionEntity? existingSession = await context.WorkoutSessions.AsNoTracking()
        .SingleOrDefaultAsync(item => item.Id == sessionId, cancellationToken);
      return Map(existing, existingSession);
    }
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId && item.Enabled && item.State == "Connected", cancellationToken)
      ?? throw new InvalidOperationException("Garmin activity upload must be connected and enabled for this runner.");
    WorkoutSessionEntity session = await context.WorkoutSessions.AsNoTracking()
      .SingleOrDefaultAsync(item => item.Id == sessionId && item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("The synthetic Garmin test session was not found.");
    if (session.SessionOrigin != nameof(SessionOrigin.SystemTest) || session.State != nameof(SessionState.Completed))
      throw new InvalidOperationException("Only a completed system-test session can use the explicit Garmin test queue.");
    string keyMaterial = $"garmin-fit-test-v1|{profileId:D}|{sessionId:D}";
    var entity = new GarminActivityUploadJobEntity
    {
      Id = Guid.NewGuid(),
      UserProfileId = profileId,
      GarminActivityUploadAccountId = account.Id,
      WorkoutSessionId = sessionId,
      IdempotencyKey = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(keyMaterial))),
      Status = "Pending",
      OperationPhase = "Upload",
      AvailableAtUtc = nowUtc,
      CreatedAtUtc = nowUtc,
      UpdatedAtUtc = nowUtc,
    };
    context.GarminActivityUploadJobs.Add(entity);
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity, session);
  }

  public async Task<GarminActivityUploadJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await context.Database.ExecuteSqlInterpolatedAsync($"""
      UPDATE GarminActivityUploadJobs
      SET Status = 'Pending',
          AttemptCount = max(0, AttemptCount - 1),
          AvailableAtUtc = {nowUtc},
          LastError = NULL,
          LeaseExpiresAtUtc = NULL,
          UpdatedAtUtc = {nowUtc}
      WHERE Status = 'InFlight'
        AND OperationPhase IN ('WatchSearch', 'VerifyResync', 'ResolveReplacement', 'EnsureReplacement', 'DeleteReplacementDuplicates', 'ResolveOriginal', 'ResolveRestoredOriginal', 'ResolveLocalSource', 'ResolveRestoredLocal', 'DeleteGeneratedCopies')
        AND LeaseExpiresAtUtc IS NOT NULL
        AND julianday(LeaseExpiresAtUtc) <= julianday({nowUtc})
      """, cancellationToken);
    const string uncertainMutation = "The service restarted or timed out after a Garmin mutation may have begun; the outcome is unknown and will not be retried automatically.";
    await context.Database.ExecuteSqlInterpolatedAsync($"""
      UPDATE GarminActivityUploadJobs
      SET Status = 'Unknown',
          LastError = {uncertainMutation},
          LeaseExpiresAtUtc = NULL,
          UpdatedAtUtc = {nowUtc}
      WHERE Status = 'InFlight'
        AND OperationPhase NOT IN ('WatchSearch', 'VerifyResync', 'ResolveReplacement', 'EnsureReplacement', 'DeleteReplacementDuplicates', 'ResolveOriginal', 'ResolveRestoredOriginal', 'ResolveLocalSource', 'ResolveRestoredLocal', 'DeleteGeneratedCopies')
        AND LeaseExpiresAtUtc IS NOT NULL
        AND julianday(LeaseExpiresAtUtc) <= julianday({nowUtc})
      """, cancellationToken);
    for (var contentionAttempt = 0; contentionAttempt < 3; contentionAttempt++)
    {
      GarminActivityUploadJobEntity? candidate = await context.GarminActivityUploadJobs
        .FromSqlInterpolated($"""
          SELECT job.*
          FROM GarminActivityUploadJobs AS job
          WHERE job.Status = 'Pending'
            AND job.AttemptCount < 3
            AND julianday(job.AvailableAtUtc) <= julianday({nowUtc})
            AND EXISTS (
              SELECT 1
              FROM GarminActivityUploadAccounts AS account
              WHERE account.Id = job.GarminActivityUploadAccountId
                AND account.Enabled = 1
                AND account.State = 'Connected')
          ORDER BY julianday(job.AvailableAtUtc), julianday(job.CreatedAtUtc), job.Id
          LIMIT 1
          """)
        .AsNoTracking()
        .SingleOrDefaultAsync(cancellationToken);
      if (candidate is null) return null;
      int nextAttempt = candidate.AttemptCount + 1;
      DateTimeOffset leaseExpiresAtUtc = nowUtc.Add(leaseDuration);
      int changed = await context.Database.ExecuteSqlInterpolatedAsync($"""
        UPDATE GarminActivityUploadJobs
        SET Status = 'InFlight',
            AttemptCount = {nextAttempt},
            LeaseExpiresAtUtc = {leaseExpiresAtUtc},
            UpdatedAtUtc = {nowUtc}
        WHERE Id = {candidate.Id}
          AND Status = 'Pending'
          AND AttemptCount = {candidate.AttemptCount}
          AND EXISTS (
            SELECT 1
            FROM GarminActivityUploadAccounts AS account
            WHERE account.Id = GarminActivityUploadJobs.GarminActivityUploadAccountId
              AND account.Enabled = 1
              AND account.State = 'Connected')
        """, cancellationToken);
      if (changed == 0) continue;
      candidate.Status = "InFlight";
      candidate.AttemptCount = nextAttempt;
      candidate.LeaseExpiresAtUtc = leaseExpiresAtUtc;
      candidate.UpdatedAtUtc = nowUtc;
      return Map(candidate);
    }
    return null;
  }

  public async Task MarkConfirmedAsync(Guid jobId, string? remoteId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Confirmed"; job.RemoteId = remoteId; job.FailureKind = null; job.LastError = null; job.LeaseExpiresAtUtc = null; job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore; account.State = "Connected"; account.LastError = null; account.LastUploadSuccessAtUtc = nowUtc; account.UpdatedAtUtc = nowUtc; account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkOriginalDeletedAwaitingResyncAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "VerifyResync";
    job.RemoteId = job.ReplacementRemoteId;
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddMinutes(2);
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.LastUploadSuccessAtUtc = nowUtc;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteResyncCheckAsync(Guid jobId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (job.AttemptCount >= 3)
    {
      job.Status = "Confirmed";
      job.RemoteId = job.ReplacementRemoteId;
    }
    else
    {
      int delayMinutes = job.AttemptCount == 1 ? 10 : 15;
      job.Status = "Pending";
      job.OperationPhase = "VerifyResync";
      job.AvailableAtUtc = nowUtc.AddMinutes(delayMinutes);
    }
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task RecordHistoricalReconciliationAsync(Guid jobId, string remoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await context.GarminActivityUploadJobs.SingleAsync(
      item => item.Id == jobId && item.Status == "Confirmed", cancellationToken);
    job.RemoteId = BoundId(remoteId);
    job.MatchedRemoteId = BoundId(remoteId);
    job.MatchEvidence = Bound(evidence);
    job.FailureKind = null;
    job.LastError = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkUploadStartedAsync(
    Guid jobId,
    string operationPhase,
    DateTimeOffset expectedLeaseExpiresAtUtc,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (operationPhase is not ("Upload" or "ReplacementUpload" or "RestoreOriginal" or "RestoreLocal" or "DeleteReplacementDuplicate" or "DeleteGeneratedCopy" or "DeleteOriginal" or "DeleteResyncedOriginal"))
      throw new ArgumentException("A supported Garmin mutation phase is required.", nameof(operationPhase));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredMutationLeaseAsync(
      context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.OperationPhase = operationPhase;
    job.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkReplacementUploadStartedAsync(
    Guid jobId,
    string matchedRemoteId,
    string evidence,
    DateTimeOffset expectedLeaseExpiresAtUtc,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredMutationLeaseAsync(
      context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.OperationPhase = "ReplacementUpload";
    job.MatchedRemoteId = BoundId(matchedRemoteId);
    job.MatchEvidence = Bound(evidence);
    job.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkWatchFoundAsync(Guid jobId, string remoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "FoundInGarmin";
    job.RemoteId = BoundId(remoteId);
    job.MatchedRemoteId = BoundId(remoteId);
    job.MatchEvidence = Bound(evidence);
    job.FailureKind = "watch-match";
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.AcknowledgedAtUtc = nowUtc;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkReplacementUploadedAsync(Guid jobId, string matchedRemoteId, string replacementRemoteId, string evidence, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    if (string.Equals(matchedRemoteId, replacementRemoteId, StringComparison.Ordinal))
      throw new InvalidOperationException("The replacement activity must have a distinct Garmin activity ID.");
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "DeleteOriginal";
    job.MatchedRemoteId = BoundId(matchedRemoteId);
    job.ReplacementRemoteId = BoundId(replacementRemoteId);
    job.MatchEvidence = Bound(evidence);
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkReplacementAwaitingResolutionAsync(
    Guid jobId,
    string matchedRemoteId,
    string evidence,
    string error,
    string? protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "ResolveReplacement";
    job.MatchedRemoteId = BoundId(matchedRemoteId);
    job.ReplacementRemoteId = null;
    job.MatchEvidence = Bound(evidence);
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddMinutes(2);
    job.FailureKind = null;
    job.LastError = Bound(error);
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    if (!string.IsNullOrWhiteSpace(protectedTokenStore))
    {
      GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
        item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
      account.ProtectedTokenStore = protectedTokenStore;
      account.UpdatedAtUtc = nowUtc;
      account.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteReplacementResolutionCheckAsync(
    Guid jobId,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.OperationPhase = "ResolveReplacement";
    if (job.AttemptCount >= 3)
    {
      job.Status = "Unknown";
      job.FailureKind = "transport";
      job.LastError = "Garmin accepted the merged activity without returning its ID, and the read-only checks could not identify it. No second upload was sent; use Check Garmin again later.";
    }
    else
    {
      job.Status = "Pending";
      job.AvailableAtUtc = nowUtc.AddMinutes(job.AttemptCount == 1 ? 5 : 10);
      job.FailureKind = null;
      job.LastError = "Garmin accepted the merged activity; waiting for it to appear before removing any duplicate.";
    }
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkReplacementResolvedAsync(
    Guid jobId,
    string matchedRemoteId,
    string replacementRemoteId,
    string evidence,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    if (string.Equals(matchedRemoteId, replacementRemoteId, StringComparison.Ordinal))
      throw new InvalidOperationException("The resolved replacement activity must have a distinct Garmin activity ID.");
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "DeleteReplacementDuplicates";
    job.MatchedRemoteId = BoundId(matchedRemoteId);
    job.ReplacementRemoteId = BoundId(replacementRemoteId);
    job.MatchEvidence = Bound(evidence);
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc;
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task ContinueReplacementDuplicateCleanupAsync(
    Guid jobId,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "DeleteReplacementDuplicates";
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddSeconds(2);
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteReplacementDuplicateCleanupAsync(
    Guid jobId,
    bool originalStillExists,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.ReplacementRemoteId))
      throw new InvalidOperationException("Replacement identity is incomplete; duplicate cleanup cannot finish.");
    job.Status = "Pending";
    job.OperationPhase = originalStillExists ? "DeleteOriginal" : "VerifyResync";
    job.RemoteId = originalStillExists ? null : job.ReplacementRemoteId;
    job.AttemptCount = 0;
    job.AvailableAtUtc = originalStillExists ? nowUtc : nowUtc.AddMinutes(2);
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkOriginalAwaitingResolutionAsync(
    Guid jobId,
    string error,
    string? protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "ResolveRestoredOriginal";
    job.RemoteId = null;
    job.ReplacementRemoteId = null;
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddMinutes(2);
    job.FailureKind = null;
    job.LastError = Bound(error);
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    if (!string.IsNullOrWhiteSpace(protectedTokenStore))
    {
      GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
        item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
      account.ProtectedTokenStore = protectedTokenStore;
      account.UpdatedAtUtc = nowUtc;
      account.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteOriginalResolutionCheckAsync(
    Guid jobId,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.OperationPhase = "ResolveRestoredOriginal";
    if (job.AttemptCount >= 3)
    {
      job.Status = "Unknown";
      job.FailureKind = "transport";
      job.LastError = "Garmin accepted the original activity restore without returning its ID, and the read-only checks could not identify it. No second restore upload was sent.";
    }
    else
    {
      job.Status = "Pending";
      job.AvailableAtUtc = nowUtc.AddMinutes(job.AttemptCount == 1 ? 5 : 10);
      job.FailureKind = null;
      job.LastError = "Waiting for Garmin to expose the restored original before removing any generated copies.";
    }
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkOriginalResolvedAsync(
    Guid jobId,
    string originalRemoteId,
    string evidence,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    // During Undo, MatchedRemoteId is the retained watch-original.  RemoteId
    // is reserved for the retained plain local TreadmillRunner activity and
    // is populated by the explicit local-source resolution phase.
    job.OperationPhase = "ResolveLocalSource";
    job.MatchedRemoteId = BoundId(originalRemoteId);
    job.RemoteId = null;
    job.ReplacementRemoteId = null;
    job.MatchEvidence = Bound(evidence);
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc;
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkLocalAwaitingResolutionAsync(
    Guid jobId,
    string error,
    string? protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId))
      throw new InvalidOperationException("The retained watch-original must be identified before resolving the local activity.");
    job.Status = "Pending";
    job.OperationPhase = "ResolveRestoredLocal";
    job.RemoteId = null;
    job.ReplacementRemoteId = null;
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddMinutes(2);
    job.FailureKind = null;
    job.LastError = Bound(error);
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    if (!string.IsNullOrWhiteSpace(protectedTokenStore))
    {
      GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
        item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
      account.ProtectedTokenStore = protectedTokenStore;
      account.UpdatedAtUtc = nowUtc;
      account.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteLocalResolutionCheckAsync(
    Guid jobId,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId))
      throw new InvalidOperationException("The retained watch-original must be identified before resolving the local activity.");
    job.OperationPhase = "ResolveRestoredLocal";
    if (job.AttemptCount >= 3)
    {
      job.Status = "Unknown";
      job.FailureKind = "transport";
      job.LastError = "Garmin accepted the local activity restore without returning its ID, and the read-only checks could not identify it. No second local upload was sent.";
    }
    else
    {
      job.Status = "Pending";
      job.AvailableAtUtc = nowUtc.AddMinutes(job.AttemptCount == 1 ? 5 : 10);
      job.FailureKind = null;
      job.LastError = "Waiting for Garmin to expose the restored local activity before removing generated copies.";
    }
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkLocalResolvedAsync(
    Guid jobId,
    string localRemoteId,
    string evidence,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId))
      throw new InvalidOperationException("The retained watch-original must be identified before resolving the local activity.");
    if (string.Equals(job.MatchedRemoteId, localRemoteId, StringComparison.Ordinal))
      throw new InvalidOperationException("The retained watch-original and local activity must have distinct Garmin activity IDs.");
    job.Status = "Pending";
    job.OperationPhase = "DeleteGeneratedCopies";
    job.RemoteId = BoundId(localRemoteId);
    job.ReplacementRemoteId = null;
    job.MatchEvidence = Bound(evidence);
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc;
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task ContinueGeneratedCopyCleanupAsync(
    Guid jobId,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Pending";
    job.OperationPhase = "DeleteGeneratedCopies";
    job.AttemptCount = 0;
    job.AvailableAtUtc = nowUtc.AddSeconds(2);
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task CompleteUndoAsync(
    Guid jobId,
    string evidence,
    string protectedTokenStore,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId) || string.IsNullOrWhiteSpace(job.RemoteId))
      throw new InvalidOperationException("Both the retained watch-original and plain local Garmin activities must be identified before undo can finish.");
    if (string.Equals(job.MatchedRemoteId, job.RemoteId, StringComparison.Ordinal))
      throw new InvalidOperationException("The retained watch-original and plain local Garmin activities must have distinct IDs.");
    job.Status = "Confirmed";
    job.OperationPhase = "UndoComplete";
    job.ReplacementRemoteId = null;
    job.MatchEvidence = Bound(evidence);
    job.FailureKind = null;
    job.LastError = null;
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(
      item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore;
    account.State = "Connected";
    account.LastError = null;
    account.LastUploadSuccessAtUtc = nowUtc;
    account.UpdatedAtUtc = nowUtc;
    account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task MarkReplacementUncertainAsync(Guid jobId, string matchedRemoteId, string evidence, string error, string? protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = "Unknown";
    job.OperationPhase = "ReplacementUpload";
    job.MatchedRemoteId = BoundId(matchedRemoteId);
    job.MatchEvidence = Bound(evidence);
    job.FailureKind = "transport";
    job.LastError = Bound(error);
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    if (!string.IsNullOrWhiteSpace(protectedTokenStore))
    {
      GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
      account.ProtectedTokenStore = protectedTokenStore;
      account.UpdatedAtUtc = nowUtc;
      account.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
  }

  public Task MarkFailedAsync(Guid jobId, string error, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null) =>
    UpdateFailureAsync(jobId, error, needsAuthentication ? "Failed" : "Pending", needsAuthentication ? "authentication" : "provider", needsAuthentication, retryAtUtc, nowUtc, cancellationToken, expectedLeaseExpiresAtUtc);

  public Task MarkRejectedAsync(Guid jobId, string failureKind, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null) =>
    UpdateFailureAsync(jobId, error, "Failed", failureKind, false, nowUtc, nowUtc, cancellationToken, expectedLeaseExpiresAtUtc);

  public Task MarkProviderUnavailableAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null) =>
    UpdateFailureAsync(jobId, error, "Failed", "provider-unavailable", false, nowUtc, nowUtc, cancellationToken, expectedLeaseExpiresAtUtc, providerUnavailable: true);

  public Task MarkUnknownAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default, DateTimeOffset? expectedLeaseExpiresAtUtc = null) =>
    UpdateFailureAsync(jobId, error, "Unknown", "transport", false, nowUtc, nowUtc, cancellationToken, expectedLeaseExpiresAtUtc);

  public async Task MarkReviewRequiredAsync(
    Guid jobId,
    string? candidateRemoteId,
    string evidence,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default,
    DateTimeOffset? expectedLeaseExpiresAtUtc = null)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(evidence);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? job = expectedLeaseExpiresAtUtc is { } lease
      ? await context.GarminActivityUploadJobs.SingleOrDefaultAsync(item => item.Id == jobId && item.Status == "InFlight" && item.LeaseExpiresAtUtc == lease, cancellationToken)
      : await context.GarminActivityUploadJobs.SingleOrDefaultAsync(item => item.Id == jobId && item.Status == "InFlight", cancellationToken);
    if (expectedLeaseExpiresAtUtc is not null && job is null)
      throw new InvalidOperationException("The Garmin upload lease is no longer current.");
    if (job is null) return;
    job.Status = "ReviewRequired";
    job.OperationPhase = "Review";
    // Never overwrite a durable source identity while a recovery/cleanup
    // phase is reviewing another candidate.  The retained watch ID is needed
    // to resume safely; initial WatchSearch jobs have no identity yet and may
    // still record the candidate for the UI.
    if (string.IsNullOrWhiteSpace(job.MatchedRemoteId))
      job.MatchedRemoteId = string.IsNullOrWhiteSpace(candidateRemoteId) ? null : BoundId(candidateRemoteId);
    job.MatchEvidence = Bound(evidence);
    job.FailureKind = "review-required";
    job.LastError = "A possible Garmin activity match requires manual review before upload.";
    job.LeaseExpiresAtUtc = null;
    job.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
  }

  public Task<bool> RetryFailedAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    ChangeTerminalAsync(jobId, profileId, "Failed", "Pending", nowUtc, cancellationToken);

  public async Task<bool> DismissAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? entity = await context.GarminActivityUploadJobs.SingleOrDefaultAsync(
      item => item.Id == jobId && item.UserProfileId == profileId &&
        (item.Status == "Failed" || item.Status == "Unknown" || item.Status == "ReviewRequired"), cancellationToken);
    if (entity is null) return false;
    entity.Status = "Dismissed"; entity.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  public async Task<GarminActivityUploadJob> AcknowledgeFoundInGarminAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (jobId == Guid.Empty || profileId == Guid.Empty || operationId == Guid.Empty)
      throw new ArgumentException("Job, profile, and operation IDs are required.");
    if (requestFingerprint.Length != 64)
      throw new ArgumentException("A valid request fingerprint is required.", nameof(requestFingerprint));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    var receipt = new PersistenceWriteOperation(
      operationId,
      "garmin.activity.found",
      200,
      "{}",
      nowUtc,
      requestFingerprint);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, receipt, cancellationToken);
    GarminActivityUploadJobEntity entity = await context.GarminActivityUploadJobs
      .SingleOrDefaultAsync(item => item.Id == jobId && item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("The Garmin activity upload job was not found.");
    if (entity.Status != "Unknown")
      throw new InvalidOperationException("Only an upload with an unknown outcome can be marked as found in Garmin.");
    entity.Status = "FoundInGarmin";
    entity.AcknowledgedAtUtc = nowUtc;
    entity.LeaseExpiresAtUtc = null;
    entity.UpdatedAtUtc = nowUtc;
    WorkoutSessionEntity? session = await context.WorkoutSessions.AsNoTracking()
      .SingleOrDefaultAsync(item => item.Id == entity.WorkoutSessionId, cancellationToken);
    GarminActivityUploadJob result = Map(entity, session);
    PersistenceReceipts.Add(context, receipt with { OutcomeJson = System.Text.Json.JsonSerializer.Serialize(result) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return result;
  }

  public async Task<bool> ResumeIncompleteReplacementAsync(
    Guid jobId,
    string matchedRemoteId,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(matchedRemoteId);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? entity = await context.GarminActivityUploadJobs
      .Include(item => item.Account)
      .SingleOrDefaultAsync(item => item.Id == jobId, cancellationToken);
    if (entity is null || !entity.Account.Enabled || entity.Account.State != "Connected" ||
        entity.Account.WatchActivityHandling != GarminWatchActivityHandling.MergeAndReplace ||
        entity.RemoteId is not null || entity.ReplacementRemoteId is not null)
      return false;

    bool oldUnknown = entity.Status == "Unknown" && entity.OperationPhase == "ReplacementUpload" &&
      string.Equals(entity.MatchedRemoteId, matchedRemoteId, StringComparison.Ordinal);
    bool damagedConfirmed = entity.Status == "Confirmed" && entity.OperationPhase == "Upload" &&
      entity.MatchedRemoteId is null;
    if (!oldUnknown && !damagedConfirmed) return false;

    entity.Status = "Pending";
    entity.OperationPhase = "ResolveReplacement";
    entity.MatchedRemoteId = BoundId(matchedRemoteId);
    entity.AttemptCount = 0;
    entity.AvailableAtUtc = nowUtc;
    entity.FailureKind = null;
    entity.LastError = "Recovering the accepted merged activity without sending another upload.";
    entity.LeaseExpiresAtUtc = null;
    entity.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  public async Task<GarminActivityUploadJob> RetryUnknownVerifiedAbsentAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (jobId == Guid.Empty || profileId == Guid.Empty || operationId == Guid.Empty)
      throw new ArgumentException("Job, profile, and operation IDs are required.");
    if (requestFingerprint.Length != 64)
      throw new ArgumentException("A valid request fingerprint is required.", nameof(requestFingerprint));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    var receipt = new PersistenceWriteOperation(
      operationId,
      "garmin.activity.absent-retry",
      202,
      "{}",
      nowUtc,
      requestFingerprint);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, receipt, cancellationToken);
    GarminActivityUploadJobEntity entity = await context.GarminActivityUploadJobs
      .Include(item => item.Account)
      .SingleOrDefaultAsync(item => item.Id == jobId && item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("The Garmin activity upload job was not found.");
    if (entity.Status != "Unknown" || entity.RemoteId is not null || entity.MatchedRemoteId is not null ||
        entity.ReplacementRemoteId is not null || entity.OperationPhase != "Upload")
      throw new InvalidOperationException("Only an uncertain upload with no activity found in Garmin can be retried this way.");
    if (!entity.Account.Enabled || entity.Account.State != "Connected")
      throw new InvalidOperationException("Garmin activity upload must be connected and enabled before retrying.");

    entity.Status = "Pending";
    entity.AttemptCount = 0;
    entity.OperationPhase = entity.Account.WatchActivityHandling == GarminWatchActivityHandling.MergeAndReplace
      ? "WatchSearch"
      : "Upload";
    entity.RemoteId = null;
    entity.MatchedRemoteId = null;
    entity.ReplacementRemoteId = null;
    entity.MatchEvidence = null;
    entity.FailureKind = null;
    entity.LastError = null;
    entity.AvailableAtUtc = nowUtc;
    entity.LeaseExpiresAtUtc = null;
    entity.AcknowledgedAtUtc = null;
    entity.UpdatedAtUtc = nowUtc;
    GarminActivityUploadJob result = Map(entity);
    PersistenceReceipts.Add(context, receipt with { OutcomeJson = System.Text.Json.JsonSerializer.Serialize(result) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return result;
  }

  public async Task<GarminActivityUploadJob> StartHistoricalRecoveryAsync(
    Guid jobId,
    Guid profileId,
    string action,
    string matchedRemoteId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (jobId == Guid.Empty || profileId == Guid.Empty || operationId == Guid.Empty)
      throw new ArgumentException("Job, profile, and operation IDs are required.");
    if (action is not ("MergeIntoOne" or "UndoMerge"))
      throw new ArgumentException("A supported Garmin historical recovery action is required.", nameof(action));
    ArgumentException.ThrowIfNullOrWhiteSpace(matchedRemoteId);
    if (requestFingerprint.Length != 64)
      throw new ArgumentException("A valid request fingerprint is required.", nameof(requestFingerprint));

    await HistoricalRecoveryGate.WaitAsync(cancellationToken);
    try
    {
      await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
      await using var transaction = await context.Database.BeginTransactionAsync(
        System.Data.IsolationLevel.Serializable,
        cancellationToken);
      var receipt = new PersistenceWriteOperation(
        operationId,
        "garmin.activity.historical-recovery",
        202,
        "{}",
        nowUtc,
        requestFingerprint);
      await PersistenceReceipts.ThrowIfCompletedAsync(context, receipt, cancellationToken);
      GarminActivityUploadJobEntity entity = await context.GarminActivityUploadJobs
        .Include(item => item.Account)
        .SingleOrDefaultAsync(item => item.Id == jobId && item.UserProfileId == profileId, cancellationToken)
        ?? throw new KeyNotFoundException("The Garmin activity upload job was not found.");
      if (entity.Status is "Pending" or "InFlight")
        throw new InvalidOperationException("This Garmin recovery is already running. Refresh the historical item before choosing another action.");
      if (!entity.Account.Enabled || entity.Account.State != "Connected")
        throw new InvalidOperationException("Reconnect this Garmin upload account before recovering the historical item.");

      bool hasDurableReplacement = action == "MergeIntoOne" && !string.IsNullOrWhiteSpace(entity.ReplacementRemoteId);
      bool hasTwoUndoSources = action == "UndoMerge" &&
        !string.IsNullOrWhiteSpace(entity.MatchedRemoteId) &&
        !string.IsNullOrWhiteSpace(entity.RemoteId) &&
        !string.Equals(entity.MatchedRemoteId, entity.RemoteId, StringComparison.Ordinal) &&
        string.IsNullOrWhiteSpace(entity.ReplacementRemoteId);
      string nextPhase = action switch
      {
        "MergeIntoOne" when hasDurableReplacement ||
          entity.OperationPhase is "ReplacementUpload" or "ResolveReplacement" ||
          (!string.IsNullOrWhiteSpace(entity.MatchedRemoteId) && entity.OperationPhase != "UndoComplete") => "ResolveReplacement",
        "MergeIntoOne" => "EnsureReplacement",
        "UndoMerge" when hasTwoUndoSources => "DeleteGeneratedCopies",
        "UndoMerge" when entity.OperationPhase is "RestoreOriginal" or "ResolveRestoredOriginal" => "ResolveRestoredOriginal",
        "UndoMerge" when entity.OperationPhase is "RestoreLocal" or "ResolveRestoredLocal" => "ResolveRestoredLocal",
        "UndoMerge" when entity.OperationPhase == "ResolveLocalSource" => "ResolveLocalSource",
        _ => "ResolveOriginal",
      };
      string previousPhase = entity.OperationPhase;
      entity.Status = "Pending";
      entity.OperationPhase = nextPhase;
      // Durable IDs are never discarded when resuming a recovery. Their exact
      // FITs are resolved read-only before cleanup, so a stale search cannot
      // authorize another upload.
      bool preserveUndoOriginal = action == "UndoMerge" && nextPhase != "ResolveOriginal" &&
        !string.IsNullOrWhiteSpace(entity.MatchedRemoteId);
      bool preserveMergedFromUndoOriginal = action == "MergeIntoOne" &&
        previousPhase == "UndoComplete" &&
        !string.IsNullOrWhiteSpace(entity.MatchedRemoteId);
      bool preserveLocalIdentity = action == "UndoMerge" &&
        nextPhase is "ResolveLocalSource" or "ResolveRestoredLocal" or "DeleteGeneratedCopies" &&
        !string.IsNullOrWhiteSpace(entity.RemoteId);
      entity.RemoteId = preserveLocalIdentity ? entity.RemoteId : null;
      entity.MatchedRemoteId = preserveUndoOriginal || preserveMergedFromUndoOriginal
        ? entity.MatchedRemoteId
        : BoundId(matchedRemoteId);
      entity.ReplacementRemoteId = hasDurableReplacement ? entity.ReplacementRemoteId : null;
      entity.MatchEvidence = action == "MergeIntoOne"
        ? "Historical item requested: merge into one verified Garmin activity."
        : "Historical item requested: restore one watch-original plus one plain TreadmillRunner Garmin activity while keeping local History.";
      entity.AttemptCount = 0;
      entity.AvailableAtUtc = nowUtc;
      entity.LeaseExpiresAtUtc = null;
      entity.FailureKind = null;
      entity.LastError = null;
      entity.AcknowledgedAtUtc = null;
      entity.UpdatedAtUtc = nowUtc;
      WorkoutSessionEntity? session = await context.WorkoutSessions.AsNoTracking()
        .SingleOrDefaultAsync(item => item.Id == entity.WorkoutSessionId, cancellationToken);
      GarminActivityUploadJob result = Map(entity, session);
      // Save through the receipt helper so a cross-process unique/lock race
      // can be translated to the same replay/scope exceptions as the normal
      // in-process gate path.
      await PersistenceReceipts.SaveAsync(
        context,
        contextFactory,
        receipt with { OutcomeJson = System.Text.Json.JsonSerializer.Serialize(result) },
        cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return result;
    }
    finally
    {
      HistoricalRecoveryGate.Release();
    }
  }

  public async Task<GarminActivityUploadJob> ReprocessLegacyConfirmedForMergeAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (jobId == Guid.Empty || profileId == Guid.Empty || operationId == Guid.Empty)
      throw new ArgumentException("Job, profile, and operation IDs are required.");
    if (requestFingerprint.Length != 64)
      throw new ArgumentException("A valid request fingerprint is required.", nameof(requestFingerprint));

    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    var receipt = new PersistenceWriteOperation(
      operationId,
      "garmin.activity.reprocess-merge",
      202,
      "{}",
      nowUtc,
      requestFingerprint);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, receipt, cancellationToken);

    GarminActivityUploadJobEntity entity = await context.GarminActivityUploadJobs
      .Include(item => item.Account)
      .SingleOrDefaultAsync(item => item.Id == jobId && item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("The Garmin activity upload job was not found.");
    if (!entity.Account.Enabled || entity.Account.State != "Connected" ||
        entity.Account.WatchActivityHandling != GarminWatchActivityHandling.MergeAndReplace)
      throw new InvalidOperationException("Garmin upload must be connected, enabled, and set to merge-and-replace.");
    if (entity.Status != "Confirmed" || entity.RemoteId is not null || entity.OperationPhase != "WatchSearch" ||
        entity.MatchedRemoteId is not null || entity.ReplacementRemoteId is not null)
      throw new InvalidOperationException("Only a legacy confirmed job that has not recorded a watch-search result can be reprocessed.");

    entity.Status = "Pending";
    entity.AttemptCount = 0;
    entity.AvailableAtUtc = nowUtc;
    entity.LeaseExpiresAtUtc = null;
    entity.FailureKind = null;
    entity.LastError = null;
    entity.AcknowledgedAtUtc = null;
    entity.UpdatedAtUtc = nowUtc;
    WorkoutSessionEntity? session = await context.WorkoutSessions.AsNoTracking()
      .SingleOrDefaultAsync(item => item.Id == entity.WorkoutSessionId, cancellationToken);
    GarminActivityUploadJob result = Map(entity, session);
    PersistenceReceipts.Add(context, receipt with { OutcomeJson = System.Text.Json.JsonSerializer.Serialize(result) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return result;
  }

  private async Task UpdateFailureAsync(Guid jobId, string error, string status, string failureKind, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken, DateTimeOffset? expectedLeaseExpiresAtUtc = null, bool providerUnavailable = false)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredWorkerJobAsync(context, jobId, expectedLeaseExpiresAtUtc, cancellationToken);
    job.Status = job.AttemptCount >= 3 && status == "Pending" ? "Failed" : status;
    job.FailureKind = failureKind; job.LastError = Bound(error); job.AvailableAtUtc = retryAtUtc; job.LeaseExpiresAtUtc = null; job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.LastError = Bound(error); account.State = needsAuthentication ? "NeedsAuthentication" : providerUnavailable ? "ProviderUnavailable" : account.State; account.UpdatedAtUtc = nowUtc; account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  private async Task<bool> ChangeTerminalAsync(Guid jobId, Guid profileId, string from, string to, DateTimeOffset nowUtc, CancellationToken cancellationToken)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? entity = await context.GarminActivityUploadJobs.Include(item => item.Account).SingleOrDefaultAsync(
      item => item.Id == jobId && item.UserProfileId == profileId && item.Status == from &&
        (item.FailureKind == "provider" || item.FailureKind == "provider-unavailable" || item.FailureKind == "duplicate"), cancellationToken);
    if (entity is null) return false;
    string? failureKind = entity.FailureKind;
    entity.Status = to; entity.AttemptCount = 0; entity.AvailableAtUtc = nowUtc; entity.FailureKind = null; entity.LastError = null; entity.UpdatedAtUtc = nowUtc;
    entity.OperationPhase = entity.OperationPhase switch
    {
      "ReplacementUpload" when failureKind == "duplicate" => "ResolveReplacement",
      "ReplacementUpload" => "EnsureReplacement",
      "RestoreOriginal" when failureKind == "duplicate" => "ResolveRestoredOriginal",
      "RestoreOriginal" => "ResolveOriginal",
      "RestoreLocal" when failureKind == "duplicate" => "ResolveRestoredLocal",
      "RestoreLocal" => "ResolveLocalSource",
      "DeleteReplacementDuplicate" => "DeleteReplacementDuplicates",
      "DeleteGeneratedCopy" => "DeleteGeneratedCopies",
      "DeleteResyncedOriginal" => "VerifyResync",
      _ => entity.OperationPhase,
    };
    if (entity.Account.Enabled && entity.Account.State == "Connected" &&
        entity.Account.WatchActivityHandling == GarminWatchActivityHandling.MergeAndReplace &&
        entity.MatchedRemoteId is null && entity.ReplacementRemoteId is null)
      entity.OperationPhase = "WatchSearch";
    if (entity.Account.State == "ProviderUnavailable") entity.Account.State = "Connected";
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  private static Task<GarminActivityUploadJobEntity> RequiredJobAsync(TreadmillRunnerDbContext context, Guid jobId, CancellationToken cancellationToken) =>
    context.GarminActivityUploadJobs.SingleAsync(item => item.Id == jobId && item.Status == "InFlight", cancellationToken);
  private static Task<GarminActivityUploadJobEntity> RequiredWorkerJobAsync(
    TreadmillRunnerDbContext context,
    Guid jobId,
    DateTimeOffset? expectedLeaseExpiresAtUtc,
    CancellationToken cancellationToken) =>
    expectedLeaseExpiresAtUtc is { } lease
      ? RequiredMutationLeaseAsync(context, jobId, lease, cancellationToken)
      : RequiredJobAsync(context, jobId, cancellationToken);
  private static Task<GarminActivityUploadJobEntity> RequiredMutationLeaseAsync(
    TreadmillRunnerDbContext context,
    Guid jobId,
    DateTimeOffset expectedLeaseExpiresAtUtc,
    CancellationToken cancellationToken) =>
    context.GarminActivityUploadJobs.SingleAsync(item =>
      item.Id == jobId &&
      item.Status == "InFlight" &&
      item.LeaseExpiresAtUtc == expectedLeaseExpiresAtUtc,
      cancellationToken);
  private static void EnsureVersion(int actual, int expected) { if (actual != expected) throw new DbUpdateConcurrencyException("The Garmin upload setting changed; refresh and try again."); }
  private static string Bound(string value) => string.IsNullOrWhiteSpace(value) ? "Garmin upload failed." : value.Trim()[..Math.Min(value.Trim().Length, 1000)];
  private static string BoundId(string value) => string.IsNullOrWhiteSpace(value) ? throw new ArgumentException("A Garmin activity ID is required.", nameof(value)) : value.Trim()[..Math.Min(value.Trim().Length, 256)];
  private static GarminActivityUploadAccount Map(GarminActivityUploadAccountEntity entity) => new(entity.Id, entity.UserProfileId, entity.AccountLabel, entity.ProtectedTokenStore, entity.Enabled, entity.WatchActivityHandling, entity.State, entity.ConnectedAtUtc, entity.UploadFromUtc, entity.LastUploadSuccessAtUtc, entity.LastError, entity.Version);
  private static GarminActivityUploadJob Map(GarminActivityUploadJobEntity entity, WorkoutSessionEntity? session = null)
  {
    bool canRetry = entity.Status == "Failed" &&
      (entity.FailureKind is "provider" or "provider-unavailable" or "duplicate");
    DateTimeOffset? retryAtUtc = entity.Status == "Pending" || canRetry
      ? entity.AvailableAtUtc
      : null;
    return new(
      entity.Id, entity.UserProfileId, entity.GarminActivityUploadAccountId, entity.WorkoutSessionId, entity.Status, entity.AttemptCount,
      entity.LeaseExpiresAtUtc,
      entity.RemoteId, entity.OperationPhase, entity.MatchedRemoteId, entity.ReplacementRemoteId, entity.MatchEvidence,
      entity.FailureKind, canRetry, retryAtUtc,
      session?.WorkoutTitle, session?.StartedAtUtc, session?.DurationSeconds, entity.LastError, entity.UpdatedAtUtc,
      entity.AcknowledgedAtUtc);
  }
}
