using System.Security.Cryptography;
using System.Text;
using System.Globalization;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record GarminActivityUploadAccount(
  Guid Id,
  Guid UserProfileId,
  string AccountLabel,
  string ProtectedTokenStore,
  bool Enabled,
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
  string? RemoteId,
  string? FailureKind,
  bool CanRetry,
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
  string? AccountLabel,
  string State,
  int Pending,
  int Confirmed,
  int FoundInGarmin,
  int Failed,
  int Unknown,
  DateTimeOffset? LastSuccessAtUtc,
  string? LastError,
  int? Version);

public interface IGarminActivityUploadStore
{
  Task<GarminActivityUploadStatus> GetStatusAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<GarminActivityUploadJob>> ListJobsAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount?> FindAccountAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount> ConnectAsync(Guid profileId, string accountLabel, string protectedTokenStore, bool enabled, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadAccount> SetEnabledAsync(Guid profileId, bool enabled, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> DisconnectAsync(Guid profileId, int expectedVersion, CancellationToken cancellationToken = default);
  Task<int> ReconcileCompletedSessionsAsync(DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> EnqueueSystemTestAsync(Guid profileId, Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default);
  Task MarkConfirmedAsync(Guid jobId, string? remoteId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkFailedAsync(Guid jobId, string error, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkRejectedAsync(Guid jobId, string failureKind, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkProviderUnavailableAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task MarkUnknownAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> RetryFailedAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> DismissAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<GarminActivityUploadJob> AcknowledgeFoundInGarminAsync(
    Guid jobId,
    Guid profileId,
    Guid operationId,
    string requestFingerprint,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
}

public sealed class GarminActivityUploadStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IGarminActivityUploadStore
{
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

  public async Task<GarminActivityUploadStatus> GetStatusAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity? account = await context.GarminActivityUploadAccounts.AsNoTracking()
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (account is null || account.State == "Disconnected") return new(profileId, false, false, null, "Disconnected", 0, 0, 0, 0, 0, null, null, account?.Version);
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
      account.AccountLabel,
      account.State,
      Count("Pending") + Count("InFlight"),
      Count("Confirmed"),
      Count("FoundInGarmin"),
      Count("Failed"),
      Count("Unknown"),
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
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
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
      if (enabled && entity.UploadFromUtc is null) entity.UploadFromUtc = nowUtc;
      entity.State = "Connected";
      entity.LastError = null;
      entity.UpdatedAtUtc = nowUtc;
      entity.Version++;
    }
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task<GarminActivityUploadAccount> SetEnabledAsync(Guid profileId, bool enabled, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadAccountEntity entity = await context.GarminActivityUploadAccounts
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException("No unsupported Garmin upload account is connected for this runner.");
    EnsureVersion(entity.Version, expectedVersion);
    if (enabled && !entity.Enabled) entity.UploadFromUtc = nowUtc;
    entity.Enabled = enabled;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

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
    var candidates = new List<(Guid Session, Guid Profile, Guid Account)>();
    foreach (GarminActivityUploadAccountEntity account in accounts)
    {
      int remaining = 100 - candidates.Count;
      if (remaining <= 0) break;
      string watermark = account.UploadFromUtc!.Value.ToString("yyyy-MM-dd HH:mm:ss.fffffffzzz", CultureInfo.InvariantCulture);
      WorkoutSessionEntity[] sessions = await context.WorkoutSessions
        .FromSqlInterpolated($"SELECT s.* FROM WorkoutSessions AS s WHERE s.UserProfileId = {account.UserProfileId} AND s.SessionOrigin <> 'SystemTest' AND s.StartedAtUtc IS NOT NULL AND s.EndedAtUtc IS NOT NULL AND s.EndedAtUtc >= {watermark} AND (s.State = 'Completed' OR s.State = 'Stopped') AND NOT EXISTS (SELECT 1 FROM GarminActivityUploadJobs AS j WHERE j.WorkoutSessionId = s.Id) ORDER BY s.EndedAtUtc LIMIT {remaining}")
        .AsNoTracking()
        .ToArrayAsync(cancellationToken);
      candidates.AddRange(sessions.Select(session => (session.Id, session.UserProfileId, account.Id)));
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
        AvailableAtUtc = nowUtc,
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
    List<GarminActivityUploadJobEntity> expired = (await context.GarminActivityUploadJobs
      .Where(item => item.Status == "InFlight")
      .Take(100)
      .ToListAsync(cancellationToken))
      .Where(item => item.LeaseExpiresAtUtc < nowUtc)
      .ToList();
    foreach (GarminActivityUploadJobEntity item in expired)
    {
      item.Status = "Unknown";
      item.LastError = "The service restarted or timed out after upload began; the outcome is unknown and will not be retried automatically.";
      item.LeaseExpiresAtUtc = null;
      item.UpdatedAtUtc = nowUtc;
    }
    if (expired.Count > 0) await context.SaveChangesAsync(cancellationToken);
    for (var contentionAttempt = 0; contentionAttempt < 3; contentionAttempt++)
    {
      GarminActivityUploadJobEntity? candidate = (await context.GarminActivityUploadJobs.AsNoTracking()
        .Include(item => item.Account)
        .Where(item => item.Status == "Pending" && item.AttemptCount < 3 && item.Account.Enabled && item.Account.State == "Connected")
        .Take(100)
        .ToListAsync(cancellationToken))
        .Where(item => item.AvailableAtUtc <= nowUtc)
        .OrderBy(item => item.CreatedAtUtc)
        .FirstOrDefault();
      if (candidate is null) return null;
      int nextAttempt = candidate.AttemptCount + 1;
      int changed = await context.GarminActivityUploadJobs
        .Where(item => item.Id == candidate.Id && item.Status == "Pending" && item.AttemptCount == candidate.AttemptCount && item.Account.Enabled && item.Account.State == "Connected")
        .ExecuteUpdateAsync(setters => setters
          .SetProperty(item => item.Status, "InFlight")
          .SetProperty(item => item.AttemptCount, nextAttempt)
          .SetProperty(item => item.LeaseExpiresAtUtc, nowUtc.Add(leaseDuration))
          .SetProperty(item => item.UpdatedAtUtc, nowUtc), cancellationToken);
      if (changed == 0) continue;
      candidate.Status = "InFlight";
      candidate.AttemptCount = nextAttempt;
      candidate.LeaseExpiresAtUtc = nowUtc.Add(leaseDuration);
      candidate.UpdatedAtUtc = nowUtc;
      return Map(candidate);
    }
    return null;
  }

  public async Task MarkConfirmedAsync(Guid jobId, string? remoteId, string protectedTokenStore, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredJobAsync(context, jobId, cancellationToken);
    job.Status = "Confirmed"; job.RemoteId = remoteId; job.FailureKind = null; job.LastError = null; job.LeaseExpiresAtUtc = null; job.UpdatedAtUtc = nowUtc;
    GarminActivityUploadAccountEntity account = await context.GarminActivityUploadAccounts.SingleAsync(item => item.Id == job.GarminActivityUploadAccountId, cancellationToken);
    account.ProtectedTokenStore = protectedTokenStore; account.State = "Connected"; account.LastError = null; account.LastUploadSuccessAtUtc = nowUtc; account.UpdatedAtUtc = nowUtc; account.Version++;
    await context.SaveChangesAsync(cancellationToken);
  }

  public Task MarkFailedAsync(Guid jobId, string error, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    UpdateFailureAsync(jobId, error, needsAuthentication ? "Failed" : "Pending", needsAuthentication ? "authentication" : "provider", needsAuthentication, retryAtUtc, nowUtc, cancellationToken);

  public Task MarkRejectedAsync(Guid jobId, string failureKind, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    UpdateFailureAsync(jobId, error, "Failed", failureKind, false, nowUtc, nowUtc, cancellationToken);

  public Task MarkProviderUnavailableAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    UpdateFailureAsync(jobId, error, "Failed", "provider-unavailable", false, nowUtc, nowUtc, cancellationToken, providerUnavailable: true);

  public Task MarkUnknownAsync(Guid jobId, string error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    UpdateFailureAsync(jobId, error, "Unknown", "transport", false, nowUtc, nowUtc, cancellationToken);

  public Task<bool> RetryFailedAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) =>
    ChangeTerminalAsync(jobId, profileId, "Failed", "Pending", nowUtc, cancellationToken);

  public async Task<bool> DismissAsync(Guid jobId, Guid profileId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity? entity = await context.GarminActivityUploadJobs.SingleOrDefaultAsync(
      item => item.Id == jobId && item.UserProfileId == profileId && (item.Status == "Failed" || item.Status == "Unknown"), cancellationToken);
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

  private async Task UpdateFailureAsync(Guid jobId, string error, string status, string failureKind, bool needsAuthentication, DateTimeOffset retryAtUtc, DateTimeOffset nowUtc, CancellationToken cancellationToken, bool providerUnavailable = false)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminActivityUploadJobEntity job = await RequiredJobAsync(context, jobId, cancellationToken);
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
        (item.FailureKind == "provider" || item.FailureKind == "provider-unavailable"), cancellationToken);
    if (entity is null) return false;
    entity.Status = to; entity.AttemptCount = 0; entity.AvailableAtUtc = nowUtc; entity.FailureKind = null; entity.LastError = null; entity.UpdatedAtUtc = nowUtc;
    if (entity.Account.State == "ProviderUnavailable") entity.Account.State = "Connected";
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  private static Task<GarminActivityUploadJobEntity> RequiredJobAsync(TreadmillRunnerDbContext context, Guid jobId, CancellationToken cancellationToken) =>
    context.GarminActivityUploadJobs.SingleAsync(item => item.Id == jobId && item.Status == "InFlight", cancellationToken);
  private static void EnsureVersion(int actual, int expected) { if (actual != expected) throw new DbUpdateConcurrencyException("The Garmin upload setting changed; refresh and try again."); }
  private static string Bound(string value) => string.IsNullOrWhiteSpace(value) ? "Garmin upload failed." : value.Trim()[..Math.Min(value.Trim().Length, 1000)];
  private static GarminActivityUploadAccount Map(GarminActivityUploadAccountEntity entity) => new(entity.Id, entity.UserProfileId, entity.AccountLabel, entity.ProtectedTokenStore, entity.Enabled, entity.State, entity.ConnectedAtUtc, entity.UploadFromUtc, entity.LastUploadSuccessAtUtc, entity.LastError, entity.Version);
  private static GarminActivityUploadJob Map(GarminActivityUploadJobEntity entity, WorkoutSessionEntity? session = null) => new(
    entity.Id, entity.UserProfileId, entity.GarminActivityUploadAccountId, entity.WorkoutSessionId, entity.Status, entity.AttemptCount,
    entity.RemoteId, entity.FailureKind, entity.Status == "Failed" && entity.FailureKind is "provider" or "provider-unavailable",
    session?.WorkoutTitle, session?.StartedAtUtc, session?.DurationSeconds, entity.LastError, entity.UpdatedAtUtc,
    entity.AcknowledgedAtUtc);
}
