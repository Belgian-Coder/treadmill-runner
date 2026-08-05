using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record VersionedUserProfile(UserProfile Profile, int Version, bool IsArchived, DateTimeOffset? ArchivedAtUtc);

public interface IProfileStore
{
  Task<IReadOnlyList<VersionedUserProfile>> ListAsync(CancellationToken cancellationToken = default);
  Task<VersionedUserProfile?> FindAsync(Guid id, CancellationToken cancellationToken = default);
  Task<VersionedUserProfile> CreateAsync(UserProfile profile, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<VersionedUserProfile> UpdateAsync(UserProfile profile, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> SetArchivedAsync(Guid id, bool isArchived, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
}

public sealed class ProfileStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IProfileStore
{
  public async Task<IReadOnlyList<VersionedUserProfile>> ListAsync(CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var entities = await context.UserProfiles.AsNoTracking()
      .Include(entity => entity.HeartRateZones)
      .OrderBy(entity => entity.NormalizedDisplayName)
      .ToListAsync(cancellationToken);
    return entities.Select(Map).ToArray();
  }

  public async Task<VersionedUserProfile?> FindAsync(Guid id, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var entity = await context.UserProfiles.AsNoTracking()
      .Include(candidate => candidate.HeartRateZones)
      .SingleOrDefaultAsync(candidate => candidate.Id == id, cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<VersionedUserProfile> CreateAsync(
    UserProfile profile,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(profile);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var entity = CreateEntity(profile, nowUtc, version: 1);
    context.UserProfiles.Add(entity);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return Map(entity);
  }

  public async Task<VersionedUserProfile> UpdateAsync(
    UserProfile profile,
    int expectedVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(profile);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var entity = await context.UserProfiles
      .SingleOrDefaultAsync(candidate => candidate.Id == profile.Id, cancellationToken)
      ?? throw new KeyNotFoundException($"Profile {profile.Id} was not found.");
    RequireVersion(entity.Version, expectedVersion);

    var existingZones = await context.HeartRateZones
      .Where(zone => zone.UserProfileId == profile.Id)
      .ToListAsync(cancellationToken);

    entity.DisplayName = profile.DisplayName;
    entity.NormalizedDisplayName = NormalizeName(profile.DisplayName);
    entity.UnitSystem = profile.UnitSystem.ToString();
    entity.WeightKilograms = profile.WeightKilograms;
    entity.MaximumHeartRateBpm = profile.MaximumHeartRateBpm;
    entity.MaximumSpeedKph = profile.MaximumSpeedKph;
    entity.HeartRateIncreaseStepKph = profile.HeartRateController.IncreaseStepKph;
    entity.HeartRateIncreaseCooldownSeconds = profile.HeartRateController.IncreaseCooldownSeconds;
    entity.HeartRateDecreaseStepKph = profile.HeartRateController.DecreaseStepKph;
    entity.HeartRateDecreaseCooldownSeconds = profile.HeartRateController.DecreaseCooldownSeconds;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    context.HeartRateZones.RemoveRange(existingZones);
    context.HeartRateZones.AddRange(profile.HeartRateZones.Select(zone => CreateZone(profile.Id, zone)));

    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return new VersionedUserProfile(profile, entity.Version, entity.IsArchived, entity.ArchivedAtUtc);
  }

  public async Task<bool> SetArchivedAsync(
    Guid id,
    bool isArchived,
    int expectedVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var entity = await context.UserProfiles.SingleOrDefaultAsync(candidate => candidate.Id == id, cancellationToken);
    if (entity is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }

    RequireVersion(entity.Version, expectedVersion);
    entity.IsArchived = isArchived;
    entity.ArchivedAtUtc = isArchived ? nowUtc : null;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  private static UserProfileEntity CreateEntity(UserProfile profile, DateTimeOffset nowUtc, int version) => new()
  {
    Id = profile.Id,
    DisplayName = profile.DisplayName,
    NormalizedDisplayName = NormalizeName(profile.DisplayName),
    UnitSystem = profile.UnitSystem.ToString(),
    WeightKilograms = profile.WeightKilograms,
    MaximumHeartRateBpm = profile.MaximumHeartRateBpm,
    MaximumSpeedKph = profile.MaximumSpeedKph,
    HeartRateIncreaseStepKph = profile.HeartRateController.IncreaseStepKph,
    HeartRateIncreaseCooldownSeconds = profile.HeartRateController.IncreaseCooldownSeconds,
    HeartRateDecreaseStepKph = profile.HeartRateController.DecreaseStepKph,
    HeartRateDecreaseCooldownSeconds = profile.HeartRateController.DecreaseCooldownSeconds,
    Version = version,
    CreatedAtUtc = nowUtc,
    UpdatedAtUtc = nowUtc,
    HeartRateZones = profile.HeartRateZones.Select(zone => CreateZone(profile.Id, zone)).ToList(),
  };

  private static HeartRateZoneEntity CreateZone(Guid profileId, HeartRateZone zone) => new()
  {
    Id = Guid.NewGuid(),
    UserProfileId = profileId,
    Number = zone.Number,
    Name = zone.Name,
    MinimumBpm = zone.MinimumBpm,
    MaximumBpm = zone.MaximumBpm,
  };

  private static VersionedUserProfile Map(UserProfileEntity entity) => new(
    new UserProfile(
      entity.Id,
      entity.DisplayName,
      Enum.Parse<UnitSystem>(entity.UnitSystem),
      entity.WeightKilograms,
      entity.MaximumHeartRateBpm,
      entity.MaximumSpeedKph,
      entity.HeartRateZones
        .OrderBy(zone => zone.Number)
        .Select(zone => new HeartRateZone(zone.Number, zone.Name, zone.MinimumBpm, zone.MaximumBpm))
        .ToArray(),
      new HeartRateControllerSettings(
        entity.HeartRateIncreaseStepKph,
        entity.HeartRateIncreaseCooldownSeconds,
        entity.HeartRateDecreaseStepKph,
        entity.HeartRateDecreaseCooldownSeconds)),
    entity.Version,
    entity.IsArchived,
    entity.ArchivedAtUtc);

  private static string NormalizeName(string displayName) => displayName.Trim().ToUpperInvariant();

  private static void RequireVersion(int actual, int expected)
  {
    if (actual != expected)
    {
      throw new DbUpdateConcurrencyException($"Expected version {expected}, but stored version is {actual}.");
    }
  }
}
