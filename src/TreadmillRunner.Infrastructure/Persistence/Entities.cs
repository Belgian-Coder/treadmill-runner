namespace TreadmillRunner.Infrastructure.Persistence;

internal sealed class DeviceEnrollmentEntity
{
  public Guid Id { get; set; }
  public string Role { get; set; } = string.Empty;
  public string DeviceId { get; set; } = string.Empty;
  public string ProtocolId { get; set; } = string.Empty;
  public string IdentityFingerprint { get; set; } = string.Empty;
  public string DisplayName { get; set; } = string.Empty;
  public string? ModelNumber { get; set; }
  public string? FirmwareRevision { get; set; }
  public string? TelemetryMode { get; set; }
  public string? CapabilitiesJson { get; set; }
  public string Evidence { get; set; } = string.Empty;
  public DateTimeOffset? LastVerifiedAtUtc { get; set; }
  public string? HeartRateDeviceKind { get; set; }
  public string? HeartRateDeviceFamily { get; set; }
  public int Version { get; set; }
  public bool IsArchived { get; set; }
  public DateTimeOffset? ArchivedAtUtc { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
}

internal sealed class TreadmillMaintenancePolicyEntity
{
  public Guid Id { get; set; }
  public Guid DeviceEnrollmentId { get; set; }
  public int IntervalMonths { get; set; } = 3;
  public double DistanceIntervalKilometers { get; set; } = 241;
  public int Version { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public DeviceEnrollmentEntity DeviceEnrollment { get; set; } = null!;
  public List<TreadmillMaintenanceEventEntity> Events { get; set; } = [];
}

internal sealed class TreadmillMaintenanceEventEntity
{
  public Guid Id { get; set; }
  public Guid TreadmillMaintenancePolicyId { get; set; }
  public Guid OperationId { get; set; }
  public DateTimeOffset PerformedAtUtc { get; set; }
  public double AppDistanceBaselineKilometers { get; set; }
  public string? Note { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public TreadmillMaintenancePolicyEntity Policy { get; set; } = null!;
}

internal sealed class HeartRateDeviceAssignmentEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public Guid DeviceEnrollmentId { get; set; }
  public int Priority { get; set; }
  public bool AutoConnect { get; set; }
  public bool IsPreferred { get; set; }
  public int Version { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
  public DeviceEnrollmentEntity DeviceEnrollment { get; set; } = null!;
}

internal sealed class BleReliabilityIncidentEntity
{
  public Guid Id { get; set; }
  public Guid DeviceEnrollmentId { get; set; }
  public string Role { get; set; } = string.Empty;
  public string DeviceDisplayName { get; set; } = string.Empty;
  public long StartedAtUnixMilliseconds { get; set; }
  public long? RecoveredAtUnixMilliseconds { get; set; }
  public long FirstConnectionGeneration { get; set; }
  public long? RecoveredConnectionGeneration { get; set; }
  public int FailedAttemptCount { get; set; }
  public string FailureKind { get; set; } = string.Empty;
  public string LastSanitizedFault { get; set; } = string.Empty;
  public double MaximumReconnectDelaySeconds { get; set; }
}

internal sealed class UserProfileEntity
{
  public Guid Id { get; set; }
  public string DisplayName { get; set; } = string.Empty;
  public string NormalizedDisplayName { get; set; } = string.Empty;
  public string UnitSystem { get; set; } = string.Empty;
  public double WeightKilograms { get; set; }
  public ushort? MaximumHeartRateBpm { get; set; }
  public double? MaximumSpeedKph { get; set; }
  public double HeartRateIncreaseStepKph { get; set; } = 0.2;
  public int HeartRateIncreaseCooldownSeconds { get; set; } = 30;
  public double HeartRateDecreaseStepKph { get; set; } = 0.5;
  public int HeartRateDecreaseCooldownSeconds { get; set; } = 15;
  public int Version { get; set; }
  public bool IsArchived { get; set; }
  public DateTimeOffset? ArchivedAtUtc { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public List<HeartRateZoneEntity> HeartRateZones { get; set; } = [];
}

internal sealed class HeartRateZoneEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public int Number { get; set; }
  public string Name { get; set; } = string.Empty;
  public ushort MinimumBpm { get; set; }
  public ushort MaximumBpm { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
}

internal sealed class WorkoutEntity
{
  public Guid Id { get; set; }
  public string Name { get; set; } = string.Empty;
  public string Kind { get; set; } = "Structured";
  public DateTimeOffset CreatedAtUtc { get; set; }
  public bool IsArchived { get; set; }
  public List<WorkoutRevisionEntity> Revisions { get; set; } = [];
}

internal sealed class WorkoutRevisionEntity
{
  public Guid Id { get; set; }
  public Guid WorkoutId { get; set; }
  public int RevisionNumber { get; set; }
  public string DefinitionJson { get; set; } = string.Empty;
  public string ContentSha256 { get; set; } = string.Empty;
  public DateTimeOffset CreatedAtUtc { get; set; }
  public WorkoutEntity Workout { get; set; } = null!;
}

internal sealed class ImportAuditEntity
{
  public Guid Id { get; set; }
  public Guid? UserProfileId { get; set; }
  public Guid WorkoutId { get; set; }
  public Guid WorkoutRevisionId { get; set; }
  public string OriginalFileName { get; set; } = string.Empty;
  public string Format { get; set; } = string.Empty;
  public string SourceSha256 { get; set; } = string.Empty;
  public string WarningSummaryJson { get; set; } = "[]";
  public DateTimeOffset ImportedAtUtc { get; set; }
}

internal sealed class CalendarSeriesEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public Guid ScheduleGroupId { get; set; }
  public string Name { get; set; } = string.Empty;
  public string TimeZoneId { get; set; } = "Europe/Brussels";
  public DateOnly StartDate { get; set; }
  public DateOnly? EndDate { get; set; }
  public int IntervalWeeks { get; set; }
  public int WeekdayMask { get; set; }
  public int Version { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public List<CalendarSeriesOptionEntity> Options { get; set; } = [];
  public List<CalendarExceptionEntity> Exceptions { get; set; } = [];
}

internal sealed class CalendarSeriesOptionEntity
{
  public Guid Id { get; set; }
  public Guid CalendarSeriesId { get; set; }
  public Guid WorkoutRevisionId { get; set; }
  public int DisplayOrder { get; set; }
  public CalendarSeriesEntity CalendarSeries { get; set; } = null!;
}

internal sealed class CalendarExceptionEntity
{
  public Guid Id { get; set; }
  public Guid CalendarSeriesId { get; set; }
  public DateOnly LocalDate { get; set; }
  public string Kind { get; set; } = string.Empty;
  public string? Note { get; set; }
  public CalendarSeriesEntity CalendarSeries { get; set; } = null!;
  public List<CalendarExceptionOptionEntity> Options { get; set; } = [];
}

internal sealed class CalendarExceptionOptionEntity
{
  public Guid Id { get; set; }
  public Guid CalendarExceptionId { get; set; }
  public Guid WorkoutRevisionId { get; set; }
  public int DisplayOrder { get; set; }
  public CalendarExceptionEntity CalendarException { get; set; } = null!;
}

internal sealed class TrainingDaySelectionEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public DateOnly LocalDate { get; set; }
  public Guid CalendarSeriesId { get; set; }
  public Guid WorkoutRevisionId { get; set; }
  public DateTimeOffset SelectedAtUtc { get; set; }
}

internal sealed class WorkoutProgramEntity
{
  public Guid Id { get; set; }
  public bool IsArchived { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public List<WorkoutProgramRevisionEntity> Revisions { get; set; } = [];
}

internal sealed class WorkoutProgramRevisionEntity
{
  public Guid Id { get; set; }
  public Guid WorkoutProgramId { get; set; }
  public int RevisionNumber { get; set; }
  public string Name { get; set; } = string.Empty;
  public string? Description { get; set; }
  public string Category { get; set; } = string.Empty;
  public string ContentSha256 { get; set; } = string.Empty;
  public string? TemplateId { get; set; }
  public string? TemplateVersion { get; set; }
  public Guid? OwnerProfileId { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public WorkoutProgramEntity WorkoutProgram { get; set; } = null!;
  public List<WorkoutProgramItemEntity> Items { get; set; } = [];
}

internal sealed class WorkoutProgramItemEntity
{
  public Guid Id { get; set; }
  public Guid WorkoutProgramRevisionId { get; set; }
  public Guid WorkoutRevisionId { get; set; }
  public int Position { get; set; }
  public int? WeekNumber { get; set; }
  public int? SessionNumber { get; set; }
  public string? Phase { get; set; }
  public WorkoutProgramRevisionEntity WorkoutProgramRevision { get; set; } = null!;
}

internal sealed class PremadePlanInstallationEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public string TemplateId { get; set; } = string.Empty;
  public string TemplateVersion { get; set; } = string.Empty;
  public string TemplateContentSha256 { get; set; } = string.Empty;
  public int CopyNumber { get; set; }
  public Guid WorkoutProgramId { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
}

internal sealed class WorkoutProgramRunEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public Guid WorkoutProgramRevisionId { get; set; }
  public string Status { get; set; } = string.Empty;
  public DateTimeOffset StartedAtUtc { get; set; }
  public DateTimeOffset? EndedAtUtc { get; set; }
  public int Version { get; set; }
}

internal sealed class WorkoutSessionEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public string UserProfileName { get; set; } = string.Empty;
  public Guid WorkoutRevisionId { get; set; }
  public Guid? WorkoutProgramRunId { get; set; }
  public Guid? WorkoutProgramItemId { get; set; }
  public string SelectionSource { get; set; } = "Legacy";
  public string SessionOrigin { get; set; } = "Legacy";
  public string WorkoutTitle { get; set; } = string.Empty;
  public string State { get; set; } = string.Empty;
  public DateTimeOffset ArmedAtUtc { get; set; }
  public DateTimeOffset? StartedAtUtc { get; set; }
  public DateTimeOffset? EndedAtUtc { get; set; }
  public double DurationSeconds { get; set; }
  public double DistanceKilometers { get; set; }
  public double EstimatedCalories { get; set; }
  public double? AverageHeartRateBpm { get; set; }
  public ushort? MaximumHeartRateBpm { get; set; }
  public double AverageSpeedKph { get; set; }
  public double AverageInclinePercent { get; set; }
  public string MetricAlgorithmVersion { get; set; } = string.Empty;
  public string ControllerConfigurationJson { get; set; } = "{}";
  public string? RecoveryCheckpointJson { get; set; }
  public DateTimeOffset? RecoveryCheckpointUpdatedAtUtc { get; set; }
  public int? PerceivedExertion { get; set; }
  public string? DebriefNote { get; set; }
  public DateTimeOffset? DebriefUpdatedAtUtc { get; set; }
  public List<SessionSampleEntity> Samples { get; set; } = [];
  public List<SessionEventEntity> Events { get; set; } = [];
}

internal sealed class SessionSampleEntity
{
  public Guid WorkoutSessionId { get; set; }
  public long Sequence { get; set; }
  public DateTimeOffset CapturedAtUtc { get; set; }
  public double ElapsedMilliseconds { get; set; }
  public double? PlannedSpeedKph { get; set; }
  public double RequestedSpeedKph { get; set; }
  public double MeasuredSpeedKph { get; set; }
  public double? PlannedInclinePercent { get; set; }
  public double RequestedInclinePercent { get; set; }
  public double MeasuredInclinePercent { get; set; }
  public ushort? HeartRateBpm { get; set; }
  public double DistanceKilometers { get; set; }
  public double EstimatedCalories { get; set; }
  public double TelemetryAgeMilliseconds { get; set; }
  public string MetricAlgorithmVersion { get; set; } = string.Empty;
  public WorkoutSessionEntity WorkoutSession { get; set; } = null!;
}

internal sealed class SessionEventEntity
{
  public Guid Id { get; set; }
  public Guid WorkoutSessionId { get; set; }
  public DateTimeOffset OccurredAtUtc { get; set; }
  public string Kind { get; set; } = string.Empty;
  public string DetailsJson { get; set; } = "{}";
  public WorkoutSessionEntity WorkoutSession { get; set; } = null!;
}

internal sealed class GarminAccountLinkEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public string ProviderSubject { get; set; } = string.Empty;
  public string AccountLabel { get; set; } = string.Empty;
  public string ProtectedAccessToken { get; set; } = string.Empty;
  public string? ProtectedRefreshToken { get; set; }
  public DateTimeOffset? AccessTokenExpiresAtUtc { get; set; }
  public string Scopes { get; set; } = string.Empty;
  public DateTimeOffset ConnectedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public DateTimeOffset? LastSyncAttemptAtUtc { get; set; }
  public DateTimeOffset? LastSyncSuccessAtUtc { get; set; }
  public string? LastSyncError { get; set; }
  public int Version { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
  public List<GarminSyncItemEntity> SyncItems { get; set; } = [];
}

internal sealed class GarminOAuthStateEntity
{
  public string StateHash { get; set; } = string.Empty;
  public Guid UserProfileId { get; set; }
  public string ProtectedCodeVerifier { get; set; } = string.Empty;
  public string RedirectUri { get; set; } = string.Empty;
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset ExpiresAtUtc { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
}

internal sealed class GarminSyncItemEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public Guid GarminAccountLinkId { get; set; }
  public string Kind { get; set; } = string.Empty;
  public Guid SourceId { get; set; }
  public string SourceVersion { get; set; } = string.Empty;
  public string IdempotencyKey { get; set; } = string.Empty;
  public string PayloadJson { get; set; } = string.Empty;
  public string Status { get; set; } = string.Empty;
  public int AttemptCount { get; set; }
  public DateTimeOffset AvailableAtUtc { get; set; }
  public DateTimeOffset? LeaseExpiresAtUtc { get; set; }
  public string? RemoteId { get; set; }
  public string? LastError { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public GarminAccountLinkEntity AccountLink { get; set; } = null!;
}

internal sealed class GarminWatchBindingEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public string DeviceLabel { get; set; } = string.Empty;
  public string TokenSha256 { get; set; } = string.Empty;
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset? LastSeenAtUtc { get; set; }
  public int Version { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
}

internal sealed class GarminActivityUploadAccountEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public string AccountLabel { get; set; } = string.Empty;
  public string ProtectedTokenStore { get; set; } = string.Empty;
  public bool Enabled { get; set; }
  public string State { get; set; } = "Connected";
  public DateTimeOffset ConnectedAtUtc { get; set; }
  public DateTimeOffset? UploadFromUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public DateTimeOffset? LastUploadSuccessAtUtc { get; set; }
  public string? LastError { get; set; }
  public int Version { get; set; }
  public UserProfileEntity UserProfile { get; set; } = null!;
  public List<GarminActivityUploadJobEntity> Jobs { get; set; } = [];
}

internal sealed class GarminActivityUploadJobEntity
{
  public Guid Id { get; set; }
  public Guid UserProfileId { get; set; }
  public Guid GarminActivityUploadAccountId { get; set; }
  public Guid WorkoutSessionId { get; set; }
  public string IdempotencyKey { get; set; } = string.Empty;
  public string Status { get; set; } = "Pending";
  public int AttemptCount { get; set; }
  public DateTimeOffset AvailableAtUtc { get; set; }
  public DateTimeOffset? LeaseExpiresAtUtc { get; set; }
  public string? RemoteId { get; set; }
  public string? FailureKind { get; set; }
  public string? LastError { get; set; }
  public DateTimeOffset CreatedAtUtc { get; set; }
  public DateTimeOffset UpdatedAtUtc { get; set; }
  public DateTimeOffset? AcknowledgedAtUtc { get; set; }
  public GarminActivityUploadAccountEntity Account { get; set; } = null!;
}

internal sealed class OperationReceiptEntity
{
  public Guid Id { get; set; }
  public Guid ClientOperationId { get; set; }
  public string OperationType { get; set; } = string.Empty;
  public int StatusCode { get; set; }
  public string OutcomeJson { get; set; } = string.Empty;
  public DateTimeOffset CreatedAtUtc { get; set; }
  public string RequestFingerprint { get; set; } = string.Empty;
}
