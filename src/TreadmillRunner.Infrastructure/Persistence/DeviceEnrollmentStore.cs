using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record VersionedDeviceEnrollment(
  DeviceEnrollment Enrollment,
  int Version,
  bool IsArchived,
  DateTimeOffset? ArchivedAtUtc);

public sealed record HeartRateAssignmentPreference(
  Guid UserProfileId,
  int Priority,
  bool AutoConnect,
  bool IsPreferred);

public interface IDeviceEnrollmentStore
{
  Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(CancellationToken cancellationToken = default);
  Task<IReadOnlyList<HeartRateDeviceAssignment>> ListHeartRateAssignmentsAsync(CancellationToken cancellationToken = default) =>
    Task.FromResult<IReadOnlyList<HeartRateDeviceAssignment>>([]);
  Task<VersionedDeviceEnrollment?> FindActiveAsync(DeviceRole role, CancellationToken cancellationToken = default);
  Task<VersionedDeviceEnrollment> EnrollAsync(DeviceEnrollment enrollment, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<VersionedDeviceEnrollment> EnrollWithAssignmentsAsync(DeviceEnrollment enrollment, IReadOnlyList<HeartRateAssignmentPreference> assignments, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
    EnrollAsync(enrollment, nowUtc, operation, cancellationToken);
  Task<bool> ForgetAsync(DeviceRole role, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> ForgetByIdAsync(Guid id, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
    throw new NotSupportedException("ID-based device removal is not supported by this store.");
  Task<IReadOnlyList<HeartRateDeviceAssignment>> ConfigureHeartRateAssignmentsAsync(Guid enrollmentId, IReadOnlyList<HeartRateAssignmentPreference> assignments, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
    throw new NotSupportedException("Heart-rate assignments are not supported by this store.");
  Task<VersionedDeviceEnrollment> RenameAsync(Guid enrollmentId, string displayName, int expectedVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default) =>
    throw new NotSupportedException("Device renaming is not supported by this store.");
  Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(Guid id, int expectedVersion, string? modelNumber, string? firmwareRevision, TreadmillCapabilities? capabilities, TreadmillCapabilityEvidence evidence, DateTimeOffset verifiedAtUtc, CancellationToken cancellationToken = default);
}

public sealed class DeviceEnrollmentStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IDeviceEnrollmentStore
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<IReadOnlyList<VersionedDeviceEnrollment>> ListActiveAsync(
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    List<DeviceEnrollmentEntity> entities = await context.DeviceEnrollments.AsNoTracking()
      .Where(entity => !entity.IsArchived)
      .OrderBy(entity => entity.Role)
      .ToListAsync(cancellationToken);
    return entities.Select(Map).ToArray();
  }

  public async Task<IReadOnlyList<HeartRateDeviceAssignment>> ListHeartRateAssignmentsAsync(
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    List<HeartRateDeviceAssignmentEntity> entities = await context.HeartRateDeviceAssignments.AsNoTracking()
      .Where(entity => !entity.DeviceEnrollment.IsArchived && !entity.UserProfile.IsArchived)
      .OrderBy(entity => entity.UserProfileId)
      .ThenBy(entity => entity.Priority)
      .ThenBy(entity => entity.DeviceEnrollmentId)
      .ToListAsync(cancellationToken);
    return entities.Select(MapAssignment).ToArray();
  }

  public async Task<VersionedDeviceEnrollment?> FindActiveAsync(
    DeviceRole role,
    CancellationToken cancellationToken = default)
  {
    string roleName = role.ToString();
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    DeviceEnrollmentEntity? entity = role == DeviceRole.Treadmill
      ? await context.DeviceEnrollments.AsNoTracking().SingleOrDefaultAsync(
        candidate => candidate.Role == roleName && !candidate.IsArchived,
        cancellationToken)
      : await context.DeviceEnrollments.AsNoTracking()
        .Where(candidate => candidate.Role == roleName && !candidate.IsArchived)
        .OrderBy(candidate => candidate.CreatedAtUtc)
        .FirstOrDefaultAsync(cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<VersionedDeviceEnrollment> EnrollAsync(
    DeviceEnrollment enrollment,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    return await EnrollWithAssignmentsAsync(enrollment, [], nowUtc, operation, cancellationToken);
  }

  public async Task<VersionedDeviceEnrollment> EnrollWithAssignmentsAsync(
    DeviceEnrollment enrollment,
    IReadOnlyList<HeartRateAssignmentPreference> assignments,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(enrollment);
    ArgumentNullException.ThrowIfNull(assignments);
    if (enrollment.Role == DeviceRole.Treadmill && assignments.Count > 0)
    {
      throw new ArgumentException("Treadmill enrollments cannot have heart-rate assignments.", nameof(assignments));
    }
    ValidateAssignments(assignments);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    DeviceEnrollmentEntity entity = CreateEntity(enrollment, nowUtc);
    context.DeviceEnrollments.Add(entity);
    if (enrollment.Role == DeviceRole.Treadmill)
    {
      context.TreadmillMaintenancePolicies.Add(new TreadmillMaintenancePolicyEntity
      {
        Id = Guid.NewGuid(),
        DeviceEnrollmentId = enrollment.Id,
        IntervalMonths = 3,
        DistanceIntervalKilometers = 241,
        Version = 1,
        CreatedAtUtc = nowUtc,
        UpdatedAtUtc = nowUtc,
      });
    }
    context.HeartRateDeviceAssignments.AddRange(assignments.Select(preference => CreateAssignment(
      enrollment.Id,
      preference,
      nowUtc)));
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return Map(entity);
  }

  public async Task<bool> ForgetAsync(
    DeviceRole role,
    int expectedVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    string roleName = role.ToString();
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    DeviceEnrollmentEntity? entity = role == DeviceRole.Treadmill
      ? await context.DeviceEnrollments.SingleOrDefaultAsync(
        candidate => candidate.Role == roleName && !candidate.IsArchived,
        cancellationToken)
      : await context.DeviceEnrollments
        .Where(candidate => candidate.Role == roleName && !candidate.IsArchived)
        .OrderBy(candidate => candidate.CreatedAtUtc)
        .FirstOrDefaultAsync(cancellationToken);
    if (entity is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }

    if (entity.Version != expectedVersion)
    {
      throw new DbUpdateConcurrencyException(
        $"Expected enrollment version {expectedVersion}, but stored version is {entity.Version}.");
    }

    entity.IsArchived = true;
    entity.ArchivedAtUtc = nowUtc;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  public async Task<bool> ForgetByIdAsync(
    Guid id,
    int expectedVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    if (id == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(id));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    DeviceEnrollmentEntity? entity = await context.DeviceEnrollments.SingleOrDefaultAsync(
      candidate => candidate.Id == id && !candidate.IsArchived,
      cancellationToken);
    if (entity is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }
    if (entity.Version != expectedVersion)
    {
      throw new DbUpdateConcurrencyException(
        $"Expected enrollment version {expectedVersion}, but stored version is {entity.Version}.");
    }
    entity.IsArchived = true;
    entity.ArchivedAtUtc = nowUtc;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  public async Task<IReadOnlyList<HeartRateDeviceAssignment>> ConfigureHeartRateAssignmentsAsync(
    Guid enrollmentId,
    IReadOnlyList<HeartRateAssignmentPreference> assignments,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    ArgumentNullException.ThrowIfNull(assignments);
    ValidateAssignments(assignments);
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    DeviceEnrollmentEntity enrollment = await context.DeviceEnrollments.SingleOrDefaultAsync(
      candidate => candidate.Id == enrollmentId && !candidate.IsArchived,
      cancellationToken) ?? throw new KeyNotFoundException("The heart-rate enrollment was not found.");
    if (enrollment.Role != DeviceRole.HeartRate.ToString())
    {
      throw new ArgumentException("Assignments are valid only for heart-rate devices.", nameof(enrollmentId));
    }
    List<HeartRateDeviceAssignmentEntity> existing = await context.HeartRateDeviceAssignments
      .Where(candidate => candidate.DeviceEnrollmentId == enrollmentId)
      .ToListAsync(cancellationToken);
    Guid[] preferredProfiles = assignments
      .Where(item => item.IsPreferred)
      .Select(item => item.UserProfileId)
      .ToArray();
    if (preferredProfiles.Length > 0)
    {
      List<HeartRateDeviceAssignmentEntity> previousPreferred = await context.HeartRateDeviceAssignments
        .Where(candidate => candidate.DeviceEnrollmentId != enrollmentId &&
          preferredProfiles.Contains(candidate.UserProfileId) && candidate.IsPreferred)
        .ToListAsync(cancellationToken);
      foreach (HeartRateDeviceAssignmentEntity assignment in previousPreferred)
      {
        assignment.IsPreferred = false;
        assignment.UpdatedAtUtc = nowUtc;
        assignment.Version++;
      }
    }
    context.HeartRateDeviceAssignments.RemoveRange(existing);
    HeartRateDeviceAssignmentEntity[] replacements = assignments
      .Select(preference => CreateAssignment(enrollmentId, preference, nowUtc))
      .ToArray();
    context.HeartRateDeviceAssignments.AddRange(replacements);
    enrollment.UpdatedAtUtc = nowUtc;
    enrollment.Version++;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return replacements.Select(MapAssignment).ToArray();
  }

  public async Task<VersionedDeviceEnrollment> RenameAsync(
    Guid enrollmentId,
    string displayName,
    int expectedVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    ArgumentException.ThrowIfNullOrWhiteSpace(displayName);
    string normalized = displayName.Trim();
    if (normalized.Length > 100) throw new ArgumentException("Device name cannot exceed 100 characters.", nameof(displayName));
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    DeviceEnrollmentEntity entity = await context.DeviceEnrollments.SingleOrDefaultAsync(
      candidate => candidate.Id == enrollmentId && !candidate.IsArchived,
      cancellationToken) ?? throw new KeyNotFoundException("The device enrollment was not found.");
    if (entity.Version != expectedVersion)
      throw new DbUpdateConcurrencyException($"Expected enrollment version {expectedVersion}, but stored version is {entity.Version}.");
    entity.DisplayName = normalized;
    entity.UpdatedAtUtc = nowUtc;
    entity.Version++;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return Map(entity);
  }

  public async Task<VersionedDeviceEnrollment> UpdateEvidenceAsync(
    Guid id,
    int expectedVersion,
    string? modelNumber,
    string? firmwareRevision,
    TreadmillCapabilities? capabilities,
    TreadmillCapabilityEvidence evidence,
    DateTimeOffset verifiedAtUtc,
    CancellationToken cancellationToken = default)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    DeviceEnrollmentEntity entity = await context.DeviceEnrollments.SingleOrDefaultAsync(
      candidate => candidate.Id == id && !candidate.IsArchived,
      cancellationToken) ?? throw new KeyNotFoundException($"Enrollment {id} was not found.");
    if (entity.Version != expectedVersion)
    {
      throw new DbUpdateConcurrencyException(
        $"Expected enrollment version {expectedVersion}, but stored version is {entity.Version}.");
    }

    string? capabilitiesJson = capabilities is null
      ? null
      : JsonSerializer.Serialize(capabilities, JsonOptions);
    string? normalizedModel = string.IsNullOrWhiteSpace(modelNumber) ? entity.ModelNumber : modelNumber.Trim();
    string? normalizedFirmware = string.IsNullOrWhiteSpace(firmwareRevision) ? entity.FirmwareRevision : firmwareRevision.Trim();
    if (entity.ModelNumber == normalizedModel &&
        entity.FirmwareRevision == normalizedFirmware &&
        entity.CapabilitiesJson == capabilitiesJson &&
        entity.Evidence == evidence.ToString() &&
        entity.LastVerifiedAtUtc is not null)
    {
      return Map(entity);
    }

    entity.ModelNumber = normalizedModel;
    entity.FirmwareRevision = normalizedFirmware;
    entity.CapabilitiesJson = capabilitiesJson;
    entity.Evidence = evidence.ToString();
    entity.LastVerifiedAtUtc = verifiedAtUtc;
    entity.UpdatedAtUtc = verifiedAtUtc;
    entity.Version++;
    await context.SaveChangesAsync(cancellationToken);
    return Map(entity);
  }

  private static VersionedDeviceEnrollment Map(DeviceEnrollmentEntity entity)
  {
    var role = Enum.Parse<DeviceRole>(entity.Role);
    TreadmillCapabilities? capabilities = entity.CapabilitiesJson is null
      ? null
      : JsonSerializer.Deserialize<TreadmillCapabilities>(entity.CapabilitiesJson, JsonOptions)
        ?? throw new InvalidDataException("Stored treadmill capabilities are invalid.");
    return new VersionedDeviceEnrollment(
      new DeviceEnrollment(
        entity.Id,
        role,
        entity.DeviceId,
        entity.ProtocolId,
        entity.IdentityFingerprint,
        entity.DisplayName,
        entity.ModelNumber,
        entity.FirmwareRevision,
        entity.TelemetryMode is null ? null : Enum.Parse<TreadmillTelemetryMode>(entity.TelemetryMode),
        capabilities,
        Enum.Parse<TreadmillCapabilityEvidence>(entity.Evidence),
        entity.LastVerifiedAtUtc,
        entity.HeartRateDeviceKind is null ? null : Enum.Parse<HeartRateDeviceKind>(entity.HeartRateDeviceKind),
        entity.HeartRateDeviceFamily is null ? null : Enum.Parse<HeartRateDeviceFamily>(entity.HeartRateDeviceFamily)),
      entity.Version,
      entity.IsArchived,
      entity.ArchivedAtUtc);
  }

  private static DeviceEnrollmentEntity CreateEntity(DeviceEnrollment enrollment, DateTimeOffset nowUtc) => new()
  {
    Id = enrollment.Id,
    Role = enrollment.Role.ToString(),
    DeviceId = enrollment.DeviceId,
    ProtocolId = enrollment.ProtocolId,
    IdentityFingerprint = enrollment.IdentityFingerprint,
    DisplayName = enrollment.DisplayName,
    ModelNumber = enrollment.ModelNumber,
    FirmwareRevision = enrollment.FirmwareRevision,
    TelemetryMode = enrollment.TelemetryMode?.ToString(),
    CapabilitiesJson = enrollment.Capabilities is null
      ? null
      : JsonSerializer.Serialize(enrollment.Capabilities, JsonOptions),
    Evidence = enrollment.Evidence.ToString(),
    LastVerifiedAtUtc = enrollment.LastVerifiedAtUtc,
    HeartRateDeviceKind = enrollment.HeartRateDeviceKind?.ToString(),
    HeartRateDeviceFamily = enrollment.HeartRateDeviceFamily?.ToString(),
    Version = 1,
    CreatedAtUtc = nowUtc,
    UpdatedAtUtc = nowUtc,
  };

  private static HeartRateDeviceAssignmentEntity CreateAssignment(
    Guid enrollmentId,
    HeartRateAssignmentPreference preference,
    DateTimeOffset nowUtc) => new()
    {
      Id = Guid.NewGuid(),
      UserProfileId = preference.UserProfileId,
      DeviceEnrollmentId = enrollmentId,
      Priority = preference.Priority,
      AutoConnect = preference.AutoConnect,
      IsPreferred = preference.IsPreferred,
      Version = 1,
      CreatedAtUtc = nowUtc,
      UpdatedAtUtc = nowUtc,
    };

  private static HeartRateDeviceAssignment MapAssignment(HeartRateDeviceAssignmentEntity entity) =>
    new HeartRateDeviceAssignment(
      entity.Id,
      entity.UserProfileId,
      entity.DeviceEnrollmentId,
      entity.Priority,
      entity.AutoConnect,
      entity.IsPreferred,
      entity.Version).Validate();

  private static void ValidateAssignments(IReadOnlyList<HeartRateAssignmentPreference> assignments)
  {
    if (assignments.Select(item => item.UserProfileId).Distinct().Count() != assignments.Count)
    {
      throw new ArgumentException("A runner can be assigned to a sensor only once.", nameof(assignments));
    }
    if (assignments.Any(item => item.UserProfileId == Guid.Empty || item.Priority is < 0 or > 99))
    {
      throw new ArgumentException("Every assignment requires a runner and priority from 0 to 99.", nameof(assignments));
    }
  }
}
