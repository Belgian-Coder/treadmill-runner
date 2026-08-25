using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Household;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record VersionedRunnerExperiencePreferences(
  Guid ProfileId,
  RunnerExperiencePreferences Preferences,
  int Version,
  DateTimeOffset UpdatedAtUtc);

public sealed record LocalGoalDefinition(
  Guid Id,
  Guid ProfileId,
  string Kind,
  string Period,
  double TargetValue,
  bool Enabled,
  int Version,
  DateTimeOffset UpdatedAtUtc);

public sealed record StoredProgressionRecommendation(
  Guid Id,
  Guid ProfileId,
  Guid SessionId,
  ProgressionRecommendation Recommendation,
  string Status,
  int Version,
  DateTimeOffset CreatedAtUtc,
  DateTimeOffset? DecidedAtUtc);

public sealed record VersionedLocalBackupPolicy(
  Guid Id,
  LocalBackupPolicy Policy,
  int Version,
  DateTimeOffset UpdatedAtUtc);

public sealed record StoredBackupVerification(
  Guid Id,
  Guid PolicyId,
  string BackupPath,
  string Status,
  string Detail,
  long BackupBytes,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset CompletedAtUtc);

public interface ILocalFirstExperienceStore
{
  Task<VersionedRunnerExperiencePreferences> GetPreferencesAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<VersionedRunnerExperiencePreferences> SavePreferencesAsync(Guid profileId, RunnerExperiencePreferences preferences, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<LocalGoalDefinition>> ListGoalsAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<LocalGoalDefinition> SaveGoalAsync(Guid profileId, Guid? goalId, string kind, string period, double targetValue, bool enabled, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<StoredProgressionRecommendation> SaveRecommendationAsync(Guid operationId, Guid profileId, Guid sessionId, ProgressionEvidence evidence, ProgressionRecommendation recommendation, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<StoredProgressionRecommendation> DecideRecommendationAsync(Guid id, Guid profileId, bool accepted, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<StoredProgressionRecommendation>> ListRecommendationsAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<VersionedLocalBackupPolicy?> GetBackupPolicyAsync(CancellationToken cancellationToken = default);
  Task<VersionedLocalBackupPolicy> SaveBackupPolicyAsync(Guid? id, LocalBackupPolicy policy, int? expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task RecordBackupVerificationAsync(StoredBackupVerification result, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<StoredBackupVerification>> ListBackupVerificationsAsync(int take, CancellationToken cancellationToken = default);
}

public sealed class LocalFirstExperienceStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : ILocalFirstExperienceStore
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<VersionedRunnerExperiencePreferences> GetPreferencesAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    RequireId(profileId, nameof(profileId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    RunnerExperiencePreferenceEntity? entity = await context.RunnerExperiencePreferences.AsNoTracking()
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (entity is not null) return Map(entity);
    bool exists = await context.UserProfiles.AsNoTracking().AnyAsync(item => item.Id == profileId && !item.IsArchived, cancellationToken);
    if (!exists) throw new KeyNotFoundException($"Profile {profileId} was not found.");
    return new(profileId, RunnerExperiencePreferences.Default, 0, DateTimeOffset.UnixEpoch);
  }

  public async Task<VersionedRunnerExperiencePreferences> SavePreferencesAsync(
    Guid profileId,
    RunnerExperiencePreferences preferences,
    int? expectedVersion,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    RequireId(profileId, nameof(profileId));
    ArgumentNullException.ThrowIfNull(preferences);
    RequireUtc(nowUtc, nameof(nowUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    if (!await context.UserProfiles.AnyAsync(item => item.Id == profileId && !item.IsArchived, cancellationToken))
      throw new KeyNotFoundException($"Profile {profileId} was not found.");
    RunnerExperiencePreferenceEntity? entity = await context.RunnerExperiencePreferences
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (entity is null)
    {
      if (expectedVersion is not null and not 0) throw new DbUpdateConcurrencyException("Preferences do not have that version.");
      entity = new RunnerExperiencePreferenceEntity { Id = Guid.NewGuid(), UserProfileId = profileId, Version = 1 };
      context.RunnerExperiencePreferences.Add(entity);
    }
    else
    {
      RequireVersion(entity.Version, expectedVersion);
      entity.Version++;
    }

    entity.DisplayStyle = preferences.DisplayStyle.ToString();
    entity.PrimaryMetricsJson = JsonSerializer.Serialize(preferences.PrimaryMetrics, JsonOptions);
    entity.CueStepChanges = preferences.Cues.StepChanges;
    entity.CueHeartRateDeparture = preferences.Cues.HeartRateDeparture;
    entity.CueHalfway = preferences.Cues.Halfway;
    entity.CueConnectionProblems = preferences.Cues.ConnectionProblems;
    entity.CueCompletion = preferences.Cues.Completion;
    entity.CueVolumePercent = preferences.Cues.VolumePercent;
    entity.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task<IReadOnlyList<LocalGoalDefinition>> ListGoalsAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    RequireId(profileId, nameof(profileId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.LocalGoals.AsNoTracking().Where(item => item.UserProfileId == profileId)
      .OrderBy(item => item.Kind).ThenBy(item => item.Period)
      .Select(item => new LocalGoalDefinition(item.Id, item.UserProfileId, item.Kind, item.Period, item.TargetValue, item.Enabled, item.Version, item.UpdatedAtUtc))
      .ToArrayAsync(cancellationToken);
  }

  public async Task<LocalGoalDefinition> SaveGoalAsync(
    Guid profileId,
    Guid? goalId,
    string kind,
    string period,
    double targetValue,
    bool enabled,
    int? expectedVersion,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    RequireId(profileId, nameof(profileId));
    if (kind is not ("Sessions" or "Minutes" or "Distance" or "PlanCompletion")) throw new ArgumentException("Unsupported local goal kind.", nameof(kind));
    if (period is not ("Weekly" or "Monthly" or "Plan")) throw new ArgumentException("Unsupported local goal period.", nameof(period));
    if (!double.IsFinite(targetValue) || targetValue <= 0) throw new ArgumentOutOfRangeException(nameof(targetValue));
    RequireUtc(nowUtc, nameof(nowUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    if (!await context.UserProfiles.AnyAsync(item => item.Id == profileId && !item.IsArchived, cancellationToken))
      throw new KeyNotFoundException($"Profile {profileId} was not found.");
    LocalGoalEntity? entity = goalId is { } id
      ? await context.LocalGoals.SingleOrDefaultAsync(item => item.Id == id && item.UserProfileId == profileId, cancellationToken)
      : await context.LocalGoals.SingleOrDefaultAsync(item => item.UserProfileId == profileId && item.Kind == kind && item.Period == period, cancellationToken);
    if (entity is null)
    {
      if (expectedVersion is not null) throw new DbUpdateConcurrencyException("Goal does not have that version.");
      entity = new LocalGoalEntity { Id = goalId ?? Guid.NewGuid(), UserProfileId = profileId, CreatedAtUtc = nowUtc, Version = 1 };
      context.LocalGoals.Add(entity);
    }
    else
    {
      RequireVersion(entity.Version, expectedVersion);
      entity.Version++;
    }
    entity.Kind = kind;
    entity.Period = period;
    entity.TargetValue = targetValue;
    entity.Enabled = enabled;
    entity.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    return new(entity.Id, profileId, kind, period, targetValue, enabled, entity.Version, nowUtc);
  }

  public async Task<StoredProgressionRecommendation> SaveRecommendationAsync(
    Guid operationId,
    Guid profileId,
    Guid sessionId,
    ProgressionEvidence evidence,
    ProgressionRecommendation recommendation,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    RequireId(operationId, nameof(operationId));
    RequireId(profileId, nameof(profileId));
    RequireId(sessionId, nameof(sessionId));
    RequireUtc(nowUtc, nameof(nowUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    ProgressionRecommendationEntity? existing = await context.ProgressionRecommendations
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId && item.WorkoutSessionId == sessionId, cancellationToken);
    if (existing is not null)
    {
      if (string.Equals(existing.Status, "Pending", StringComparison.Ordinal))
      {
        existing.Action = recommendation.Action.ToString();
        existing.Reason = recommendation.Reason;
        existing.AlgorithmVersion = recommendation.AlgorithmVersion;
        existing.EvidenceJson = JsonSerializer.Serialize(evidence, JsonOptions);
        existing.Version++;
        await context.SaveChangesAsync(cancellationToken);
      }

      return Map(existing);
    }
    var entity = new ProgressionRecommendationEntity
    {
      Id = Guid.NewGuid(),
      OperationId = operationId,
      UserProfileId = profileId,
      WorkoutSessionId = sessionId,
      Action = recommendation.Action.ToString(),
      Reason = recommendation.Reason,
      AlgorithmVersion = recommendation.AlgorithmVersion,
      EvidenceJson = JsonSerializer.Serialize(evidence, JsonOptions),
      Status = "Pending",
      CreatedAtUtc = nowUtc,
      Version = 1,
    };
    context.ProgressionRecommendations.Add(entity);
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task<StoredProgressionRecommendation> DecideRecommendationAsync(
    Guid id,
    Guid profileId,
    bool accepted,
    int expectedVersion,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    RequireId(id, nameof(id));
    RequireId(profileId, nameof(profileId));
    RequireUtc(nowUtc, nameof(nowUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    ProgressionRecommendationEntity entity = await context.ProgressionRecommendations
      .SingleOrDefaultAsync(item => item.Id == id && item.UserProfileId == profileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Recommendation {id} was not found.");
    RequireVersion(entity.Version, expectedVersion);
    if (entity.Status != "Pending") throw new InvalidOperationException("Recommendation already has a decision receipt.");
    entity.Status = accepted ? "Accepted" : "Rejected";
    entity.DecidedAtUtc = nowUtc;
    entity.Version++;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task<IReadOnlyList<StoredProgressionRecommendation>> ListRecommendationsAsync(Guid profileId, CancellationToken cancellationToken = default)
  {
    RequireId(profileId, nameof(profileId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    ProgressionRecommendationEntity[] entities = await context.ProgressionRecommendations
      .FromSqlInterpolated($"""
        SELECT "Id", "OperationId", "UserProfileId", "WorkoutSessionId", "Action",
          "Reason", "AlgorithmVersion", "EvidenceJson", "Status", "CreatedAtUtc",
          "DecidedAtUtc", "Version"
        FROM "ProgressionRecommendations"
        WHERE "UserProfileId" = {profileId}
        ORDER BY "CreatedAtUtc" DESC
        LIMIT 50
        """)
      .AsNoTracking()
      .ToArrayAsync(cancellationToken);
    return entities.Select(Map).ToArray();
  }

  public async Task<VersionedLocalBackupPolicy?> GetBackupPolicyAsync(CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    LocalBackupPolicyEntity? entity = await context.LocalBackupPolicies.AsNoTracking().SingleOrDefaultAsync(cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<VersionedLocalBackupPolicy> SaveBackupPolicyAsync(
    Guid? id,
    LocalBackupPolicy policy,
    int? expectedVersion,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(policy);
    RequireUtc(nowUtc, nameof(nowUtc));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    LocalBackupPolicyEntity? entity = await context.LocalBackupPolicies.SingleOrDefaultAsync(cancellationToken);
    if (entity is null)
    {
      if (expectedVersion is not null) throw new DbUpdateConcurrencyException("Backup policy does not have that version.");
      entity = new LocalBackupPolicyEntity { Id = id ?? Guid.NewGuid(), Version = 1 };
      context.LocalBackupPolicies.Add(entity);
    }
    else
    {
      if (id is { } requestedId && requestedId != entity.Id) throw new InvalidOperationException("Only one local backup policy is supported.");
      RequireVersion(entity.Version, expectedVersion);
      entity.Version++;
    }
    entity.DestinationPath = policy.DestinationPath;
    entity.IntervalHours = policy.IntervalHours;
    entity.RetentionCount = policy.RetentionCount;
    entity.Enabled = policy.Enabled;
    entity.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  public async Task RecordBackupVerificationAsync(StoredBackupVerification result, CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(result);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    context.BackupVerifications.Add(new BackupVerificationEntity
    {
      Id = result.Id,
      LocalBackupPolicyId = result.PolicyId,
      BackupPath = result.BackupPath,
      Status = result.Status,
      Detail = result.Detail,
      BackupBytes = result.BackupBytes,
      StartedAtUtc = result.StartedAtUtc,
      CompletedAtUtc = result.CompletedAtUtc,
    });
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<IReadOnlyList<StoredBackupVerification>> ListBackupVerificationsAsync(int take, CancellationToken cancellationToken = default)
  {
    if (take is < 1 or > 100) throw new ArgumentOutOfRangeException(nameof(take));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    BackupVerificationEntity[] entities = await context.BackupVerifications
      .FromSqlInterpolated($"""
        SELECT "Id", "LocalBackupPolicyId", "BackupPath", "Status", "Detail", "BackupBytes",
          "StartedAtUtc", "CompletedAtUtc"
        FROM "BackupVerifications"
        ORDER BY "CompletedAtUtc" DESC
        LIMIT {take}
        """)
      .AsNoTracking()
      .ToArrayAsync(cancellationToken);
    return entities
      .Select(item => new StoredBackupVerification(item.Id, item.LocalBackupPolicyId, item.BackupPath, item.Status, item.Detail, item.BackupBytes, item.StartedAtUtc, item.CompletedAtUtc))
      .ToArray();
  }

  private static VersionedRunnerExperiencePreferences Map(RunnerExperiencePreferenceEntity entity)
  {
    LiveMetric[] metrics = JsonSerializer.Deserialize<LiveMetric[]>(entity.PrimaryMetricsJson, JsonOptions) ?? [];
    var cues = new RunCuePreferences(entity.CueStepChanges, entity.CueHeartRateDeparture, entity.CueHalfway, entity.CueConnectionProblems, entity.CueCompletion, entity.CueVolumePercent);
    return new(entity.UserProfileId, new RunnerExperiencePreferences(Enum.Parse<LiveDisplayStyle>(entity.DisplayStyle), metrics, cues), entity.Version, entity.UpdatedAtUtc);
  }

  private static StoredProgressionRecommendation Map(ProgressionRecommendationEntity entity) => new(
    entity.Id, entity.UserProfileId, entity.WorkoutSessionId,
    new ProgressionRecommendation(Enum.Parse<ProgressionAction>(entity.Action), entity.Reason, entity.AlgorithmVersion),
    entity.Status, entity.Version, entity.CreatedAtUtc, entity.DecidedAtUtc);

  private static VersionedLocalBackupPolicy Map(LocalBackupPolicyEntity entity) => new(
    entity.Id, new LocalBackupPolicy(entity.DestinationPath, entity.IntervalHours, entity.RetentionCount, entity.Enabled),
    entity.Version, entity.UpdatedAtUtc);

  private static void RequireId(Guid value, string name)
  {
    if (value == Guid.Empty) throw new ArgumentException("Identifier cannot be empty.", name);
  }

  private static void RequireUtc(DateTimeOffset value, string name)
  {
    if (value.Offset != TimeSpan.Zero) throw new ArgumentException("Timestamp must be UTC.", name);
  }

  private static void RequireVersion(int actual, int? expected)
  {
    if (expected is null || expected.Value != actual)
      throw new DbUpdateConcurrencyException($"Expected version {expected?.ToString() ?? "<missing>"}, but stored version is {actual}.");
  }
}
