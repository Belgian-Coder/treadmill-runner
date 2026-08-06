using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class TreadmillMaintenanceStoreTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private string DatabasePath => Path.Combine(directory, "maintenance.db");
  public Task InitializeAsync() { Directory.CreateDirectory(directory); return Task.CompletedTask; }
  public Task DisposeAsync() { Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools(); if (Directory.Exists(directory)) Directory.Delete(directory, true); return Task.CompletedTask; }

  [Fact]
  public async Task Reminder_requires_baseline_and_counts_only_terminal_hardware_distance_across_profiles()
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      await context.Database.MigrateAsync();
      DateTimeOffset now = DateTimeOffset.Parse("2026-08-06T10:00:00Z");
      Guid enrollmentId = Guid.NewGuid();
      context.DeviceEnrollments.Add(new DeviceEnrollmentEntity { Id = enrollmentId, Role = "Treadmill", DeviceId = "local", ProtocolId = "omega", IdentityFingerprint = new string('a', 64), DisplayName = "Omega Z", TelemetryMode = "Ftms", CapabilitiesJson = "{}", Evidence = "Verified", Version = 1, CreatedAtUtc = now, UpdatedAtUtc = now });
      context.TreadmillMaintenancePolicies.Add(new TreadmillMaintenancePolicyEntity { Id = Guid.NewGuid(), DeviceEnrollmentId = enrollmentId, IntervalMonths = 3, DistanceIntervalKilometers = 10, Version = 1, CreatedAtUtc = now, UpdatedAtUtc = now });
      AddSession(context, SessionOrigin.Hardware, "Completed", 4);
      AddSession(context, SessionOrigin.Hardware, "Stopped", 3);
      AddSession(context, SessionOrigin.Simulator, "Completed", 100);
      AddSession(context, SessionOrigin.SystemTest, "Completed", 100);
      AddSession(context, SessionOrigin.Hardware, "Running", 100);
      await context.SaveChangesAsync();
    }
    DateTimeOffset current = DateTimeOffset.Parse("2026-08-06T10:00:00Z");
    var store = new TreadmillMaintenanceStore(factory);
    TreadmillMaintenanceSnapshot setup = Assert.IsType<TreadmillMaintenanceSnapshot>(await store.GetAsync(current));
    Assert.Equal(TreadmillMaintenanceState.SetupRequired, setup.State);
    Assert.Equal(7, setup.AppTrackedHardwareDistanceKilometers);

    var recordOperation = new PersistenceWriteOperation(Guid.NewGuid(), "maintenance.record", 200, "{}", current, new string('b', 64));
    TreadmillMaintenanceSnapshot baseline = await store.RecordAsync(current.AddMonths(-2), "Inspected", setup.Policy.Version,
      recordOperation, current);
    Assert.Equal(TreadmillMaintenanceState.Current, baseline.State);
    await Assert.ThrowsAsync<OperationReplayException>(() => store.RecordAsync(
      current.AddMonths(-2), "Inspected", setup.Policy.Version, recordOperation, current));
    TreadmillMaintenanceSnapshot dateDue = Assert.IsType<TreadmillMaintenanceSnapshot>(await store.GetAsync(current.AddMonths(2)));
    Assert.Equal(TreadmillMaintenanceState.DueByDate, dateDue.State);

    TreadmillMaintenanceSnapshot changed = await store.UpdatePolicyAsync(6, 20, baseline.Policy.Version,
      new PersistenceWriteOperation(Guid.NewGuid(), "maintenance.policy", 200, "{}", current, new string('c', 64)), current);
    Assert.Equal(6, changed.Policy.IntervalMonths);
    Assert.Equal(20, changed.Policy.DistanceIntervalKilometers);
    Assert.Equal(TreadmillMaintenanceState.Current,
      Assert.IsType<TreadmillMaintenanceSnapshot>(await store.GetAsync(current.AddMonths(2))).State);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
    {
      AddSession(context, SessionOrigin.Hardware, "Completed", 21);
      await context.SaveChangesAsync();
    }
    TreadmillMaintenanceSnapshot due = Assert.IsType<TreadmillMaintenanceSnapshot>(await store.GetAsync(current));
    Assert.Equal(TreadmillMaintenanceState.DueByDistance, due.State);
    Assert.True(due.IsDue);
  }

  private static void AddSession(TreadmillRunnerDbContext context, SessionOrigin origin, string state, double distance)
  {
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-06T09:00:00Z");
    Guid profileId = Guid.NewGuid();
    Guid workoutId = Guid.NewGuid();
    Guid revisionId = Guid.NewGuid();
    context.UserProfiles.Add(new UserProfileEntity { Id = profileId, DisplayName = profileId.ToString("N"), NormalizedDisplayName = profileId.ToString("N").ToUpperInvariant(), UnitSystem = "Metric", WeightKilograms = 70, Version = 1, CreatedAtUtc = now, UpdatedAtUtc = now });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = workoutId.ToString("N"), Kind = "Structured", CreatedAtUtc = now });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity { Id = revisionId, WorkoutId = workoutId, RevisionNumber = 1, DefinitionJson = "{}", ContentSha256 = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(revisionId.ToByteArray())), CreatedAtUtc = now });
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = Guid.NewGuid(),
      UserProfileId = profileId,
      UserProfileName = "Runner",
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Run",
      State = state,
      SessionOrigin = origin.ToString(),
      ArmedAtUtc = now,
      StartedAtUtc = now,
      EndedAtUtc = state == "Running" ? null : now.AddMinutes(10),
      DurationSeconds = 600,
      DistanceKilometers = distance,
      ControllerConfigurationJson = "{}",
      MetricAlgorithmVersion = "v1",
    });
  }
}
