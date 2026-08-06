using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class BleReliabilityStoreTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "reliability.db"));
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  [Fact]
  public async Task Persists_one_sanitized_incident_across_multiple_attempts_and_recovery()
  {
    var store = new BleReliabilityStore(_factory);
    Guid enrollmentId = Guid.NewGuid();
    DateTimeOffset started = new(2026, 8, 5, 8, 0, 0, TimeSpan.Zero);
    await store.BeginOrContinueIncidentAsync(
      enrollmentId,
      DeviceRole.HeartRate,
      "Polar H10",
      7,
      BleReliabilityFailureKind.NativeDisconnected,
      "The BLE device disconnected.",
      TimeSpan.FromSeconds(1.2),
      started);
    await store.ResolveIncidentAsync(
      enrollmentId,
      9,
      additionalFailedAttempts: 2,
      TimeSpan.FromSeconds(4.2),
      started.AddSeconds(8));

    BleReliabilityIncident incident = Assert.Single(await store.ListSinceAsync(started.AddDays(-1), 100));
    Assert.Equal(enrollmentId, incident.DeviceEnrollmentId);
    Assert.Equal("Polar H10", incident.DeviceDisplayName);
    Assert.Equal(3, incident.FailedAttemptCount);
    Assert.Equal(8, incident.RecoveryDuration!.Value.TotalSeconds);
    Assert.Equal(4.2, incident.MaximumReconnectDelaySeconds, precision: 3);
    Assert.DoesNotContain("RAW-DEVICE-ID-DO-NOT-LOG", incident.LastSanitizedFault, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Continues_an_unresolved_incident_instead_of_creating_duplicates()
  {
    var store = new BleReliabilityStore(_factory);
    Guid enrollmentId = Guid.NewGuid();
    DateTimeOffset now = DateTimeOffset.UtcNow;
    await store.BeginOrContinueIncidentAsync(enrollmentId, DeviceRole.Treadmill, "Omega Z", 1,
      BleReliabilityFailureKind.GattTimeout, "The BLE operation timed out.", TimeSpan.FromSeconds(1), now);
    await store.BeginOrContinueIncidentAsync(enrollmentId, DeviceRole.Treadmill, "Omega Z", 2,
      BleReliabilityFailureKind.GattTimeout, "The BLE operation timed out.", TimeSpan.FromSeconds(2), now.AddSeconds(2));

    BleReliabilityIncident incident = Assert.Single(await store.ListSinceAsync(now.AddMinutes(-1), 100));
    Assert.Null(incident.RecoveredAtUtc);
    Assert.Equal(2, incident.FailedAttemptCount);
    Assert.Equal(2, incident.MaximumReconnectDelaySeconds);
  }

  [Theory]
  [InlineData("UnknownRole", "NativeDisconnected", 100L, null)]
  [InlineData("Treadmill", "UnknownFailure", 100L, null)]
  [InlineData("Treadmill", "NativeDisconnected", -1L, null)]
  [InlineData("Treadmill", "NativeDisconnected", 100L, 99L)]
  public async Task Schema_rejects_invalid_enums_and_incident_times(
    string role,
    string failureKind,
    long startedAt,
    long? recoveredAt)
  {
    SqliteException exception = await Assert.ThrowsAsync<SqliteException>(() => InsertIncidentAsync(
      Guid.NewGuid(),
      Guid.NewGuid(),
      role,
      failureKind,
      startedAt,
      recoveredAt));

    Assert.Equal(19, exception.SqliteErrorCode);
  }

  [Fact]
  public async Task Schema_allows_only_one_open_incident_per_device()
  {
    Guid enrollmentId = Guid.NewGuid();
    await InsertIncidentAsync(
      Guid.NewGuid(),
      enrollmentId,
      DeviceRole.Treadmill.ToString(),
      BleReliabilityFailureKind.NativeDisconnected.ToString(),
      100,
      null);

    SqliteException duplicate = await Assert.ThrowsAsync<SqliteException>(() => InsertIncidentAsync(
      Guid.NewGuid(),
      enrollmentId,
      DeviceRole.Treadmill.ToString(),
      BleReliabilityFailureKind.GattTimeout.ToString(),
      101,
      null));
    Assert.Equal(19, duplicate.SqliteErrorCode);

    await using (TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync())
    {
      await context.Database.ExecuteSqlInterpolatedAsync(
        $"UPDATE BleReliabilityIncidents SET RecoveredAtUnixMilliseconds = {102L}, RecoveredConnectionGeneration = {2L} WHERE DeviceEnrollmentId = {enrollmentId};");
    }

    await InsertIncidentAsync(
      Guid.NewGuid(),
      enrollmentId,
      DeviceRole.Treadmill.ToString(),
      BleReliabilityFailureKind.GattTimeout.ToString(),
      103,
      null);
  }

  private async Task InsertIncidentAsync(
    Guid id,
    Guid enrollmentId,
    string role,
    string failureKind,
    long startedAt,
    long? recoveredAt)
  {
    long? recoveredGeneration = recoveredAt is null ? null : 2L;
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.ExecuteSqlInterpolatedAsync($"""
      INSERT INTO BleReliabilityIncidents
        (Id, DeviceEnrollmentId, Role, DeviceDisplayName, StartedAtUnixMilliseconds,
         RecoveredAtUnixMilliseconds, FirstConnectionGeneration, RecoveredConnectionGeneration,
         FailedAttemptCount, FailureKind, LastSanitizedFault, MaximumReconnectDelaySeconds)
      VALUES
        ({id}, {enrollmentId}, {role}, {"Test device"}, {startedAt},
         {recoveredAt}, {1L}, {recoveredGeneration},
         {1}, {failureKind}, {"Sanitized fault"}, {1.0});
      """);
  }

  public Task DisposeAsync()
  {
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }
}
