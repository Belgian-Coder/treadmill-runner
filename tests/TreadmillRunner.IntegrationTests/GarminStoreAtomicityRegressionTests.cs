using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminStoreAtomicityRegressionTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(directory, "garmin-store-regressions.db");

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
  public async Task Replacing_oauth_state_rolls_back_the_removal_when_the_new_state_is_invalid()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId) = await CreateDatabaseAsync();
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    string originalHash = new('a', 64);
    await store.SaveOAuthStateAsync(
      new GarminOAuthStateRecord(
        originalHash,
        profileId,
        "protected-original",
        "https://runner.example/callback",
        now.AddMinutes(10)),
      now);

    await Assert.ThrowsAsync<DbUpdateException>(() => store.SaveOAuthStateAsync(
      new GarminOAuthStateRecord(
        "invalid-hash",
        profileId,
        "protected-replacement",
        "https://runner.example/callback",
        now.AddMinutes(11)),
      now.AddMinutes(1)));

    GarminOAuthStateRecord retained = Assert.IsType<GarminOAuthStateRecord>(
      await store.ConsumeOAuthStateAsync(originalHash, now.AddMinutes(2)));
    Assert.Equal("protected-original", retained.ProtectedCodeVerifier);
  }

  [Fact]
  public async Task One_enqueue_batch_deduplicates_repeated_Garmin_documents()
  {
    (IDbContextFactory<TreadmillRunnerDbContext> factory, Guid profileId) = await CreateDatabaseAsync();
    var store = new GarminStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
    await store.ConnectAsync(
      new GarminAccountLinkRecord(
        Guid.NewGuid(),
        profileId,
        "garmin-runner",
        "Runner",
        "protected-access",
        "protected-refresh",
        now.AddHours(1),
        "training",
        now,
        null,
        null,
        null,
        1),
      now);
    var document = new GarminSyncDocument(
      "Workout",
      Guid.NewGuid(),
      "sha-v1",
      "{\"title\":\"Easy run\"}");

    int inserted = await store.EnqueueAsync(profileId, [document, document], now);

    Assert.Equal(1, inserted);
    Assert.Equal(1, (await store.GetQueueStatusAsync(profileId)).Pending);
  }

  private async Task<(IDbContextFactory<TreadmillRunnerDbContext> Factory, Guid ProfileId)>
    CreateDatabaseAsync()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory =
      TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T07:00:00Z");
    var profile = new UserProfileEntity
    {
      Id = Guid.NewGuid(),
      DisplayName = "Runner",
      NormalizedDisplayName = "RUNNER",
      UnitSystem = "Metric",
      WeightKilograms = 72,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    };
    context.UserProfiles.Add(profile);
    await context.SaveChangesAsync();
    return (factory, profile.Id);
  }
}
