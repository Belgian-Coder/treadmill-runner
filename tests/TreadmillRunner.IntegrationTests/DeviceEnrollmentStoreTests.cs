using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class DeviceEnrollmentStoreTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "devices.db"));
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  public Task DisposeAsync()
  {
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Store_allows_one_treadmill_and_multiple_distinct_heart_rate_sensors()
  {
    var store = new DeviceEnrollmentStore(_factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-03T18:00:00Z");
    VersionedDeviceEnrollment treadmill = await store.EnrollAsync(
      Treadmill("A1B2C3D4E5F6"), now, Op("device.enroll", now));
    VersionedDeviceEnrollment heartRate = await store.EnrollAsync(
      HeartRate("102030405060"), now, Op("device.enroll", now));
    VersionedDeviceEnrollment watch = await store.EnrollAsync(
      HeartRate("AABBCCDDEEFF", "Garmin fēnix 8"), now, Op("device.enroll", now));

    Assert.Equal(3, (await store.ListActiveAsync()).Count);
    Assert.Equal(1, treadmill.Version);
    Assert.Equal(DeviceRole.HeartRate, heartRate.Enrollment.Role);
    await Assert.ThrowsAsync<DbUpdateException>(() => store.EnrollAsync(
      Treadmill("112233445566"), now, Op("device.enroll", now)));
    await Assert.ThrowsAsync<DbUpdateException>(() => store.EnrollAsync(
      HeartRate("AABBCCDDEEFF", "Duplicate watch"), now, Op("device.enroll", now)));
    watch = await store.RenameAsync(
      watch.Enrollment.Id,
      "Marc's Garmin",
      watch.Version,
      now.AddSeconds(1),
      Op("device.rename", now));
    Assert.Equal("Marc's Garmin", watch.Enrollment.DisplayName);
    Assert.Equal(2, watch.Version);
    Assert.True(await store.ForgetByIdAsync(
      watch.Enrollment.Id, watch.Version, now, Op("device.forget", now)));

    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() => store.ForgetAsync(
      DeviceRole.Treadmill, expectedVersion: 2, now, Op("device.forget", now)));
    Assert.True(await store.ForgetAsync(
      DeviceRole.Treadmill, treadmill.Version, now, Op("device.forget", now)));
    Assert.Null(await store.FindActiveAsync(DeviceRole.Treadmill));

    VersionedDeviceEnrollment replacement = await store.EnrollAsync(
      Treadmill("112233445566"), now.AddMinutes(1), Op("device.enroll", now));
    Assert.Equal("112233445566", replacement.Enrollment.DeviceId);
  }

  [Fact]
  public async Task Product_specific_rename_promotes_generic_heart_rate_metadata_without_downgrading_known_identity()
  {
    var store = new DeviceEnrollmentStore(_factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T12:00:00Z");
    VersionedDeviceEnrollment generic = await store.EnrollAsync(
      HeartRate("102030405060", "Household HR"),
      now,
      Op("device.enroll.generic", now));
    Assert.Equal(HeartRateDeviceKind.Sensor, generic.Enrollment.HeartRateDeviceKind);
    Assert.Equal(HeartRateDeviceFamily.Other, generic.Enrollment.HeartRateDeviceFamily);

    VersionedDeviceEnrollment promoted = await store.RenameAsync(
      generic.Enrollment.Id,
      "Marc Polar H10",
      generic.Version,
      now.AddSeconds(1),
      Op("device.rename.promote", now.AddSeconds(1)));
    Assert.Equal(HeartRateDeviceKind.ChestStrap, promoted.Enrollment.HeartRateDeviceKind);
    Assert.Equal(HeartRateDeviceFamily.Polar, promoted.Enrollment.HeartRateDeviceFamily);

    VersionedDeviceEnrollment friendly = await store.RenameAsync(
      promoted.Enrollment.Id,
      "Marc's training sensor",
      promoted.Version,
      now.AddSeconds(2),
      Op("device.rename.friendly", now.AddSeconds(2)));
    Assert.Equal(HeartRateDeviceKind.ChestStrap, friendly.Enrollment.HeartRateDeviceKind);
    Assert.Equal(HeartRateDeviceFamily.Polar, friendly.Enrollment.HeartRateDeviceFamily);
  }

  [Fact]
  public async Task Assignments_are_profile_specific_and_preferred_selection_moves_atomically()
  {
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-04T10:00:00Z");
    var marc = new UserProfile(Guid.NewGuid(), "Marc", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(marc, now, Op("profile.create", now));
    VersionedDeviceEnrollment polar = await store.EnrollWithAssignmentsAsync(
      HeartRate("POLAR-A1B2C3D4"),
      [new HeartRateAssignmentPreference(marc.Id, 0, true, true)],
      now,
      Op("device.enroll", now));
    VersionedDeviceEnrollment watch = await store.EnrollWithAssignmentsAsync(
      HeartRate("GARMIN-FENIX8", "Garmin fēnix 8"),
      [new HeartRateAssignmentPreference(marc.Id, 1, true, false)],
      now,
      Op("device.enroll", now));

    await store.ConfigureHeartRateAssignmentsAsync(
      watch.Enrollment.Id,
      [new HeartRateAssignmentPreference(marc.Id, 0, true, true)],
      now.AddMinutes(1),
      Op("device.assign-heart-rate", now));

    IReadOnlyList<HeartRateDeviceAssignment> assignments = await store.ListHeartRateAssignmentsAsync();
    Assert.False(assignments.Single(item => item.DeviceEnrollmentId == polar.Enrollment.Id).IsPreferred);
    Assert.True(assignments.Single(item => item.DeviceEnrollmentId == watch.Enrollment.Id).IsPreferred);
  }

  [Fact]
  public async Task Forgetting_heart_rate_sensor_removes_assignments_and_allows_preferred_reenrollment()
  {
    var profiles = new ProfileStore(_factory);
    var store = new DeviceEnrollmentStore(_factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-21T18:00:00Z");
    var marc = new UserProfile(Guid.NewGuid(), "Marc", UnitSystem.Metric, 75, 190, 18, []);
    await profiles.CreateAsync(marc, now, Op("profile.create", now));
    VersionedDeviceEnrollment polar = await store.EnrollWithAssignmentsAsync(
      HeartRate("POLAR-OLD"),
      [new HeartRateAssignmentPreference(marc.Id, 0, true, true)],
      now,
      Op("device.enroll", now));
    VersionedDeviceEnrollment watch = await store.EnrollWithAssignmentsAsync(
      HeartRate("GARMIN-WATCH", "Garmin fēnix 8"),
      [new HeartRateAssignmentPreference(marc.Id, 1, true, false)],
      now,
      Op("device.enroll", now));

    Assert.True(await store.ForgetByIdAsync(
      polar.Enrollment.Id,
      polar.Version,
      now.AddMinutes(1),
      Op("device.forget", now)));

    HeartRateDeviceAssignment remaining = Assert.Single(await store.ListHeartRateAssignmentsAsync());
    Assert.Equal(watch.Enrollment.Id, remaining.DeviceEnrollmentId);
    VersionedDeviceEnrollment replacement = await store.EnrollWithAssignmentsAsync(
      HeartRate("POLAR-NEW"),
      [new HeartRateAssignmentPreference(marc.Id, 0, true, true)],
      now.AddMinutes(2),
      Op("device.enroll", now));
    IReadOnlyList<HeartRateDeviceAssignment> assignments = await store.ListHeartRateAssignmentsAsync();
    Assert.Contains(assignments, item => item.DeviceEnrollmentId == replacement.Enrollment.Id && item.IsPreferred);
    Assert.Contains(assignments, item => item.DeviceEnrollmentId == watch.Enrollment.Id && !item.IsPreferred);

    Assert.True(await store.ForgetByIdAsync(
      watch.Enrollment.Id,
      watch.Version,
      now.AddMinutes(3),
      Op("device.forget", now)));
    Assert.DoesNotContain(
      await store.ListHeartRateAssignmentsAsync(),
      item => item.DeviceEnrollmentId == watch.Enrollment.Id);
    Assert.True(await store.ForgetAsync(
      DeviceRole.HeartRate,
      replacement.Version,
      now.AddMinutes(4),
      Op("device.forget", now)));
    Assert.Empty(await store.ListHeartRateAssignmentsAsync());
  }

  private static DeviceEnrollment Treadmill(string deviceId) => new(
    Guid.NewGuid(), DeviceRole.Treadmill, deviceId, "horizon-omega-z", new string('a', 64),
    "Horizon Omega Z", "Omega Z", null, TreadmillTelemetryMode.Ftms,
    new TreadmillCapabilities(), TreadmillCapabilityEvidence.Unknown, null);

  private static DeviceEnrollment HeartRate(string deviceId, string name = "Polar H10") => new(
    Guid.NewGuid(), DeviceRole.HeartRate, deviceId, "bluetooth-heart-rate", new string('b', 64),
    name, "HR", null, null, null, TreadmillCapabilityEvidence.Unknown, null);

  private static PersistenceWriteOperation Op(string type, DateTimeOffset now) => new(
    Guid.NewGuid(), type, 200, "{}", now, new string('0', 64));
}
