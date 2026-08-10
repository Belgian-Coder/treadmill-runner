namespace TreadmillRunner.Gateway.Planning;

public sealed record HeartRateZoneDto(int Number, string Name, ushort MinimumBpm, ushort MaximumBpm);

public sealed record ProfileDto(
  Guid Id,
  string DisplayName,
  string UnitSystem,
  double WeightKilograms,
  ushort? MaximumHeartRateBpm,
  double? MaximumSpeedKph,
  double HeartRateIncreaseStepKph,
  int HeartRateIncreaseCooldownSeconds,
  double HeartRateDecreaseStepKph,
  int HeartRateDecreaseCooldownSeconds,
  int Version,
  IReadOnlyList<HeartRateZoneDto> HeartRateZones);

public sealed record ProfileUpsertRequest(
  Guid OperationId,
  string DisplayName,
  string UnitSystem,
  double WeightKilograms,
  ushort? MaximumHeartRateBpm,
  double? MaximumSpeedKph,
  IReadOnlyList<HeartRateZoneDto> HeartRateZones,
  int? ExpectedVersion,
  double? HeartRateIncreaseStepKph = null,
  int? HeartRateIncreaseCooldownSeconds = null,
  double? HeartRateDecreaseStepKph = null,
  int? HeartRateDecreaseCooldownSeconds = null);

public sealed record ArchiveProfileRequest(Guid OperationId, int ExpectedVersion);

public sealed record WorkoutBlockRequest(
  string Kind,
  int Repetitions,
  IReadOnlyList<WorkoutBlockRequest> Blocks,
  string GoalKind,
  double GoalValue,
  string SpeedKind,
  double SpeedStartKph,
  double SpeedEndKph,
  ushort HeartRateMinimumBpm,
  ushort HeartRateMaximumBpm,
  int HeartRateZoneNumber,
  double HeartRateInitialSpeedKph,
  double HeartRateMinimumSpeedKph,
  double HeartRateMaximumSpeedKph,
  string InclineKind,
  double InclineStartPercent,
  double InclineEndPercent,
  string? Cue,
  string? Notes);

public sealed record WorkoutSaveRequest(
  Guid OperationId,
  string Name,
  string? Description,
  IReadOnlyList<WorkoutBlockRequest> Blocks,
  string Kind = "Structured");

public sealed record WorkoutSaveResponse(Guid WorkoutId, Guid RevisionId, int RevisionNumber, string ContentSha256);

public sealed record WorkoutRevisionDto(
  Guid WorkoutId,
  Guid RevisionId,
  int RevisionNumber,
  string ContentSha256,
  string Name,
  string? Description,
  IReadOnlyList<WorkoutBlockRequest> Blocks);

public sealed record WorkoutSummaryDto(
  Guid Id,
  string Name,
  string? Description,
  string Kind,
  bool IsArchived,
  Guid CurrentRevisionId,
  int CurrentRevisionNumber,
  int ExpandedStepCount,
  double? DurationMinutes,
  DateTimeOffset UpdatedAtUtc,
  string StructureLabel = "Structured",
  string GoalLabel = "Mixed goals",
  string SpeedLabel = "Varied speed",
  string InclineLabel = "Varied incline",
  bool UsesHeartRate = false);

public sealed record WorkoutReuseDto(
  Guid WorkoutId,
  Guid WorkoutRevisionId,
  string Name,
  string? Description,
  int ExpandedStepCount,
  double? PlannedDurationMinutes,
  DateTimeOffset LastCompletedAtUtc,
  TimeSpan LastActualDuration,
  int CompletionCount);

public sealed record WorkoutProgramItemRequest(Guid WorkoutRevisionId);

public sealed record WorkoutProgramSaveRequest(
  Guid OperationId,
  string Name,
  string? Description,
  string Category,
  IReadOnlyList<WorkoutProgramItemRequest> Items,
  Guid? OwnerProfileId = null);

public sealed record WorkoutProgramItemDto(
  Guid Id,
  Guid WorkoutRevisionId,
  int Position,
  string WorkoutName,
  int WorkoutRevisionNumber,
  double? DurationMinutes,
  int? WeekNumber = null,
  int? SessionNumber = null,
  string? Phase = null,
  IReadOnlyList<WorkoutProgramAlternativeDto>? Alternatives = null);

public sealed record WorkoutProgramAlternativeDto(
  Guid WorkoutRevisionId,
  int DisplayOrder,
  string Variant,
  string WorkoutName,
  int WorkoutRevisionNumber,
  double? DurationMinutes);

public sealed record WorkoutProgramRunDto(
  Guid Id,
  Guid ProfileId,
  string Status,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset? EndedAtUtc,
  int Version,
  DateOnly? ScheduledStartDate = null,
  int ScheduledWeekdayMask = 0,
  string? ScheduleTimeZoneId = null);

public sealed record WorkoutProgramSummaryDto(
  Guid Id,
  bool IsArchived,
  Guid RevisionId,
  int RevisionNumber,
  string Name,
  string? Description,
  string Category,
  int ItemCount,
  WorkoutProgramRunDto? Run,
  int CompletedItemCount,
  Guid? NextItemId,
  Guid? NextWorkoutRevisionId,
  string? NextWorkoutName,
  int? NextWorkoutRevisionNumber,
  double? NextWorkoutDurationMinutes,
  bool IsComplete,
  int RequiredTrainingDays,
  string? TemplateId = null,
  string? TemplateVersion = null,
  Guid? OwnerProfileId = null,
  int SkippedItemCount = 0);

public sealed record WorkoutProgramDto(
  Guid Id,
  bool IsArchived,
  Guid RevisionId,
  int RevisionNumber,
  string Name,
  string? Description,
  string Category,
  IReadOnlyList<WorkoutProgramItemDto> Items,
  WorkoutProgramRunDto? Run,
  int CompletedItemCount,
  Guid? NextItemId,
  Guid? NextWorkoutRevisionId,
  bool IsComplete,
  string? TemplateId = null,
  string? TemplateVersion = null,
  Guid? OwnerProfileId = null,
  int SkippedItemCount = 0);

public sealed record PremadePlanCatalogDto(
  string Id,
  string Version,
  string Name,
  string Description,
  string Goal,
  string Experience,
  int Weeks,
  int SessionsPerWeek,
  int SessionCount,
  int VariantCount,
  int MaximumDurationMinutes,
  double MaximumSpeedKph,
  double MaximumInclinePercent,
  bool Repeatable,
  bool RequiresHeartRate,
  IReadOnlyList<string> Tags,
  bool AlreadyAdded,
  int CopyCount);

public sealed record PremadePlanPhaseDto(string Name, int FirstWeek, int LastWeek, int SessionCount);

public sealed record PremadePlanWorkoutDto(
  string Key,
  string Name,
  string? Description,
  int ExpandedStepCount,
  double? DurationMinutes,
  IReadOnlyList<WorkoutBlockRequest> Blocks);

public sealed record PremadePlanSessionAlternativeDto(string WorkoutKey, string Variant, string WorkoutName);

public sealed record PremadePlanSessionDto(
  int Position,
  int WeekNumber,
  int SessionNumber,
  string Phase,
  string WorkoutKey,
  string WorkoutName,
  IReadOnlyList<PremadePlanSessionAlternativeDto> Alternatives);

public sealed record PremadePlanPreviewDto(
  PremadePlanCatalogDto Template,
  Guid ProfileId,
  string ProfileName,
  bool Compatible,
  string CompatibilityMessage,
  bool HeartRateZonesReady,
  double NormalizedMaximumSpeedKph,
  double NormalizedMaximumInclinePercent,
  int NormalizedTargetCount,
  int RejectedTargetCount,
  int UniqueWorkoutCount,
  IReadOnlyList<PremadePlanPhaseDto> Phases,
  IReadOnlyList<PremadePlanWorkoutDto> Workouts,
  IReadOnlyList<PremadePlanSessionDto> Sessions);

public sealed record PremadePlanMaterializeRequest(
  Guid OperationId,
  Guid ProfileId,
  string TemplateId,
  string TemplateVersion);

public sealed record PremadePlanMaterializeDto(
  Guid InstallationId,
  Guid ProgramId,
  Guid ProgramRevisionId,
  string TemplateId,
  string TemplateVersion,
  int CopyNumber,
  int PositionCount,
  int UniqueWorkoutCount,
  bool AlreadyAdded,
  bool Replayed);

public sealed record WorkoutProgramStartRequest(
  Guid OperationId,
  Guid ProfileId,
  Guid ExpectedProgramRevisionId,
  Guid? ExpectedActiveRunId,
  int? ExpectedActiveRunVersion,
  DateOnly? ScheduledStartDate = null,
  int ScheduledWeekdayMask = 0,
  string? ScheduleTimeZoneId = null);
public sealed record ArchiveWorkoutProgramRequest(Guid OperationId);
public sealed record WorkoutProgramClearUpcomingRequest(
  Guid OperationId,
  Guid ProfileId,
  int ExpectedRunVersion,
  DateOnly Today);

public sealed record ArchiveWorkoutRequest(Guid OperationId);

public sealed record ImportWarningDto(string Code, string Message);

public sealed record ImportPreviewDto(
  Guid PreviewId,
  string SourceSha256,
  string FileName,
  string Format,
  string Title,
  int ExpandedStepCount,
  double? DurationMinutes,
  DateTimeOffset ExpiresAtUtc,
  IReadOnlyList<ImportWarningDto> Warnings);

public sealed record ImportConfirmRequest(
  Guid OperationId,
  Guid PreviewId,
  string SourceSha256,
  Guid? ProfileId,
  string? QDomyosUnits);

public sealed record ImportConfirmResponse(
  Guid WorkoutId,
  Guid RevisionId,
  int RevisionNumber,
  bool Replayed,
  IReadOnlyList<ImportWarningDto> Warnings);

public sealed record CalendarAlternativeRequest(Guid WorkoutRevisionId, int DisplayOrder);

public sealed record CalendarExceptionRequest(
  DateOnly Date,
  string Kind,
  IReadOnlyList<CalendarAlternativeRequest> Alternatives);

public sealed record CalendarSeriesSaveRequest(
  Guid OperationId,
  Guid ProfileId,
  string Name,
  string TimeZoneId,
  DateOnly StartDate,
  DateOnly? EndDate,
  int IntervalWeeks,
  int WeekdayMask,
  IReadOnlyList<CalendarAlternativeRequest> Alternatives,
  IReadOnlyList<CalendarExceptionRequest> Exceptions,
  int? ExpectedVersion);

public sealed record CalendarSeriesDto(
  Guid Id,
  Guid ScheduleGroupId,
  Guid ProfileId,
  string Name,
  string TimeZoneId,
  DateOnly StartDate,
  DateOnly? EndDate,
  int IntervalWeeks,
  int WeekdayMask,
  int Version,
  IReadOnlyList<CalendarAlternativeRequest> Alternatives,
  IReadOnlyList<CalendarExceptionRequest> Exceptions);

public sealed record CalendarSelectionRequest(Guid OperationId, Guid SeriesId, Guid WorkoutRevisionId);

public sealed record CalendarOptionDto(
  Guid SeriesId,
  Guid ScheduleGroupId,
  string ScheduleName,
  Guid WorkoutRevisionId,
  string WorkoutName,
  int RevisionNumber,
  int DisplayOrder,
  bool IsSelected,
  string Source = "Calendar",
  Guid? ProgramRunId = null,
  Guid? ProgramItemId = null,
  int? ProgramPosition = null,
  int? ProgramTotal = null,
  int? WeekNumber = null,
  string? Phase = null,
  int? ProgramRunVersion = null,
  bool IsRepeat = false,
  Guid? ExtraOccurrenceId = null,
  DateOnly? OriginalDate = null,
  bool IsCompleted = false,
  int? ProgramWeekdayMask = null);

public sealed record CalendarDayDto(DateOnly Date, IReadOnlyList<CalendarOptionDto> Options);

public sealed record CalendarOccurrenceMoveRequest(
  Guid OperationId,
  DateOnly TargetDate,
  bool MoveFollowing,
  int ExpectedVersion,
  IReadOnlyList<CalendarSegmentVersion>? ExpectedSegments = null);

public sealed record CalendarOccurrenceDeleteRequest(Guid OperationId, int ExpectedVersion);

public sealed record CalendarSegmentVersion(Guid SeriesId, int Version);

public sealed record CalendarGroupDeleteRequest(Guid OperationId, IReadOnlyList<CalendarSegmentVersion> ExpectedSegments);

public sealed record WorkoutProgramScheduleChangeRequest(
  Guid? OperationId,
  Guid ProfileId,
  Guid ProgramItemId,
  string Action,
  DateOnly? TargetDate,
  int? ExpectedRunVersion = null);

public sealed record WorkoutProgramScheduleImpactDto(
  Guid ProgramItemId,
  int Position,
  DateOnly? CurrentDate,
  DateOnly? NewDate,
  bool IsRepeat);

public sealed record WorkoutProgramScheduleChangePreviewDto(
  Guid RunId,
  Guid ProgramItemId,
  string Action,
  int RunVersion,
  bool CanApply,
  string Message,
  IReadOnlyList<WorkoutProgramScheduleImpactDto> Impacts,
  IReadOnlyList<DateOnly> CollisionDates,
  bool Replayed = false);

public sealed record WorkoutProgramDefaultDaysRequest(
  Guid? OperationId,
  Guid ProfileId,
  int WeekdayMask,
  DateOnly EffectiveDate,
  int? ExpectedRunVersion = null,
  string? ExpectedRevision = null);

public sealed record WorkoutProgramDefaultDaysImpactDto(
  Guid ProgramItemId,
  int Position,
  DateOnly CurrentDate,
  DateOnly NewDate);

public sealed record WorkoutProgramDefaultDaysPreviewDto(
  Guid RunId,
  int RunVersion,
  int CurrentWeekdayMask,
  int NewWeekdayMask,
  DateOnly EffectiveDate,
  bool CanApply,
  string Message,
  string Revision,
  IReadOnlyList<WorkoutProgramDefaultDaysImpactDto> Impacts,
  IReadOnlyList<DateOnly> CollisionDates,
  int PreservedExceptionCount,
  bool Replayed = false);

public sealed record CalendarRangeDto(Guid ProfileId, DateOnly From, DateOnly To, IReadOnlyList<CalendarDayDto> Days);
