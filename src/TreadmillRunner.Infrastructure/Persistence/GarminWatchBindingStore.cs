using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record GarminWatchBinding(
  Guid Id,
  Guid UserProfileId,
  string RunnerName,
  string DeviceLabel,
  DateTimeOffset CreatedAtUtc,
  DateTimeOffset? LastSeenAtUtc,
  int Version);

public interface IGarminWatchBindingStore
{
  Task<GarminWatchBinding?> FindForProfileAsync(Guid profileId, CancellationToken cancellationToken = default);
  Task<GarminWatchBinding?> FindByTokenHashAsync(string tokenSha256, DateTimeOffset seenAtUtc, CancellationToken cancellationToken = default);
  Task<GarminWatchBinding> ReplaceAsync(Guid profileId, string deviceLabel, string tokenSha256, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> RevokeAsync(Guid profileId, int expectedVersion, CancellationToken cancellationToken = default);
}

public sealed class GarminWatchBindingStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IGarminWatchBindingStore
{
  public async Task<GarminWatchBinding?> FindForProfileAsync(
    Guid profileId,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminWatchBindingEntity? entity = await context.GarminWatchBindings.AsNoTracking()
      .Include(item => item.UserProfile)
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<GarminWatchBinding?> FindByTokenHashAsync(
    string tokenSha256,
    DateTimeOffset seenAtUtc,
    CancellationToken cancellationToken = default)
  {
    if (tokenSha256.Length != 64) return null;
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminWatchBindingEntity? entity = await context.GarminWatchBindings
      .Include(item => item.UserProfile)
      .SingleOrDefaultAsync(item => item.TokenSha256 == tokenSha256, cancellationToken);
    if (entity is null) return null;
    if (entity.LastSeenAtUtc is null || seenAtUtc - entity.LastSeenAtUtc.Value >= TimeSpan.FromMinutes(5))
    {
      entity.LastSeenAtUtc = seenAtUtc;
      await context.SaveChangesAsync(cancellationToken);
    }
    return Map(entity);
  }

  public async Task<GarminWatchBinding> ReplaceAsync(
    Guid profileId,
    string deviceLabel,
    string tokenSha256,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    deviceLabel = deviceLabel.Trim();
    if (deviceLabel.Length is < 1 or > 100) throw new ArgumentOutOfRangeException(nameof(deviceLabel));
    if (tokenSha256.Length != 64) throw new ArgumentException("A SHA-256 token hash is required.", nameof(tokenSha256));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    UserProfileEntity profile = await context.UserProfiles.SingleOrDefaultAsync(
      item => item.Id == profileId && !item.IsArchived,
      cancellationToken) ?? throw new InvalidOperationException("The runner profile does not exist or is archived.");
    GarminWatchBindingEntity? existing = await context.GarminWatchBindings
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (existing is null)
    {
      existing = new GarminWatchBindingEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = profileId,
        DeviceLabel = deviceLabel,
        TokenSha256 = tokenSha256,
        CreatedAtUtc = nowUtc,
        Version = 1,
        UserProfile = profile,
      };
      context.GarminWatchBindings.Add(existing);
    }
    else
    {
      existing.DeviceLabel = deviceLabel;
      existing.TokenSha256 = tokenSha256;
      existing.CreatedAtUtc = nowUtc;
      existing.LastSeenAtUtc = null;
      existing.Version++;
      existing.UserProfile = profile;
    }
    await context.SaveChangesAsync(cancellationToken);
    return Map(existing);
  }

  public async Task<bool> RevokeAsync(
    Guid profileId,
    int expectedVersion,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    GarminWatchBindingEntity? entity = await context.GarminWatchBindings
      .SingleOrDefaultAsync(item => item.UserProfileId == profileId, cancellationToken);
    if (entity is null) return false;
    if (entity.Version != expectedVersion)
      throw new DbUpdateConcurrencyException("The watch binding changed; refresh before revoking it.");
    context.GarminWatchBindings.Remove(entity);
    await context.SaveChangesAsync(cancellationToken);
    return true;
  }

  private static GarminWatchBinding Map(GarminWatchBindingEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.UserProfile.DisplayName,
    entity.DeviceLabel,
    entity.CreatedAtUtc,
    entity.LastSeenAtUtc,
    entity.Version);
}
