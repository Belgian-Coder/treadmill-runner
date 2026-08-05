using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Infrastructure.Persistence;

public interface IBleReliabilityStore
{
  Task BeginOrContinueIncidentAsync(
    Guid enrollmentId,
    DeviceRole role,
    string displayName,
    long connectionGeneration,
    BleReliabilityFailureKind failureKind,
    string sanitizedFault,
    TimeSpan reconnectDelay,
    DateTimeOffset occurredAtUtc,
    CancellationToken cancellationToken = default);

  Task ResolveIncidentAsync(
    Guid enrollmentId,
    long connectionGeneration,
    int additionalFailedAttempts,
    TimeSpan maximumReconnectDelay,
    DateTimeOffset recoveredAtUtc,
    CancellationToken cancellationToken = default);

  Task<IReadOnlyList<BleReliabilityIncident>> ListSinceAsync(
    DateTimeOffset sinceUtc,
    int maximumCount,
    CancellationToken cancellationToken = default);

  Task PruneRecoveredBeforeAsync(
    DateTimeOffset beforeUtc,
    CancellationToken cancellationToken = default);
}

public sealed class BleReliabilityStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IBleReliabilityStore
{
  public async Task BeginOrContinueIncidentAsync(
    Guid enrollmentId,
    DeviceRole role,
    string displayName,
    long connectionGeneration,
    BleReliabilityFailureKind failureKind,
    string sanitizedFault,
    TimeSpan reconnectDelay,
    DateTimeOffset occurredAtUtc,
    CancellationToken cancellationToken = default)
  {
    Validate(enrollmentId, displayName, sanitizedFault, reconnectDelay);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    BleReliabilityIncidentEntity? incident = await context.BleReliabilityIncidents
      .SingleOrDefaultAsync(
        candidate => candidate.DeviceEnrollmentId == enrollmentId && candidate.RecoveredAtUnixMilliseconds == null,
        cancellationToken);
    if (incident is null)
    {
      incident = new BleReliabilityIncidentEntity
      {
        Id = Guid.NewGuid(),
        DeviceEnrollmentId = enrollmentId,
        Role = role.ToString(),
        DeviceDisplayName = Normalize(displayName, 100),
        StartedAtUnixMilliseconds = occurredAtUtc.ToUnixTimeMilliseconds(),
        FirstConnectionGeneration = connectionGeneration,
        FailedAttemptCount = 1,
        FailureKind = failureKind.ToString(),
        LastSanitizedFault = Normalize(sanitizedFault, 256),
        MaximumReconnectDelaySeconds = reconnectDelay.TotalSeconds,
      };
      context.BleReliabilityIncidents.Add(incident);
    }
    else
    {
      incident.FailedAttemptCount++;
      incident.FailureKind = failureKind.ToString();
      incident.LastSanitizedFault = Normalize(sanitizedFault, 256);
      incident.MaximumReconnectDelaySeconds = Math.Max(
        incident.MaximumReconnectDelaySeconds,
        reconnectDelay.TotalSeconds);
    }

    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task ResolveIncidentAsync(
    Guid enrollmentId,
    long connectionGeneration,
    int additionalFailedAttempts,
    TimeSpan maximumReconnectDelay,
    DateTimeOffset recoveredAtUtc,
    CancellationToken cancellationToken = default)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    if (additionalFailedAttempts < 0) throw new ArgumentOutOfRangeException(nameof(additionalFailedAttempts));
    if (maximumReconnectDelay < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(maximumReconnectDelay));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    BleReliabilityIncidentEntity? incident = await context.BleReliabilityIncidents
      .SingleOrDefaultAsync(
        candidate => candidate.DeviceEnrollmentId == enrollmentId && candidate.RecoveredAtUnixMilliseconds == null,
        cancellationToken);
    if (incident is null) return;

    incident.RecoveredAtUnixMilliseconds = recoveredAtUtc.ToUnixTimeMilliseconds();
    incident.RecoveredConnectionGeneration = connectionGeneration;
    incident.FailedAttemptCount += additionalFailedAttempts;
    incident.MaximumReconnectDelaySeconds = Math.Max(
      incident.MaximumReconnectDelaySeconds,
      maximumReconnectDelay.TotalSeconds);
    await context.SaveChangesAsync(cancellationToken);
  }

  public async Task<IReadOnlyList<BleReliabilityIncident>> ListSinceAsync(
    DateTimeOffset sinceUtc,
    int maximumCount,
    CancellationToken cancellationToken = default)
  {
    if (maximumCount is < 1 or > 2000) throw new ArgumentOutOfRangeException(nameof(maximumCount));
    long since = sinceUtc.ToUnixTimeMilliseconds();
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    List<BleReliabilityIncidentEntity> incidents = await context.BleReliabilityIncidents.AsNoTracking()
      .Where(entity => entity.StartedAtUnixMilliseconds >= since || entity.RecoveredAtUnixMilliseconds == null)
      .OrderByDescending(entity => entity.StartedAtUnixMilliseconds)
      .Take(maximumCount)
      .ToListAsync(cancellationToken);
    return incidents.Select(Map).ToArray();
  }

  public async Task PruneRecoveredBeforeAsync(
    DateTimeOffset beforeUtc,
    CancellationToken cancellationToken = default)
  {
    long before = beforeUtc.ToUnixTimeMilliseconds();
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await context.BleReliabilityIncidents
      .Where(entity => entity.RecoveredAtUnixMilliseconds != null && entity.RecoveredAtUnixMilliseconds < before)
      .ExecuteDeleteAsync(cancellationToken);
  }

  private static BleReliabilityIncident Map(BleReliabilityIncidentEntity entity) => new(
    entity.Id,
    entity.DeviceEnrollmentId,
    Enum.Parse<DeviceRole>(entity.Role),
    entity.DeviceDisplayName,
    DateTimeOffset.FromUnixTimeMilliseconds(entity.StartedAtUnixMilliseconds),
    entity.RecoveredAtUnixMilliseconds is { } recovered
      ? DateTimeOffset.FromUnixTimeMilliseconds(recovered)
      : null,
    entity.FirstConnectionGeneration,
    entity.RecoveredConnectionGeneration,
    entity.FailedAttemptCount,
    Enum.Parse<BleReliabilityFailureKind>(entity.FailureKind),
    entity.LastSanitizedFault,
    entity.MaximumReconnectDelaySeconds);

  private static void Validate(
    Guid enrollmentId,
    string displayName,
    string sanitizedFault,
    TimeSpan reconnectDelay)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    ArgumentException.ThrowIfNullOrWhiteSpace(displayName);
    ArgumentException.ThrowIfNullOrWhiteSpace(sanitizedFault);
    if (reconnectDelay < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(reconnectDelay));
  }

  private static string Normalize(string value, int maximumLength)
  {
    string normalized = value.Trim();
    return normalized[..Math.Min(normalized.Length, maximumLength)];
  }
}
