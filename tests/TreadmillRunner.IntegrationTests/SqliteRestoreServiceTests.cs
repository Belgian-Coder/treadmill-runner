using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.IntegrationTests;

public sealed class SqliteRestoreServiceTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string LivePath => Path.Combine(_directory, "live.db");
  private string CandidatePath => Path.Combine(_directory, "candidate.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory)) Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Transactional_restore_replaces_live_database_with_integral_migrated_candidate()
  {
    var liveFactory = TreadmillRunnerDatabase.CreateFactory(LivePath);
    var candidateFactory = TreadmillRunnerDatabase.CreateFactory(CandidatePath);
    await SeedAsync(liveFactory, "Before");
    await SeedAsync(candidateFactory, "After");

    await new SqliteRestoreService(liveFactory).RestoreAsync(CandidatePath);

    await using TreadmillRunnerDbContext restored = await liveFactory.CreateDbContextAsync();
    Assert.Equal("After", (await restored.UserProfiles.AsNoTracking().SingleAsync()).DisplayName);
    Assert.Equal("ok", await SqliteRestoreService.IntegrityAsync(restored));
    Assert.Equal(13, (await restored.Database.GetAppliedMigrationsAsync()).Count());
  }

  [Fact]
  public async Task Invalid_restored_json_contract_is_rejected_and_live_database_is_retained()
  {
    var liveFactory = TreadmillRunnerDatabase.CreateFactory(LivePath);
    var candidateFactory = TreadmillRunnerDatabase.CreateFactory(CandidatePath);
    await SeedAsync(liveFactory, "Before");
    await SeedAsync(candidateFactory, "After");
    var now = new DateTimeOffset(2026, 8, 4, 20, 0, 0, TimeSpan.Zero);
    var workoutStore = new WorkoutStore(candidateFactory);
    await workoutStore.CreateAsync(
      Guid.NewGuid(),
      new WorkoutDefinition(1, "Candidate", null,
        [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(1)), new FixedSpeed(5), new FixedIncline(0))]),
      now,
      new PersistenceWriteOperation(Guid.NewGuid(), "workout.create", 201, "{}", now, new string('0', 64)));
    await using (TreadmillRunnerDbContext corrupt = await candidateFactory.CreateDbContextAsync())
    {
      await corrupt.Database.ExecuteSqlRawAsync("UPDATE OperationReceipts SET OutcomeJson = '{{not-json';");
    }

    await Assert.ThrowsAsync<InvalidDataException>(() =>
      new SqliteRestoreService(liveFactory).RestoreAsync(CandidatePath));

    await using TreadmillRunnerDbContext retained = await liveFactory.CreateDbContextAsync();
    Assert.Equal("Before", (await retained.UserProfiles.AsNoTracking().SingleAsync()).DisplayName);
  }

  private static async Task SeedAsync(
    IDbContextFactory<TreadmillRunnerDbContext> factory,
    string name)
  {
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    var now = new DateTimeOffset(2026, 8, 4, 20, 0, 0, TimeSpan.Zero);
    context.UserProfiles.Add(new UserProfileEntity
    {
      Id = Guid.NewGuid(),
      DisplayName = name,
      NormalizedDisplayName = name.ToUpperInvariant(),
      UnitSystem = "Metric",
      WeightKilograms = 70,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    });
    await context.SaveChangesAsync();
  }
}
