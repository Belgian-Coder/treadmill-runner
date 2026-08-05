using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminStoreTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string DatabasePath => Path.Combine(directory, "garmin-store.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task OAuth_state_is_profile_bound_expiring_and_one_time()
  {
    var factory = await CreateDatabaseAsync();
    Guid profileId = await SeedProfileAsync(factory, "Marc");
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T18:00:00Z");
    string stateHash = new string('a', 64);
    await store.SaveOAuthStateAsync(new GarminOAuthStateRecord(
      stateHash, profileId, "protected-verifier", "https://runner.example/callback", now.AddMinutes(10)), now);

    GarminOAuthStateRecord? consumed = await store.ConsumeOAuthStateAsync(stateHash, now.AddMinutes(1));

    Assert.NotNull(consumed);
    Assert.Equal(profileId, consumed.UserProfileId);
    Assert.Null(await store.ConsumeOAuthStateAsync(stateHash, now.AddMinutes(2)));

    string expiredHash = new string('b', 64);
    await store.SaveOAuthStateAsync(new GarminOAuthStateRecord(
      expiredHash, profileId, "protected-verifier", "https://runner.example/callback", now.AddMinutes(-1)), now.AddMinutes(-2));
    Assert.Null(await store.ConsumeOAuthStateAsync(expiredHash, now));
  }

  [Fact]
  public async Task One_Garmin_subject_cannot_bind_to_two_profiles()
  {
    var factory = await CreateDatabaseAsync();
    Guid marc = await SeedProfileAsync(factory, "Marc");
    Guid partner = await SeedProfileAsync(factory, "Partner");
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T18:00:00Z");
    await store.ConnectAsync(Link(marc, "garmin-subject", now), now);

    InvalidOperationException exception = await Assert.ThrowsAsync<InvalidOperationException>(
      () => store.ConnectAsync(Link(partner, "garmin-subject", now), now));

    Assert.Contains("another runner", exception.Message, StringComparison.OrdinalIgnoreCase);
    Assert.Null(await store.FindLinkAsync(partner));
  }

  [Fact]
  public async Task Sync_outbox_is_idempotent_leased_and_removed_with_account()
  {
    var factory = await CreateDatabaseAsync();
    Guid profileId = await SeedProfileAsync(factory, "Marc");
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T18:00:00Z");
    await store.ConnectAsync(Link(profileId, "garmin-marc", now), now);
    var document = new GarminSyncDocument("Workout", Guid.NewGuid(), "sha-v1", "{\"title\":\"Easy run\"}");

    Assert.Equal(1, await store.EnqueueAsync(profileId, [document], now));
    Assert.Equal(0, await store.EnqueueAsync(profileId, [document], now));
    GarminSyncItemRecord leased = Assert.IsType<GarminSyncItemRecord>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(1), 5));
    Assert.Equal(1, leased.AttemptCount);
    await store.MarkFailedAsync(leased.Id, "temporary", now.AddMinutes(2), now);
    Assert.Null(await store.LeaseNextAsync(now.AddMinutes(1), TimeSpan.FromMinutes(1), 5));
    GarminSyncItemRecord retry = Assert.IsType<GarminSyncItemRecord>(await store.LeaseNextAsync(now.AddMinutes(2), TimeSpan.FromMinutes(1), 5));
    await store.MarkSyncedAsync(retry.Id, "remote-1", now.AddMinutes(2));
    Assert.Equal(1, (await store.GetQueueStatusAsync(profileId)).Synced);

    Assert.True(await store.DisconnectAsync(profileId));
    GarminSyncQueueStatus afterDisconnect = await store.GetQueueStatusAsync(profileId);
    Assert.Equal(new GarminSyncQueueStatus(0, 0, 0), afterDisconnect);
  }

  [Fact]
  public async Task Manual_retry_resets_only_known_retryable_terminal_failures()
  {
    var factory = await CreateDatabaseAsync();
    Guid profileId = await SeedProfileAsync(factory, "Marc");
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T18:00:00Z");
    await store.ConnectAsync(Link(profileId, "garmin-marc", now), now);
    await store.EnqueueAsync(profileId, [new GarminSyncDocument("Workout", Guid.NewGuid(), "v1", "{}")], now);
    GarminSyncItemRecord retryable = Assert.IsType<GarminSyncItemRecord>(await store.LeaseNextAsync(now, TimeSpan.FromMinutes(1), 5));
    await store.MarkTerminalFailureAsync(
      retryable.Id,
      "Garmin Connect is temporarily unavailable.",
      5,
      now);

    Assert.Equal(1, await store.ResetRetryableTerminalFailuresAsync(profileId, 5, now.AddMinutes(1)));
    GarminSyncItemRecord reset = Assert.IsType<GarminSyncItemRecord>(await store.LeaseNextAsync(now.AddMinutes(1), TimeSpan.FromMinutes(1), 5));
    Assert.Equal(1, reset.AttemptCount);
    await store.MarkTerminalFailureAsync(
      reset.Id,
      "Garmin may have received this item, so it was not retried automatically. Reconnect or reconcile it using the approved provider workflow.",
      5,
      now.AddMinutes(1));

    Assert.Equal(0, await store.ResetRetryableTerminalFailuresAsync(profileId, 5, now.AddMinutes(2)));
    Assert.Null(await store.LeaseNextAsync(now.AddMinutes(2), TimeSpan.FromMinutes(1), 5));
  }

  private static GarminAccountLinkRecord Link(Guid profileId, string subject, DateTimeOffset now) => new(
    Guid.NewGuid(), profileId, subject, subject, "protected-access", "protected-refresh", now.AddHours(1), "training", now, null, null, null, 1);

  private async Task<Microsoft.EntityFrameworkCore.IDbContextFactory<TreadmillRunnerDbContext>> CreateDatabaseAsync()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using var context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    return factory;
  }

  private static async Task<Guid> SeedProfileAsync(
    Microsoft.EntityFrameworkCore.IDbContextFactory<TreadmillRunnerDbContext> factory,
    string name)
  {
    Guid id = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T17:00:00Z");
    await using var context = await factory.CreateDbContextAsync();
    context.UserProfiles.Add(new UserProfileEntity
    {
      Id = id,
      DisplayName = name,
      NormalizedDisplayName = name.ToUpperInvariant(),
      UnitSystem = "Metric",
      WeightKilograms = 70,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    });
    await context.SaveChangesAsync();
    return id;
  }
}
