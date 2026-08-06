namespace TreadmillRunner.Web.Planning;

public sealed record HeartRateZoneView(int Number, string Name, ushort MinimumBpm, ushort MaximumBpm);

public sealed record ProfileView(
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
  IReadOnlyList<HeartRateZoneView> HeartRateZones);

public sealed record ProfileUpsertRequest(
  Guid OperationId,
  string DisplayName,
  string UnitSystem,
  double WeightKilograms,
  ushort? MaximumHeartRateBpm,
  double? MaximumSpeedKph,
  IReadOnlyList<HeartRateZoneView> HeartRateZones,
  int? ExpectedVersion = null,
  double? HeartRateIncreaseStepKph = null,
  int? HeartRateIncreaseCooldownSeconds = null,
  double? HeartRateDecreaseStepKph = null,
  int? HeartRateDecreaseCooldownSeconds = null);

public sealed record GarminConnectionStatusView(
  Guid ProfileId,
  bool ProviderConfigured,
  string SetupMessage,
  bool Connected,
  string? AccountLabel,
  DateTimeOffset? ConnectedAtUtc,
  DateTimeOffset? LastSyncAttemptAtUtc,
  DateTimeOffset? LastSyncSuccessAtUtc,
  string? LastError,
  int PendingItems,
  int FailedItems,
  int SyncedItems);

public sealed record GarminConnectStartView(string AuthorizationUrl, DateTimeOffset ExpiresAtUtc);

public sealed record WorkoutSummaryView(
  Guid Id,
  string Name,
  string? Description,
  string Kind,
  bool IsArchived,
  Guid CurrentRevisionId,
  int CurrentRevisionNumber,
  int ExpandedStepCount,
  double? DurationMinutes,
  DateTimeOffset UpdatedAtUtc);

public sealed record WorkoutReuseView(
  Guid WorkoutId,
  Guid WorkoutRevisionId,
  string Name,
  string? Description,
  int ExpandedStepCount,
  double? PlannedDurationMinutes,
  DateTimeOffset LastCompletedAtUtc,
  TimeSpan LastActualDuration,
  int CompletionCount);

public sealed record WorkoutBlockInput(
  string Kind,
  int Repetitions,
  IReadOnlyList<WorkoutBlockInput> Blocks,
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
  IReadOnlyList<WorkoutBlockInput> Blocks,
  string Kind = "Structured");

public sealed record WorkoutSaveResponse(
  Guid WorkoutId,
  Guid RevisionId,
  int RevisionNumber,
  string ContentSha256);

public sealed record WorkoutProgramItemInput(Guid WorkoutRevisionId);

public sealed record WorkoutProgramSaveRequest(
  Guid OperationId,
  string Name,
  string? Description,
  string Category,
  IReadOnlyList<WorkoutProgramItemInput> Items);

public sealed record WorkoutProgramItemView(
  Guid Id,
  Guid WorkoutRevisionId,
  int Position,
  string WorkoutName,
  int WorkoutRevisionNumber,
  double? DurationMinutes,
  int? WeekNumber = null,
  int? SessionNumber = null,
  string? Phase = null);

public sealed record WorkoutProgramRunView(
  Guid Id,
  Guid ProfileId,
  string Status,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset? EndedAtUtc,
  int Version);

public sealed record WorkoutProgramView(
  Guid Id,
  bool IsArchived,
  Guid RevisionId,
  int RevisionNumber,
  string Name,
  string? Description,
  string Category,
  IReadOnlyList<WorkoutProgramItemView> Items,
  WorkoutProgramRunView? Run,
  int CompletedItemCount,
  Guid? NextItemId,
  Guid? NextWorkoutRevisionId,
  bool IsComplete,
  string? TemplateId = null,
  string? TemplateVersion = null,
  Guid? OwnerProfileId = null);

public sealed record PremadePlanCatalogView(
  string Id,
  string Version,
  string Name,
  string Description,
  string Goal,
  string Experience,
  int Weeks,
  int SessionsPerWeek,
  int SessionCount,
  int MaximumDurationMinutes,
  double MaximumSpeedKph,
  double MaximumInclinePercent,
  bool Repeatable,
  bool RequiresHeartRate,
  IReadOnlyList<string> Tags,
  bool AlreadyAdded,
  int CopyCount);

public sealed record PremadePlanPhaseView(string Name, int FirstWeek, int LastWeek, int SessionCount);

public sealed record PremadePlanPreviewView(
  PremadePlanCatalogView Template,
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
  IReadOnlyList<PremadePlanPhaseView> Phases);

public sealed record PremadePlanMaterializeRequest(
  Guid OperationId,
  Guid ProfileId,
  string TemplateId,
  string TemplateVersion,
  bool FreshCopy);

public sealed record PremadePlanMaterializeView(
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
  int? ExpectedActiveRunVersion);

public sealed record WorkoutRevisionView(
  Guid WorkoutId,
  Guid RevisionId,
  int RevisionNumber,
  string ContentSha256,
  string Name,
  string? Description,
  IReadOnlyList<WorkoutBlockInput> Blocks);

public sealed record ImportPreviewView(
  Guid PreviewId,
  string SourceSha256,
  string FileName,
  string Format,
  string Title,
  int ExpandedStepCount,
  double? DurationMinutes,
  DateTimeOffset ExpiresAtUtc,
  IReadOnlyList<ImportWarningView> Warnings);

public sealed record ImportWarningView(string Code, string Message);

public sealed record ImportConfirmRequest(
  Guid OperationId,
  Guid PreviewId,
  string SourceSha256,
  Guid? ProfileId,
  string? QDomyosUnits);

public sealed record WorkoutSetStrategyView(string Name, int Substitutions);
public sealed record WorkoutSetVariantView(
  string SessionId,
  string Variant,
  string Title,
  string ControlMode,
  string SelectionRule);
public sealed record WorkoutSetSlotView(
  string CanonicalSlot,
  int Week,
  int Session,
  IReadOnlyList<WorkoutSetVariantView> Variants);
public sealed record WorkoutSetImportPreviewView(
  Guid PreviewId,
  string SourceSha256,
  string FileName,
  string PlanName,
  string Category,
  string ToolVersion,
  int SlotCount,
  int VariantCount,
  DateTimeOffset ExpiresAtUtc,
  IReadOnlyList<string> Warnings,
  IReadOnlyList<WorkoutSetStrategyView> Strategies,
  IReadOnlyList<WorkoutSetSlotView> Slots);
public sealed record WorkoutSetImportConfirmRequest(
  Guid OperationId,
  Guid PreviewId,
  string SourceSha256,
  Guid? ProfileId,
  string SelectionStrategy);

public sealed record CalendarOptionView(
  Guid SeriesId,
  Guid ScheduleGroupId,
  string ScheduleName,
  Guid WorkoutRevisionId,
  string WorkoutName,
  int RevisionNumber,
  int DisplayOrder,
  bool IsSelected);

public sealed record CalendarDayView(DateOnly Date, IReadOnlyList<CalendarOptionView> Options);

public sealed record CalendarRangeView(
  Guid ProfileId,
  DateOnly From,
  DateOnly To,
  IReadOnlyList<CalendarDayView> Days);

public sealed record CalendarAlternativeInput(Guid WorkoutRevisionId, int DisplayOrder);

public sealed record CalendarExceptionInput(
  DateOnly Date,
  string Kind,
  IReadOnlyList<CalendarAlternativeInput> Alternatives);

public sealed record CalendarSeriesSaveRequest(
  Guid OperationId,
  Guid ProfileId,
  string Name,
  string TimeZoneId,
  DateOnly StartDate,
  DateOnly? EndDate,
  int IntervalWeeks,
  int WeekdayMask,
  IReadOnlyList<CalendarAlternativeInput> Alternatives,
  IReadOnlyList<CalendarExceptionInput> Exceptions,
  int? ExpectedVersion = null);

public sealed record CalendarSelectionRequest(Guid OperationId, Guid SeriesId, Guid WorkoutRevisionId);

public sealed record CalendarSeriesView(
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
  IReadOnlyList<CalendarAlternativeInput> Alternatives,
  IReadOnlyList<CalendarExceptionInput> Exceptions);

public sealed record CalendarOccurrenceMoveRequest(
  Guid OperationId,
  DateOnly TargetDate,
  bool MoveFollowing,
  int ExpectedVersion,
  IReadOnlyList<CalendarSegmentVersion>? ExpectedSegments = null);

public sealed record CalendarOccurrenceDeleteRequest(Guid OperationId, int ExpectedVersion);

public sealed record CalendarSegmentVersion(Guid SeriesId, int Version);

public sealed record CalendarGroupDeleteRequest(Guid OperationId, IReadOnlyList<CalendarSegmentVersion> ExpectedSegments);
