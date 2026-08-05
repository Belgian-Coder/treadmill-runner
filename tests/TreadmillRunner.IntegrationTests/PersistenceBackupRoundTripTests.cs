using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PersistenceBackupRoundTripTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string SourcePath => Path.Combine(_directory, "source.db");
  private string BackupPath => Path.Combine(_directory, "backup.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
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
  public async Task Online_backup_opens_as_isolated_integral_semantic_round_trip()
  {
    var sourceFactory = TreadmillRunnerDatabase.CreateFactory(SourcePath);
    await using (var source = await sourceFactory.CreateDbContextAsync())
    {
      await source.Database.MigrateAsync();
      await SeedCompleteGraphAsync(source);
    }

    var backup = new SqliteOnlineBackupService(sourceFactory);
    await backup.BackupAsync(BackupPath);

    var backupFactory = TreadmillRunnerDatabase.CreateFactory(BackupPath);
    await using var restored = await backupFactory.CreateDbContextAsync();

    Assert.Equal("ok", await ExecuteIntegrityCheckAsync(restored));
    Assert.Equal(1, await restored.UserProfiles.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.HeartRateZones.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.Workouts.AsNoTracking().CountAsync());
    var revision = await restored.WorkoutRevisions.AsNoTracking().SingleAsync();
    Assert.Equal("{\"blocks\":[],\"schemaVersion\":1,\"title\":\"Morning Run\"}", revision.DefinitionJson);
    Assert.Equal(new string('b', 64), revision.ContentSha256);
    Assert.Equal(1, await restored.ImportAudits.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.CalendarSeries.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.CalendarSeriesOptions.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.CalendarExceptions.AsNoTracking().CountAsync());
    Assert.Equal(1, await restored.CalendarExceptionOptions.AsNoTracking().CountAsync());
    var selection = await restored.TrainingDaySelections.AsNoTracking().SingleAsync();
    Assert.Equal(new DateOnly(2026, 8, 4), selection.LocalDate);
    Assert.Equal(revision.Id, selection.WorkoutRevisionId);
    Assert.Equal(1, await restored.OperationReceipts.AsNoTracking().CountAsync());
  }

  private static async Task SeedCompleteGraphAsync(TreadmillRunnerDbContext context)
  {
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var profile = new UserProfileEntity
    {
      Id = Guid.NewGuid(),
      DisplayName = "Runner",
      NormalizedDisplayName = "RUNNER",
      UnitSystem = "Metric",
      WeightKilograms = 72.5,
      MaximumHeartRateBpm = 190,
      MaximumSpeedKph = 18,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    };
    profile.HeartRateZones.Add(new HeartRateZoneEntity
    {
      Id = Guid.NewGuid(),
      Number = 2,
      Name = "Aerobic",
      MinimumBpm = 120,
      MaximumBpm = 145,
    });

    var workout = new WorkoutEntity
    {
      Id = Guid.NewGuid(),
      Name = "Morning Run",
      CreatedAtUtc = now,
    };
    var revision = new WorkoutRevisionEntity
    {
      Id = Guid.NewGuid(),
      RevisionNumber = 1,
      DefinitionJson = "{\"blocks\":[],\"schemaVersion\":1,\"title\":\"Morning Run\"}",
      ContentSha256 = new string('b', 64),
      CreatedAtUtc = now,
    };
    workout.Revisions.Add(revision);

    var series = new CalendarSeriesEntity
    {
      Id = Guid.NewGuid(),
      UserProfileId = profile.Id,
      Name = "Tuesday Plan",
      TimeZoneId = "Europe/Brussels",
      StartDate = new DateOnly(2026, 8, 1),
      EndDate = new DateOnly(2026, 12, 31),
      IntervalWeeks = 1,
      WeekdayMask = 1 << 2,
      CreatedAtUtc = now,
      Version = 1,
    };
    series.Options.Add(new CalendarSeriesOptionEntity
    {
      Id = Guid.NewGuid(),
      WorkoutRevisionId = revision.Id,
      DisplayOrder = 0,
    });
    var exception = new CalendarExceptionEntity
    {
      Id = Guid.NewGuid(),
      LocalDate = new DateOnly(2026, 8, 11),
      Kind = "Replace",
      Note = "Easy week",
    };
    exception.Options.Add(new CalendarExceptionOptionEntity
    {
      Id = Guid.NewGuid(),
      WorkoutRevisionId = revision.Id,
      DisplayOrder = 0,
    });
    series.Exceptions.Add(exception);

    await using var transaction = await context.Database.BeginTransactionAsync();
    context.AddRange(profile, workout);
    await context.SaveChangesAsync();
    context.AddRange(
      new ImportAuditEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = profile.Id,
        WorkoutId = workout.Id,
        WorkoutRevisionId = revision.Id,
        OriginalFileName = "morning.json",
        Format = "native-json",
        SourceSha256 = new string('c', 64),
        WarningSummaryJson = "[]",
        ImportedAtUtc = now,
      },
      series,
      new TrainingDaySelectionEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = profile.Id,
        LocalDate = new DateOnly(2026, 8, 4),
        CalendarSeriesId = series.Id,
        WorkoutRevisionId = revision.Id,
        SelectedAtUtc = now,
      },
      new OperationReceiptEntity
      {
        Id = Guid.NewGuid(),
        ClientOperationId = Guid.NewGuid(),
        OperationType = "workout.import.confirm",
        RequestFingerprint = new string('d', 64),
        StatusCode = 201,
        OutcomeJson = "{\"revisionNumber\":1}",
        CreatedAtUtc = now,
      });
    await context.SaveChangesAsync();
    await transaction.CommitAsync();
  }

  private static async Task<string> ExecuteIntegrityCheckAsync(TreadmillRunnerDbContext context)
  {
    await context.Database.OpenConnectionAsync();
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = "PRAGMA integrity_check;";
    return (string)(await command.ExecuteScalarAsync())!;
  }
}
