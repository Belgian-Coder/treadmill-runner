using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PersistenceSchemaTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(_directory, "schema.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Household_sensor_migration_backfills_existing_polar_for_every_active_profile()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    Guid profileId = Guid.NewGuid();
    Guid enrollmentId = Guid.NewGuid();
    Guid calendarSeriesId = Guid.NewGuid();
    await using (var oldContext = await factory.CreateDbContextAsync())
    {
      IMigrator migrator = oldContext.GetService<IMigrator>();
      await migrator.MigrateAsync("20260803224123_AddHeartRateControllerSettings");
      string now = "2026-08-04 10:00:00+00:00";
      await oldContext.Database.ExecuteSqlRawAsync(
        "INSERT INTO UserProfiles (Id,DisplayName,NormalizedDisplayName,UnitSystem,WeightKilograms,MaximumHeartRateBpm,MaximumSpeedKph,HeartRateIncreaseStepKph,HeartRateIncreaseCooldownSeconds,HeartRateDecreaseStepKph,HeartRateDecreaseCooldownSeconds,Version,IsArchived,ArchivedAtUtc,CreatedAtUtc,UpdatedAtUtc) VALUES ({0},'Marc','MARC','Metric',75,190,18,0.2,30,0.5,15,1,0,NULL,{2},{2});" +
        "INSERT INTO DeviceEnrollments (Id,Role,DeviceId,ProtocolId,IdentityFingerprint,DisplayName,ModelNumber,FirmwareRevision,TelemetryMode,CapabilitiesJson,Evidence,LastVerifiedAtUtc,Version,IsArchived,ArchivedAtUtc,CreatedAtUtc,UpdatedAtUtc) VALUES ({1},'HeartRate','POLAR-A1B2C3D4','bluetooth-heart-rate','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','Polar H10 A1B2C3D4','H10',NULL,NULL,NULL,'Unknown',NULL,1,0,NULL,{2},{2});" +
        "INSERT INTO CalendarSeries (Id,UserProfileId,Name,TimeZoneId,StartDate,EndDate,IntervalWeeks,WeekdayMask,Version,CreatedAtUtc) VALUES ({3},{0},'Existing schedule','Europe/Brussels','2026-08-04',NULL,1,2,1,{2});",
        profileId, enrollmentId, now, calendarSeriesId);
      await migrator.MigrateAsync();
    }

    await using var context = await factory.CreateDbContextAsync();
    DeviceEnrollmentEntity enrollment = await context.DeviceEnrollments.SingleAsync();
    HeartRateDeviceAssignmentEntity assignment = await context.HeartRateDeviceAssignments.SingleAsync();
    Assert.Equal("ChestStrap", enrollment.HeartRateDeviceKind);
    Assert.Equal("Polar", enrollment.HeartRateDeviceFamily);
    Assert.Equal(profileId, assignment.UserProfileId);
    Assert.Equal(enrollmentId, assignment.DeviceEnrollmentId);
    Assert.True(assignment.AutoConnect);
    Assert.True(assignment.IsPreferred);
    Assert.Equal(calendarSeriesId, (await context.CalendarSeries.SingleAsync()).ScheduleGroupId);
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory))
    {
      Directory.Delete(_directory, recursive: true);
    }

    return Task.CompletedTask;
  }

  [Fact]
  public async Task Initial_migration_creates_reviewed_schema_and_required_pragmas()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using var context = await factory.CreateDbContextAsync();

    await context.Database.MigrateAsync();

    Assert.Equal(11, (await context.Database.GetAppliedMigrationsAsync()).Count());
    Assert.Equal("wal", await ExecuteScalarAsync<string>(context, "PRAGMA journal_mode;"));
    Assert.Equal(1L, await ExecuteScalarAsync<long>(context, "PRAGMA foreign_keys;"));
    Assert.Equal(5000L, await ExecuteScalarAsync<long>(context, "PRAGMA busy_timeout;"));

    var tables = await ReadTableNamesAsync(context);
    AssertSuperset(
      tables,
      "DeviceEnrollments",
      "HeartRateDeviceAssignments",
      "UserProfiles",
      "HeartRateZones",
      "Workouts",
      "WorkoutRevisions",
      "WorkoutPrograms",
      "WorkoutProgramRevisions",
      "WorkoutProgramItems",
      "WorkoutProgramRuns",
      "ImportAudits",
      "CalendarSeries",
      "CalendarSeriesOptions",
      "CalendarExceptions",
      "CalendarExceptionOptions",
      "TrainingDaySelections",
      "WorkoutSessions",
      "SessionSamples",
      "SessionEvents",
      "GarminAccountLinks",
      "GarminOAuthStates",
      "GarminSyncItems",
      "GarminWatchBindings",
      "GarminActivityUploadAccounts",
      "GarminActivityUploadJobs",
      "OperationReceipts");
  }

  [Fact]
  public async Task Foreign_keys_and_operation_idempotency_are_enforced()
  {
    var factory = await CreateMigratedFactoryAsync();
    await using var context = await factory.CreateDbContextAsync();

    context.HeartRateZones.Add(new HeartRateZoneEntity
    {
      Id = Guid.NewGuid(),
      UserProfileId = Guid.NewGuid(),
      Number = 1,
      Name = "Recovery",
      MinimumBpm = 90,
      MaximumBpm = 110,
    });
    await Assert.ThrowsAsync<DbUpdateException>(() => context.SaveChangesAsync());

    context.ChangeTracker.Clear();
    var operationId = Guid.NewGuid();
    context.OperationReceipts.AddRange(
      Receipt(operationId, Guid.NewGuid()),
      Receipt(operationId, Guid.NewGuid()));
    await Assert.ThrowsAsync<DbUpdateException>(() => context.SaveChangesAsync());
  }

  [Fact]
  public async Task Workout_revisions_reject_update_and_delete_in_context_and_database()
  {
    var factory = await CreateMigratedFactoryAsync();
    var revisionId = Guid.NewGuid();
    await SeedWorkoutAsync(factory, revisionId);

    await using (var updateContext = await factory.CreateDbContextAsync())
    {
      var revision = await updateContext.WorkoutRevisions.SingleAsync();
      revision.DefinitionJson = "{\"schemaVersion\":2}";
      await Assert.ThrowsAsync<InvalidOperationException>(() => updateContext.SaveChangesAsync());
    }

    await using (var deleteContext = await factory.CreateDbContextAsync())
    {
      var revision = await deleteContext.WorkoutRevisions.SingleAsync();
      deleteContext.WorkoutRevisions.Remove(revision);
      await Assert.ThrowsAsync<InvalidOperationException>(() => deleteContext.SaveChangesAsync());
    }

    await using (var rawContext = await factory.CreateDbContextAsync())
    {
      await Assert.ThrowsAsync<SqliteException>(() => rawContext.Database.ExecuteSqlInterpolatedAsync(
        $"UPDATE WorkoutRevisions SET DefinitionJson = '{{}}' WHERE Id = {revisionId}"));
      await Assert.ThrowsAsync<SqliteException>(() => rawContext.Database.ExecuteSqlInterpolatedAsync(
        $"DELETE FROM WorkoutRevisions WHERE Id = {revisionId}"));
    }
  }

  private async Task<IDbContextFactory<TreadmillRunnerDbContext>> CreateMigratedFactoryAsync()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using var context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    return factory;
  }

  private static async Task SeedWorkoutAsync(
    IDbContextFactory<TreadmillRunnerDbContext> factory,
    Guid revisionId)
  {
    await using var context = await factory.CreateDbContextAsync();
    var workout = new WorkoutEntity
    {
      Id = Guid.NewGuid(),
      Name = "Steady Run",
      CreatedAtUtc = DateTimeOffset.Parse("2026-08-02T08:00:00Z"),
    };
    workout.Revisions.Add(new WorkoutRevisionEntity
    {
      Id = revisionId,
      RevisionNumber = 1,
      DefinitionJson = "{\"schemaVersion\":1}",
      ContentSha256 = new string('a', 64),
      CreatedAtUtc = workout.CreatedAtUtc,
    });
    context.Workouts.Add(workout);
    await context.SaveChangesAsync();
  }

  private static OperationReceiptEntity Receipt(Guid operationId, Guid id) => new()
  {
    Id = id,
    ClientOperationId = operationId,
    OperationType = "profile.create",
    RequestFingerprint = new string('a', 64),
    StatusCode = 201,
    OutcomeJson = "{\"id\":\"created\"}",
    CreatedAtUtc = DateTimeOffset.Parse("2026-08-02T08:00:00Z"),
  };

  private static async Task<T> ExecuteScalarAsync<T>(
    TreadmillRunnerDbContext context,
    string commandText)
  {
    await context.Database.OpenConnectionAsync();
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = commandText;
    return (T)(await command.ExecuteScalarAsync())!;
  }

  private static async Task<HashSet<string>> ReadTableNamesAsync(TreadmillRunnerDbContext context)
  {
    await context.Database.OpenConnectionAsync();
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = "SELECT name FROM sqlite_master WHERE type = 'table';";
    await using var reader = await command.ExecuteReaderAsync();
    var names = new HashSet<string>(StringComparer.Ordinal);
    while (await reader.ReadAsync())
    {
      names.Add(reader.GetString(0));
    }

    return names;
  }

  private static void AssertSuperset(HashSet<string> actual, params string[] expected)
  {
    foreach (var table in expected)
    {
      Assert.Contains(table, actual);
    }
  }
}
