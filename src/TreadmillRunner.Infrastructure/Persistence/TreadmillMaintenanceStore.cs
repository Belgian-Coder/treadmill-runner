using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Infrastructure.Persistence;

public interface ITreadmillMaintenanceStore
{
  Task<TreadmillMaintenanceSnapshot?> GetAsync(DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<TreadmillMaintenanceSnapshot> UpdatePolicyAsync(
    int intervalMonths,
    double distanceIntervalKilometers,
    int expectedVersion,
    PersistenceWriteOperation operation,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
  Task<TreadmillMaintenanceSnapshot> RecordAsync(
    DateTimeOffset performedAtUtc,
    string? note,
    int expectedPolicyVersion,
    PersistenceWriteOperation operation,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
}

public sealed class TreadmillMaintenanceStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : ITreadmillMaintenanceStore
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<TreadmillMaintenanceSnapshot?> GetAsync(
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await ReadAsync(context, nowUtc, cancellationToken);
  }

  public async Task<TreadmillMaintenanceSnapshot> UpdatePolicyAsync(
    int intervalMonths,
    double distanceIntervalKilometers,
    int expectedVersion,
    PersistenceWriteOperation operation,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ValidatePolicy(intervalMonths, distanceIntervalKilometers);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    TreadmillMaintenancePolicyEntity policy = await RequiredActivePolicyAsync(context, cancellationToken);
    EnsureVersion(policy.Version, expectedVersion);
    policy.IntervalMonths = intervalMonths;
    policy.DistanceIntervalKilometers = distanceIntervalKilometers;
    policy.Version++;
    policy.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    TreadmillMaintenanceSnapshot snapshot = await ReadAsync(context, nowUtc, cancellationToken)
      ?? throw new InvalidOperationException("The active treadmill maintenance policy disappeared.");
    PersistenceReceipts.Add(context, operation with { OutcomeJson = JsonSerializer.Serialize(snapshot, JsonOptions) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return snapshot;
  }

  public async Task<TreadmillMaintenanceSnapshot> RecordAsync(
    DateTimeOffset performedAtUtc,
    string? note,
    int expectedPolicyVersion,
    PersistenceWriteOperation operation,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    if (performedAtUtc.Offset != TimeSpan.Zero)
      throw new ArgumentException("The maintenance timestamp must be UTC.", nameof(performedAtUtc));
    if (performedAtUtc > nowUtc.AddMinutes(5) || performedAtUtc < nowUtc.AddYears(-10))
      throw new ArgumentOutOfRangeException(nameof(performedAtUtc), "Maintenance must be recorded within the last ten years and not in the future.");
    note = string.IsNullOrWhiteSpace(note) ? null : note.Trim();
    if (note?.Length > 500)
      throw new ArgumentOutOfRangeException(nameof(note), "Maintenance notes are limited to 500 characters.");

    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(
      System.Data.IsolationLevel.Serializable,
      cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    TreadmillMaintenancePolicyEntity policy = await RequiredActivePolicyAsync(context, cancellationToken);
    EnsureVersion(policy.Version, expectedPolicyVersion);
    double distance = await AppTrackedDistanceAsync(context, cancellationToken);
    context.TreadmillMaintenanceEvents.Add(new TreadmillMaintenanceEventEntity
    {
      Id = Guid.NewGuid(),
      TreadmillMaintenancePolicyId = policy.Id,
      OperationId = operation.ClientOperationId,
      PerformedAtUtc = performedAtUtc,
      AppDistanceBaselineKilometers = distance,
      Note = note,
      CreatedAtUtc = nowUtc,
    });
    policy.Version++;
    policy.UpdatedAtUtc = nowUtc;
    await context.SaveChangesAsync(cancellationToken);
    TreadmillMaintenanceSnapshot snapshot = await ReadAsync(context, nowUtc, cancellationToken)
      ?? throw new InvalidOperationException("The active treadmill maintenance policy disappeared.");
    PersistenceReceipts.Add(context, operation with { OutcomeJson = JsonSerializer.Serialize(snapshot, JsonOptions) });
    await context.SaveChangesAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return snapshot;
  }

  private static async Task<TreadmillMaintenanceSnapshot?> ReadAsync(
    TreadmillRunnerDbContext context,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken)
  {
    TreadmillMaintenancePolicyEntity? policy = await context.TreadmillMaintenancePolicies.AsNoTracking()
      .Include(item => item.DeviceEnrollment)
      .Include(item => item.Events)
      .SingleOrDefaultAsync(item => item.DeviceEnrollment.Role == "Treadmill" && !item.DeviceEnrollment.IsArchived, cancellationToken);
    if (policy is null) return null;
    double distance = await AppTrackedDistanceAsync(context, cancellationToken);
    TreadmillMaintenanceEvent[] events = policy.Events
      .OrderByDescending(item => item.PerformedAtUtc)
      .ThenByDescending(item => item.CreatedAtUtc)
      .Select(Map)
      .ToArray();
    TreadmillMaintenanceEvent? last = events.FirstOrDefault();
    DateTimeOffset? nextDate = last?.PerformedAtUtc.AddMonths(policy.IntervalMonths);
    double? nextDistance = last?.AppDistanceBaselineKilometers + policy.DistanceIntervalKilometers;
    bool dueDate = nextDate is not null && nowUtc >= nextDate;
    bool dueDistance = nextDistance is not null && distance >= nextDistance;
    TreadmillMaintenanceState state = last is null
      ? TreadmillMaintenanceState.SetupRequired
      : dueDate && dueDistance
        ? TreadmillMaintenanceState.DueByDateAndDistance
        : dueDate
          ? TreadmillMaintenanceState.DueByDate
          : dueDistance
            ? TreadmillMaintenanceState.DueByDistance
            : TreadmillMaintenanceState.Current;
    var mappedPolicy = new TreadmillMaintenancePolicy(
      policy.Id,
      policy.DeviceEnrollmentId,
      policy.DeviceEnrollment.DisplayName,
      policy.IntervalMonths,
      policy.DistanceIntervalKilometers,
      policy.Version,
      policy.UpdatedAtUtc);
    return new TreadmillMaintenanceSnapshot(
      mappedPolicy,
      state,
      dueDate || dueDistance,
      distance,
      nextDate,
      nextDistance,
      nextDistance is null ? null : Math.Max(0, nextDistance.Value - distance),
      last,
      Array.AsReadOnly(events),
      "Only hardware sessions recorded by TreadmillRunner count toward this distance. Console-only use is not visible to the app.");
  }

  private static async Task<double> AppTrackedDistanceAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken) =>
    await context.WorkoutSessions.AsNoTracking()
      .Where(item => item.SessionOrigin == nameof(SessionOrigin.Hardware) &&
        (item.State == nameof(SessionState.Completed) || item.State == nameof(SessionState.Stopped) ||
         item.State == nameof(SessionState.Interrupted) || item.State == nameof(SessionState.Faulted)))
      .SumAsync(item => (double?)item.DistanceKilometers, cancellationToken) ?? 0;

  private static async Task<TreadmillMaintenancePolicyEntity> RequiredActivePolicyAsync(
    TreadmillRunnerDbContext context,
    CancellationToken cancellationToken) =>
    await context.TreadmillMaintenancePolicies.Include(item => item.DeviceEnrollment)
      .SingleOrDefaultAsync(item => item.DeviceEnrollment.Role == "Treadmill" && !item.DeviceEnrollment.IsArchived, cancellationToken)
      ?? throw new KeyNotFoundException("No active treadmill maintenance policy is available.");

  private static TreadmillMaintenanceEvent Map(TreadmillMaintenanceEventEntity entity) => new(
    entity.Id,
    entity.TreadmillMaintenancePolicyId,
    entity.OperationId,
    entity.PerformedAtUtc,
    entity.AppDistanceBaselineKilometers,
    entity.Note,
    entity.CreatedAtUtc);

  private static void ValidatePolicy(int months, double distance)
  {
    if (months is < 1 or > 24)
      throw new ArgumentOutOfRangeException(nameof(months), "Maintenance interval must be between 1 and 24 months.");
    if (!double.IsFinite(distance) || distance is < 1 or > 5000)
      throw new ArgumentOutOfRangeException(nameof(distance), "Maintenance distance must be between 1 and 5000 km.");
  }

  private static void EnsureVersion(int actual, int expected)
  {
    if (actual != expected)
      throw new DbUpdateConcurrencyException("The maintenance policy changed; refresh and try again.");
  }
}
