using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed class TreadmillRunnerDbContext(
  DbContextOptions<TreadmillRunnerDbContext> options) : DbContext(options)
{
  internal DbSet<DeviceEnrollmentEntity> DeviceEnrollments => Set<DeviceEnrollmentEntity>();
  internal DbSet<HeartRateDeviceAssignmentEntity> HeartRateDeviceAssignments => Set<HeartRateDeviceAssignmentEntity>();
  internal DbSet<BleReliabilityIncidentEntity> BleReliabilityIncidents => Set<BleReliabilityIncidentEntity>();
  internal DbSet<TreadmillMaintenancePolicyEntity> TreadmillMaintenancePolicies => Set<TreadmillMaintenancePolicyEntity>();
  internal DbSet<TreadmillMaintenanceEventEntity> TreadmillMaintenanceEvents => Set<TreadmillMaintenanceEventEntity>();
  internal DbSet<UserProfileEntity> UserProfiles => Set<UserProfileEntity>();
  internal DbSet<HeartRateZoneEntity> HeartRateZones => Set<HeartRateZoneEntity>();
  internal DbSet<WorkoutEntity> Workouts => Set<WorkoutEntity>();
  internal DbSet<WorkoutRevisionEntity> WorkoutRevisions => Set<WorkoutRevisionEntity>();
  internal DbSet<ImportAuditEntity> ImportAudits => Set<ImportAuditEntity>();
  internal DbSet<CalendarSeriesEntity> CalendarSeries => Set<CalendarSeriesEntity>();
  internal DbSet<CalendarSeriesOptionEntity> CalendarSeriesOptions => Set<CalendarSeriesOptionEntity>();
  internal DbSet<CalendarExceptionEntity> CalendarExceptions => Set<CalendarExceptionEntity>();
  internal DbSet<CalendarExceptionOptionEntity> CalendarExceptionOptions => Set<CalendarExceptionOptionEntity>();
  internal DbSet<TrainingDaySelectionEntity> TrainingDaySelections => Set<TrainingDaySelectionEntity>();
  internal DbSet<WorkoutProgramEntity> WorkoutPrograms => Set<WorkoutProgramEntity>();
  internal DbSet<WorkoutProgramRevisionEntity> WorkoutProgramRevisions => Set<WorkoutProgramRevisionEntity>();
  internal DbSet<WorkoutProgramItemEntity> WorkoutProgramItems => Set<WorkoutProgramItemEntity>();
  internal DbSet<WorkoutProgramItemAlternativeEntity> WorkoutProgramItemAlternatives => Set<WorkoutProgramItemAlternativeEntity>();
  internal DbSet<WorkoutProgramRunEntity> WorkoutProgramRuns => Set<WorkoutProgramRunEntity>();
  internal DbSet<WorkoutProgramScheduleOverrideEntity> WorkoutProgramScheduleOverrides => Set<WorkoutProgramScheduleOverrideEntity>();
  internal DbSet<WorkoutProgramExtraOccurrenceEntity> WorkoutProgramExtraOccurrences => Set<WorkoutProgramExtraOccurrenceEntity>();
  internal DbSet<PremadePlanInstallationEntity> PremadePlanInstallations => Set<PremadePlanInstallationEntity>();
  internal DbSet<WorkoutSessionEntity> WorkoutSessions => Set<WorkoutSessionEntity>();
  internal DbSet<SessionSampleEntity> SessionSamples => Set<SessionSampleEntity>();
  internal DbSet<SessionEventEntity> SessionEvents => Set<SessionEventEntity>();
  internal DbSet<GarminAccountLinkEntity> GarminAccountLinks => Set<GarminAccountLinkEntity>();
  internal DbSet<GarminOAuthStateEntity> GarminOAuthStates => Set<GarminOAuthStateEntity>();
  internal DbSet<GarminSyncItemEntity> GarminSyncItems => Set<GarminSyncItemEntity>();
  internal DbSet<GarminWatchBindingEntity> GarminWatchBindings => Set<GarminWatchBindingEntity>();
  internal DbSet<GarminActivityUploadAccountEntity> GarminActivityUploadAccounts => Set<GarminActivityUploadAccountEntity>();
  internal DbSet<GarminActivityUploadJobEntity> GarminActivityUploadJobs => Set<GarminActivityUploadJobEntity>();
  internal DbSet<OperationReceiptEntity> OperationReceipts => Set<OperationReceiptEntity>();
  internal DbSet<RunnerExperiencePreferenceEntity> RunnerExperiencePreferences => Set<RunnerExperiencePreferenceEntity>();
  internal DbSet<LocalGoalEntity> LocalGoals => Set<LocalGoalEntity>();
  internal DbSet<ProgressionRecommendationEntity> ProgressionRecommendations => Set<ProgressionRecommendationEntity>();
  internal DbSet<LocalBackupPolicyEntity> LocalBackupPolicies => Set<LocalBackupPolicyEntity>();
  internal DbSet<BackupVerificationEntity> BackupVerifications => Set<BackupVerificationEntity>();

  public override int SaveChanges(bool acceptAllChangesOnSuccess)
  {
    RejectWorkoutRevisionMutation();
    return base.SaveChanges(acceptAllChangesOnSuccess);
  }

  public override Task<int> SaveChangesAsync(
    bool acceptAllChangesOnSuccess,
    CancellationToken cancellationToken = default)
  {
    RejectWorkoutRevisionMutation();
    return base.SaveChangesAsync(acceptAllChangesOnSuccess, cancellationToken);
  }

  protected override void OnModelCreating(ModelBuilder modelBuilder)
  {
    ConfigureDeviceEnrollments(modelBuilder);
    ConfigureHeartRateDeviceAssignments(modelBuilder);
    ConfigureBleReliability(modelBuilder);
    ConfigureTreadmillMaintenance(modelBuilder);
    ConfigureProfiles(modelBuilder);
    ConfigureWorkouts(modelBuilder);
    ConfigureImports(modelBuilder);
    ConfigureCalendar(modelBuilder);
    ConfigureWorkoutPrograms(modelBuilder);
    ConfigurePremadePlanInstallations(modelBuilder);
    ConfigureSessions(modelBuilder);
    ConfigureGarmin(modelBuilder);
    ConfigureOperationReceipts(modelBuilder);
    ConfigureLocalFirstExperience(modelBuilder);
  }

  private static void ConfigureLocalFirstExperience(ModelBuilder modelBuilder)
  {
    var preference = modelBuilder.Entity<RunnerExperiencePreferenceEntity>();
    preference.ToTable("RunnerExperiencePreferences", table =>
    {
      table.HasCheckConstraint("CK_RunnerExperiencePreferences_Style", "\"DisplayStyle\" IN ('Balanced', 'LargeText', 'HighContrast')");
      table.HasCheckConstraint("CK_RunnerExperiencePreferences_Volume", "\"CueVolumePercent\" >= 0 AND \"CueVolumePercent\" <= 100");
      table.HasCheckConstraint("CK_RunnerExperiencePreferences_Version", "\"Version\" > 0");
    });
    preference.HasKey(entity => entity.Id);
    preference.Property(entity => entity.DisplayStyle).HasMaxLength(20);
    preference.Property(entity => entity.PrimaryMetricsJson).HasMaxLength(256);
    preference.Property(entity => entity.Version).IsConcurrencyToken();
    preference.HasIndex(entity => entity.UserProfileId).IsUnique();
    preference.HasOne<UserProfileEntity>().WithOne()
      .HasForeignKey<RunnerExperiencePreferenceEntity>(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);

    var goal = modelBuilder.Entity<LocalGoalEntity>();
    goal.ToTable("LocalGoals", table =>
    {
      table.HasCheckConstraint("CK_LocalGoals_Kind", "\"Kind\" IN ('Sessions', 'Minutes', 'Distance', 'PlanCompletion')");
      table.HasCheckConstraint("CK_LocalGoals_Period", "\"Period\" IN ('Weekly', 'Monthly', 'Plan')");
      table.HasCheckConstraint("CK_LocalGoals_Target", "\"TargetValue\" > 0");
      table.HasCheckConstraint("CK_LocalGoals_Version", "\"Version\" > 0");
    });
    goal.HasKey(entity => entity.Id);
    goal.Property(entity => entity.Kind).HasMaxLength(30);
    goal.Property(entity => entity.Period).HasMaxLength(20);
    goal.Property(entity => entity.Version).IsConcurrencyToken();
    goal.HasIndex(entity => new { entity.UserProfileId, entity.Kind, entity.Period }).IsUnique();
    goal.HasOne<UserProfileEntity>().WithMany()
      .HasForeignKey(entity => entity.UserProfileId).OnDelete(DeleteBehavior.Cascade);

    var recommendation = modelBuilder.Entity<ProgressionRecommendationEntity>();
    recommendation.ToTable("ProgressionRecommendations", table =>
    {
      table.HasCheckConstraint("CK_ProgressionRecommendations_Action", "\"Action\" IN ('Maintain', 'Repeat', 'Reduce', 'Advance', 'Reschedule')");
      table.HasCheckConstraint("CK_ProgressionRecommendations_Status", "\"Status\" IN ('Pending', 'Accepted', 'Rejected')");
      table.HasCheckConstraint("CK_ProgressionRecommendations_Reason", "length(\"Reason\") > 0");
      table.HasCheckConstraint("CK_ProgressionRecommendations_Decision", "(\"Status\" = 'Pending' AND \"DecidedAtUtc\" IS NULL) OR (\"Status\" <> 'Pending' AND \"DecidedAtUtc\" IS NOT NULL)");
      table.HasCheckConstraint("CK_ProgressionRecommendations_Version", "\"Version\" > 0");
    });
    recommendation.HasKey(entity => entity.Id);
    recommendation.Property(entity => entity.Action).HasMaxLength(20);
    recommendation.Property(entity => entity.Reason).HasMaxLength(500);
    recommendation.Property(entity => entity.AlgorithmVersion).HasMaxLength(50);
    recommendation.Property(entity => entity.Status).HasMaxLength(20);
    recommendation.Property(entity => entity.Version).IsConcurrencyToken();
    recommendation.HasIndex(entity => entity.OperationId).IsUnique();
    recommendation.HasIndex(entity => new { entity.UserProfileId, entity.WorkoutSessionId }).IsUnique();
    recommendation.HasOne<UserProfileEntity>().WithMany()
      .HasForeignKey(entity => entity.UserProfileId).OnDelete(DeleteBehavior.Cascade);
    recommendation.HasOne<WorkoutSessionEntity>().WithMany()
      .HasForeignKey(entity => entity.WorkoutSessionId).OnDelete(DeleteBehavior.Cascade);

    var backupPolicy = modelBuilder.Entity<LocalBackupPolicyEntity>();
    backupPolicy.ToTable("LocalBackupPolicies", table =>
    {
      table.HasCheckConstraint("CK_LocalBackupPolicies_Interval", "\"IntervalHours\" >= 1 AND \"IntervalHours\" <= 168");
      table.HasCheckConstraint("CK_LocalBackupPolicies_Retention", "\"RetentionCount\" >= 2 AND \"RetentionCount\" <= 60");
      table.HasCheckConstraint("CK_LocalBackupPolicies_Version", "\"Version\" > 0");
    });
    backupPolicy.HasKey(entity => entity.Id);
    backupPolicy.Property(entity => entity.DestinationPath).HasMaxLength(1024);
    backupPolicy.Property(entity => entity.Version).IsConcurrencyToken();

    var verification = modelBuilder.Entity<BackupVerificationEntity>();
    verification.ToTable("BackupVerifications", table =>
    {
      table.HasCheckConstraint("CK_BackupVerifications_Status", "\"Status\" IN ('Verified', 'Failed')");
      table.HasCheckConstraint("CK_BackupVerifications_Bytes", "\"BackupBytes\" >= 0");
      table.HasCheckConstraint("CK_BackupVerifications_Time", "\"CompletedAtUtc\" >= \"StartedAtUtc\"");
    });
    verification.HasKey(entity => entity.Id);
    verification.Property(entity => entity.BackupPath).HasMaxLength(2048);
    verification.Property(entity => entity.Status).HasMaxLength(20);
    verification.Property(entity => entity.Detail).HasMaxLength(1000);
    verification.HasIndex(entity => new { entity.LocalBackupPolicyId, entity.CompletedAtUtc });
    verification.HasOne<LocalBackupPolicyEntity>().WithMany()
      .HasForeignKey(entity => entity.LocalBackupPolicyId).OnDelete(DeleteBehavior.Cascade);
  }

  private static void ConfigureBleReliability(ModelBuilder modelBuilder)
  {
    var incident = modelBuilder.Entity<BleReliabilityIncidentEntity>();
    incident.ToTable("BleReliabilityIncidents", table =>
    {
      table.HasCheckConstraint(
        "CK_BleReliabilityIncidents_Role",
        "\"Role\" IN ('Treadmill', 'HeartRate')");
      table.HasCheckConstraint("CK_BleReliabilityIncidents_DisplayName", "length(\"DeviceDisplayName\") > 0");
      table.HasCheckConstraint(
        "CK_BleReliabilityIncidents_FailureKind",
        "\"FailureKind\" IN ('NativeDisconnected', 'TelemetrySilent', 'NotificationEnded', 'GattTimeout', 'InvalidTelemetry', 'RequiredCharacteristicMissing', 'AdapterUnavailable')");
      table.HasCheckConstraint("CK_BleReliabilityIncidents_Fault", "length(\"LastSanitizedFault\") > 0");
      table.HasCheckConstraint("CK_BleReliabilityIncidents_Attempts", "\"FailedAttemptCount\" > 0");
      table.HasCheckConstraint("CK_BleReliabilityIncidents_Delay", "\"MaximumReconnectDelaySeconds\" >= 0");
      table.HasCheckConstraint("CK_BleReliabilityIncidents_StartedAt", "\"StartedAtUnixMilliseconds\" >= 0");
      table.HasCheckConstraint(
        "CK_BleReliabilityIncidents_RecoveryTime",
        "\"RecoveredAtUnixMilliseconds\" IS NULL OR \"RecoveredAtUnixMilliseconds\" >= \"StartedAtUnixMilliseconds\"");
    });
    incident.HasKey(entity => entity.Id);
    incident.Property(entity => entity.Role).HasMaxLength(20);
    incident.Property(entity => entity.DeviceDisplayName).HasMaxLength(100);
    incident.Property(entity => entity.FailureKind).HasMaxLength(50);
    incident.Property(entity => entity.LastSanitizedFault).HasMaxLength(256);
    incident.HasIndex(entity => entity.DeviceEnrollmentId)
      .HasDatabaseName("UX_BleReliabilityIncidents_OneOpenPerDevice")
      .IsUnique()
      .HasFilter("\"RecoveredAtUnixMilliseconds\" IS NULL");
    incident.HasIndex(entity => new { entity.DeviceEnrollmentId, entity.RecoveredAtUnixMilliseconds });
    incident.HasIndex(entity => entity.StartedAtUnixMilliseconds);
  }

  private static void ConfigureTreadmillMaintenance(ModelBuilder modelBuilder)
  {
    var policy = modelBuilder.Entity<TreadmillMaintenancePolicyEntity>();
    policy.ToTable("TreadmillMaintenancePolicies", table =>
    {
      table.HasCheckConstraint("CK_TreadmillMaintenancePolicies_Months", "\"IntervalMonths\" >= 1 AND \"IntervalMonths\" <= 24");
      table.HasCheckConstraint("CK_TreadmillMaintenancePolicies_Distance", "\"DistanceIntervalKilometers\" >= 1 AND \"DistanceIntervalKilometers\" <= 5000");
      table.HasCheckConstraint("CK_TreadmillMaintenancePolicies_Version", "\"Version\" > 0");
    });
    policy.HasKey(entity => entity.Id);
    policy.Property(entity => entity.Version).IsConcurrencyToken();
    policy.HasIndex(entity => entity.DeviceEnrollmentId).IsUnique();
    policy.HasOne(entity => entity.DeviceEnrollment).WithOne()
      .HasForeignKey<TreadmillMaintenancePolicyEntity>(entity => entity.DeviceEnrollmentId)
      .OnDelete(DeleteBehavior.Cascade);

    var maintenanceEvent = modelBuilder.Entity<TreadmillMaintenanceEventEntity>();
    maintenanceEvent.ToTable("TreadmillMaintenanceEvents", table =>
    {
      table.HasCheckConstraint("CK_TreadmillMaintenanceEvents_Distance", "\"AppDistanceBaselineKilometers\" >= 0");
      table.HasCheckConstraint("CK_TreadmillMaintenanceEvents_Note", "\"Note\" IS NULL OR length(\"Note\") <= 500");
    });
    maintenanceEvent.HasKey(entity => entity.Id);
    maintenanceEvent.Property(entity => entity.Note).HasMaxLength(500);
    maintenanceEvent.HasIndex(entity => entity.OperationId).IsUnique();
    maintenanceEvent.HasIndex(entity => new { entity.TreadmillMaintenancePolicyId, entity.PerformedAtUtc });
    maintenanceEvent.HasOne(entity => entity.Policy).WithMany(entity => entity.Events)
      .HasForeignKey(entity => entity.TreadmillMaintenancePolicyId)
      .OnDelete(DeleteBehavior.Cascade);
  }

  private static void ConfigureGarmin(ModelBuilder modelBuilder)
  {
    var link = modelBuilder.Entity<GarminAccountLinkEntity>();
    link.ToTable("GarminAccountLinks", table =>
    {
      table.HasCheckConstraint("CK_GarminAccountLinks_Subject", "length(\"ProviderSubject\") > 0");
      table.HasCheckConstraint("CK_GarminAccountLinks_Label", "length(\"AccountLabel\") > 0");
      table.HasCheckConstraint("CK_GarminAccountLinks_AccessToken", "length(\"ProtectedAccessToken\") > 0");
      table.HasCheckConstraint("CK_GarminAccountLinks_Version", "\"Version\" > 0");
    });
    link.HasKey(entity => entity.Id);
    link.Property(entity => entity.ProviderSubject).HasMaxLength(256);
    link.Property(entity => entity.AccountLabel).HasMaxLength(160);
    link.Property(entity => entity.ProtectedAccessToken).HasMaxLength(8192);
    link.Property(entity => entity.ProtectedRefreshToken).HasMaxLength(8192);
    link.Property(entity => entity.Scopes).HasMaxLength(1000);
    link.Property(entity => entity.LastSyncError).HasMaxLength(1000);
    link.Property(entity => entity.Version).IsConcurrencyToken();
    link.HasIndex(entity => entity.UserProfileId).IsUnique();
    link.HasIndex(entity => entity.ProviderSubject).IsUnique();
    link.HasOne(entity => entity.UserProfile)
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);

    var oauthState = modelBuilder.Entity<GarminOAuthStateEntity>();
    oauthState.ToTable("GarminOAuthStates", table =>
    {
      table.HasCheckConstraint("CK_GarminOAuthStates_Hash", "length(\"StateHash\") = 64");
      table.HasCheckConstraint("CK_GarminOAuthStates_Verifier", "length(\"ProtectedCodeVerifier\") > 0");
      table.HasCheckConstraint("CK_GarminOAuthStates_Expiry", "\"ExpiresAtUtc\" > \"CreatedAtUtc\"");
    });
    oauthState.HasKey(entity => entity.StateHash);
    oauthState.Property(entity => entity.StateHash).HasMaxLength(64).IsFixedLength();
    oauthState.Property(entity => entity.ProtectedCodeVerifier).HasMaxLength(4096);
    oauthState.Property(entity => entity.RedirectUri).HasMaxLength(2048);
    oauthState.HasIndex(entity => entity.ExpiresAtUtc);
    oauthState.HasOne(entity => entity.UserProfile)
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);

    var syncItem = modelBuilder.Entity<GarminSyncItemEntity>();
    syncItem.ToTable("GarminSyncItems", table =>
    {
      table.HasCheckConstraint("CK_GarminSyncItems_Kind", "\"Kind\" IN ('Workout', 'TrainingPlan', 'Calendar')");
      table.HasCheckConstraint("CK_GarminSyncItems_Status", "\"Status\" IN ('Pending', 'InFlight', 'Synced', 'Failed')");
      table.HasCheckConstraint("CK_GarminSyncItems_Attempts", "\"AttemptCount\" >= 0");
      table.HasCheckConstraint("CK_GarminSyncItems_Key", "length(\"IdempotencyKey\") = 64");
    });
    syncItem.HasKey(entity => entity.Id);
    syncItem.Property(entity => entity.Kind).HasMaxLength(30);
    syncItem.Property(entity => entity.SourceVersion).HasMaxLength(128);
    syncItem.Property(entity => entity.IdempotencyKey).HasMaxLength(64).IsFixedLength();
    syncItem.Property(entity => entity.Status).HasMaxLength(20);
    syncItem.Property(entity => entity.RemoteId).HasMaxLength(256);
    syncItem.Property(entity => entity.LastError).HasMaxLength(1000);
    syncItem.HasIndex(entity => entity.IdempotencyKey).IsUnique();
    syncItem.HasIndex(entity => new { entity.Status, entity.AvailableAtUtc });
    syncItem.HasIndex(entity => new { entity.UserProfileId, entity.Kind, entity.SourceId });
    syncItem.HasOne(entity => entity.AccountLink)
      .WithMany(entity => entity.SyncItems)
      .HasForeignKey(entity => entity.GarminAccountLinkId)
      .OnDelete(DeleteBehavior.Cascade);

    var watchBinding = modelBuilder.Entity<GarminWatchBindingEntity>();
    watchBinding.ToTable("GarminWatchBindings", table =>
    {
      table.HasCheckConstraint("CK_GarminWatchBindings_Label", "length(\"DeviceLabel\") > 0");
      table.HasCheckConstraint("CK_GarminWatchBindings_Token", "length(\"TokenSha256\") = 64");
      table.HasCheckConstraint("CK_GarminWatchBindings_Version", "\"Version\" > 0");
    });
    watchBinding.HasKey(entity => entity.Id);
    watchBinding.Property(entity => entity.DeviceLabel).HasMaxLength(100);
    watchBinding.Property(entity => entity.TokenSha256).HasMaxLength(64).IsFixedLength();
    watchBinding.Property(entity => entity.Version).IsConcurrencyToken();
    watchBinding.HasIndex(entity => entity.UserProfileId).IsUnique();
    watchBinding.HasIndex(entity => entity.TokenSha256).IsUnique();
    watchBinding.HasOne(entity => entity.UserProfile)
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);

    var uploadAccount = modelBuilder.Entity<GarminActivityUploadAccountEntity>();
    uploadAccount.ToTable("GarminActivityUploadAccounts", table =>
    {
      table.HasCheckConstraint("CK_GarminActivityUploadAccounts_Label", "length(\"AccountLabel\") > 0");
      table.HasCheckConstraint("CK_GarminActivityUploadAccounts_Tokens", "length(\"ProtectedTokenStore\") > 0");
      table.HasCheckConstraint("CK_GarminActivityUploadAccounts_State", "\"State\" IN ('Connected', 'NeedsAuthentication', 'ProviderUnavailable')");
      table.HasCheckConstraint("CK_GarminActivityUploadAccounts_Version", "\"Version\" > 0");
    });
    uploadAccount.HasKey(entity => entity.Id);
    uploadAccount.Property(entity => entity.AccountLabel).HasMaxLength(160);
    uploadAccount.Property(entity => entity.ProtectedTokenStore).HasMaxLength(32768);
    uploadAccount.Property(entity => entity.State).HasMaxLength(30);
    uploadAccount.Property(entity => entity.LastError).HasMaxLength(1000);
    uploadAccount.Property(entity => entity.Version).IsConcurrencyToken();
    uploadAccount.HasIndex(entity => entity.UserProfileId).IsUnique();
    uploadAccount.HasOne(entity => entity.UserProfile).WithMany()
      .HasForeignKey(entity => entity.UserProfileId).OnDelete(DeleteBehavior.Cascade);

    var uploadJob = modelBuilder.Entity<GarminActivityUploadJobEntity>();
    uploadJob.ToTable("GarminActivityUploadJobs", table =>
    {
      table.HasCheckConstraint("CK_GarminActivityUploadJobs_Status", "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed', 'FoundInGarmin')");
      table.HasCheckConstraint("CK_GarminActivityUploadJobs_Attempts", "\"AttemptCount\" >= 0 AND \"AttemptCount\" <= 3");
      table.HasCheckConstraint("CK_GarminActivityUploadJobs_Key", "length(\"IdempotencyKey\") = 64");
    });
    uploadJob.HasKey(entity => entity.Id);
    uploadJob.Property(entity => entity.IdempotencyKey).HasMaxLength(64).IsFixedLength();
    uploadJob.Property(entity => entity.Status).HasMaxLength(20);
    uploadJob.Property(entity => entity.RemoteId).HasMaxLength(256);
    uploadJob.Property(entity => entity.FailureKind).HasMaxLength(30);
    uploadJob.Property(entity => entity.LastError).HasMaxLength(1000);
    uploadJob.HasIndex(entity => entity.WorkoutSessionId).IsUnique();
    uploadJob.HasIndex(entity => entity.IdempotencyKey).IsUnique();
    uploadJob.HasIndex(entity => new { entity.Status, entity.AvailableAtUtc });
    uploadJob.HasOne(entity => entity.Account).WithMany(entity => entity.Jobs)
      .HasForeignKey(entity => entity.GarminActivityUploadAccountId).OnDelete(DeleteBehavior.Cascade);
    uploadJob.HasOne<WorkoutSessionEntity>().WithMany()
      .HasForeignKey(entity => entity.WorkoutSessionId).OnDelete(DeleteBehavior.Cascade);
  }

  private static void ConfigureDeviceEnrollments(ModelBuilder modelBuilder)
  {
    var enrollment = modelBuilder.Entity<DeviceEnrollmentEntity>();
    enrollment.ToTable("DeviceEnrollments", table =>
    {
      table.HasCheckConstraint("CK_DeviceEnrollments_Role", "\"Role\" IN ('Treadmill', 'HeartRate')");
      table.HasCheckConstraint("CK_DeviceEnrollments_DeviceId", "length(\"DeviceId\") > 0");
      table.HasCheckConstraint("CK_DeviceEnrollments_Protocol", "length(\"ProtocolId\") > 0");
      table.HasCheckConstraint("CK_DeviceEnrollments_Fingerprint", "length(\"IdentityFingerprint\") = 64");
      table.HasCheckConstraint("CK_DeviceEnrollments_Version", "\"Version\" > 0");
      table.HasCheckConstraint("CK_DeviceEnrollments_Archive", "(\"IsArchived\" = 0 AND \"ArchivedAtUtc\" IS NULL) OR (\"IsArchived\" = 1 AND \"ArchivedAtUtc\" IS NOT NULL)");
      table.HasCheckConstraint("CK_DeviceEnrollments_TreadmillSettings", "(\"Role\" = 'Treadmill' AND \"TelemetryMode\" IS NOT NULL AND \"CapabilitiesJson\" IS NOT NULL) OR (\"Role\" = 'HeartRate' AND \"TelemetryMode\" IS NULL AND \"CapabilitiesJson\" IS NULL)");
    });
    enrollment.HasKey(entity => entity.Id);
    enrollment.Property(entity => entity.Role).HasMaxLength(20);
    enrollment.Property(entity => entity.DeviceId).HasMaxLength(256);
    enrollment.Property(entity => entity.ProtocolId).HasMaxLength(100);
    enrollment.Property(entity => entity.IdentityFingerprint).HasMaxLength(64).IsFixedLength();
    enrollment.Property(entity => entity.DisplayName).HasMaxLength(100);
    enrollment.Property(entity => entity.ModelNumber).HasMaxLength(100);
    enrollment.Property(entity => entity.FirmwareRevision).HasMaxLength(100);
    enrollment.Property(entity => entity.TelemetryMode).HasMaxLength(20);
    enrollment.Property(entity => entity.Evidence).HasMaxLength(30);
    enrollment.Property(entity => entity.Version).IsConcurrencyToken();
    enrollment.Property(entity => entity.HeartRateDeviceKind).HasMaxLength(20);
    enrollment.Property(entity => entity.HeartRateDeviceFamily).HasMaxLength(20);
    enrollment.HasIndex(entity => entity.Role).IsUnique().HasFilter("\"Role\" = 'Treadmill' AND \"IsArchived\" = 0");
    enrollment.HasIndex(entity => new { entity.Role, entity.DeviceId }).IsUnique().HasFilter("\"IsArchived\" = 0");
    enrollment.HasIndex(entity => entity.IdentityFingerprint);
  }

  private static void ConfigureHeartRateDeviceAssignments(ModelBuilder modelBuilder)
  {
    var assignment = modelBuilder.Entity<HeartRateDeviceAssignmentEntity>();
    assignment.ToTable("HeartRateDeviceAssignments", table =>
    {
      table.HasCheckConstraint("CK_HeartRateDeviceAssignments_Priority", "\"Priority\" >= 0 AND \"Priority\" <= 99");
      table.HasCheckConstraint("CK_HeartRateDeviceAssignments_Version", "\"Version\" > 0");
    });
    assignment.HasKey(entity => entity.Id);
    assignment.Property(entity => entity.Version).IsConcurrencyToken();
    assignment.HasIndex(entity => new { entity.UserProfileId, entity.DeviceEnrollmentId }).IsUnique();
    assignment.HasIndex(entity => entity.UserProfileId).IsUnique().HasFilter("\"IsPreferred\" = 1");
    assignment.HasOne(entity => entity.UserProfile)
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Restrict);
    assignment.HasOne(entity => entity.DeviceEnrollment)
      .WithMany()
      .HasForeignKey(entity => entity.DeviceEnrollmentId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigureProfiles(ModelBuilder modelBuilder)
  {
    var profile = modelBuilder.Entity<UserProfileEntity>();
    profile.ToTable("UserProfiles", table =>
    {
      table.HasCheckConstraint("CK_UserProfiles_DisplayName", "length(\"DisplayName\") > 0");
      table.HasCheckConstraint("CK_UserProfiles_Weight", "\"WeightKilograms\" > 0");
      table.HasCheckConstraint("CK_UserProfiles_MaximumHeartRate", "\"MaximumHeartRateBpm\" IS NULL OR \"MaximumHeartRateBpm\" > 0");
      table.HasCheckConstraint("CK_UserProfiles_MaximumSpeed", "\"MaximumSpeedKph\" IS NULL OR \"MaximumSpeedKph\" > 0");
      table.HasCheckConstraint("CK_UserProfiles_HrIncreaseStep", "\"HeartRateIncreaseStepKph\" >= 0.1 AND \"HeartRateIncreaseStepKph\" <= 0.5");
      table.HasCheckConstraint("CK_UserProfiles_HrIncreaseCooldown", "\"HeartRateIncreaseCooldownSeconds\" >= 15 AND \"HeartRateIncreaseCooldownSeconds\" <= 180");
      table.HasCheckConstraint("CK_UserProfiles_HrDecreaseStep", "\"HeartRateDecreaseStepKph\" >= 0.1 AND \"HeartRateDecreaseStepKph\" <= 1.0");
      table.HasCheckConstraint("CK_UserProfiles_HrDecreaseCooldown", "\"HeartRateDecreaseCooldownSeconds\" >= 5 AND \"HeartRateDecreaseCooldownSeconds\" <= 120");
      table.HasCheckConstraint("CK_UserProfiles_Version", "\"Version\" > 0");
      table.HasCheckConstraint("CK_UserProfiles_Archive", "(\"IsArchived\" = 0 AND \"ArchivedAtUtc\" IS NULL) OR (\"IsArchived\" = 1 AND \"ArchivedAtUtc\" IS NOT NULL)");
    });
    profile.HasKey(entity => entity.Id);
    profile.Property(entity => entity.DisplayName).HasMaxLength(100);
    profile.Property(entity => entity.NormalizedDisplayName).HasMaxLength(100);
    profile.Property(entity => entity.UnitSystem).HasMaxLength(20);
    profile.Property(entity => entity.Version).IsConcurrencyToken();
    profile.HasIndex(entity => entity.NormalizedDisplayName).IsUnique();

    var zone = modelBuilder.Entity<HeartRateZoneEntity>();
    zone.ToTable("HeartRateZones", table =>
    {
      table.HasCheckConstraint("CK_HeartRateZones_Number", "\"Number\" > 0");
      table.HasCheckConstraint("CK_HeartRateZones_Range", "\"MinimumBpm\" <= \"MaximumBpm\"");
    });
    zone.HasKey(entity => entity.Id);
    zone.Property(entity => entity.Name).HasMaxLength(60);
    zone.HasIndex(entity => new { entity.UserProfileId, entity.Number }).IsUnique();
    zone.HasOne(entity => entity.UserProfile)
      .WithMany(entity => entity.HeartRateZones)
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);
  }

  private static void ConfigureWorkouts(ModelBuilder modelBuilder)
  {
    var workout = modelBuilder.Entity<WorkoutEntity>();
    workout.ToTable("Workouts", table =>
    {
      table.HasCheckConstraint("CK_Workouts_Name", "length(\"Name\") > 0");
      table.HasCheckConstraint("CK_Workouts_Kind", "\"Kind\" IN ('Structured', 'ManualTemplate', 'PlanInternal')");
    });
    workout.HasKey(entity => entity.Id);
    workout.Property(entity => entity.Name).HasMaxLength(160);
    workout.Property(entity => entity.Kind).HasMaxLength(20);

    var revision = modelBuilder.Entity<WorkoutRevisionEntity>();
    revision.ToTable("WorkoutRevisions", table =>
    {
      table.HasCheckConstraint("CK_WorkoutRevisions_Number", "\"RevisionNumber\" > 0");
      table.HasCheckConstraint("CK_WorkoutRevisions_Json", "length(\"DefinitionJson\") > 0");
      table.HasCheckConstraint("CK_WorkoutRevisions_Hash", "length(\"ContentSha256\") = 64");
    });
    revision.HasKey(entity => entity.Id);
    revision.Property(entity => entity.ContentSha256).HasMaxLength(64).IsFixedLength();
    revision.HasIndex(entity => new { entity.WorkoutId, entity.RevisionNumber }).IsUnique();
    revision.HasIndex(entity => new { entity.WorkoutId, entity.ContentSha256 }).IsUnique();
    revision.HasOne(entity => entity.Workout)
      .WithMany(entity => entity.Revisions)
      .HasForeignKey(entity => entity.WorkoutId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigureImports(ModelBuilder modelBuilder)
  {
    var import = modelBuilder.Entity<ImportAuditEntity>();
    import.ToTable("ImportAudits");
    import.HasKey(entity => entity.Id);
    import.Property(entity => entity.OriginalFileName).HasMaxLength(255);
    import.Property(entity => entity.Format).HasMaxLength(32);
    import.Property(entity => entity.SourceSha256).HasMaxLength(64).IsFixedLength();
    import.HasIndex(entity => new { entity.Format, entity.SourceSha256 });
    import.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.SetNull);
    import.HasOne<WorkoutEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutId)
      .OnDelete(DeleteBehavior.Restrict);
    import.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigureCalendar(ModelBuilder modelBuilder)
  {
    var series = modelBuilder.Entity<CalendarSeriesEntity>();
    series.ToTable("CalendarSeries", table =>
    {
      table.HasCheckConstraint("CK_CalendarSeries_Interval", "\"IntervalWeeks\" > 0");
      table.HasCheckConstraint("CK_CalendarSeries_Weekdays", "\"WeekdayMask\" > 0 AND \"WeekdayMask\" <= 127");
      table.HasCheckConstraint("CK_CalendarSeries_DateRange", "\"EndDate\" IS NULL OR \"EndDate\" >= \"StartDate\"");
      table.HasCheckConstraint("CK_CalendarSeries_Version", "\"Version\" > 0");
    });
    series.HasKey(entity => entity.Id);
    series.Property(entity => entity.Name).HasMaxLength(160);
    series.Property(entity => entity.TimeZoneId).HasMaxLength(100);
    series.Property(entity => entity.Version).IsConcurrencyToken();
    series.HasIndex(entity => new { entity.UserProfileId, entity.ScheduleGroupId });
    series.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);

    var seriesOption = modelBuilder.Entity<CalendarSeriesOptionEntity>();
    seriesOption.ToTable("CalendarSeriesOptions");
    seriesOption.HasKey(entity => entity.Id);
    seriesOption.HasIndex(entity => new { entity.CalendarSeriesId, entity.DisplayOrder }).IsUnique();
    seriesOption.HasOne(entity => entity.CalendarSeries)
      .WithMany(entity => entity.Options)
      .HasForeignKey(entity => entity.CalendarSeriesId)
      .OnDelete(DeleteBehavior.Cascade);
    seriesOption.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);

    var exception = modelBuilder.Entity<CalendarExceptionEntity>();
    exception.ToTable("CalendarExceptions");
    exception.HasKey(entity => entity.Id);
    exception.Property(entity => entity.Kind).HasMaxLength(20);
    exception.Property(entity => entity.Note).HasMaxLength(500);
    exception.HasIndex(entity => new { entity.CalendarSeriesId, entity.LocalDate }).IsUnique();
    exception.HasOne(entity => entity.CalendarSeries)
      .WithMany(entity => entity.Exceptions)
      .HasForeignKey(entity => entity.CalendarSeriesId)
      .OnDelete(DeleteBehavior.Cascade);

    var exceptionOption = modelBuilder.Entity<CalendarExceptionOptionEntity>();
    exceptionOption.ToTable("CalendarExceptionOptions");
    exceptionOption.HasKey(entity => entity.Id);
    exceptionOption.HasIndex(entity => new { entity.CalendarExceptionId, entity.DisplayOrder }).IsUnique();
    exceptionOption.HasOne(entity => entity.CalendarException)
      .WithMany(entity => entity.Options)
      .HasForeignKey(entity => entity.CalendarExceptionId)
      .OnDelete(DeleteBehavior.Cascade);
    exceptionOption.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);

    var selection = modelBuilder.Entity<TrainingDaySelectionEntity>();
    selection.ToTable("TrainingDaySelections");
    selection.HasKey(entity => entity.Id);
    selection.HasIndex(entity => new { entity.UserProfileId, entity.LocalDate }).IsUnique();
    selection.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Cascade);
    selection.HasOne<CalendarSeriesEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.CalendarSeriesId)
      .OnDelete(DeleteBehavior.Restrict);
    selection.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigureOperationReceipts(ModelBuilder modelBuilder)
  {
    var receipt = modelBuilder.Entity<OperationReceiptEntity>();
    receipt.ToTable("OperationReceipts");
    receipt.HasKey(entity => entity.Id);
    receipt.Property(entity => entity.OperationType).HasMaxLength(100);
    receipt.Property(entity => entity.RequestFingerprint).HasMaxLength(64).IsFixedLength();
    receipt.HasIndex(entity => entity.ClientOperationId).IsUnique();
  }

  private static void ConfigureWorkoutPrograms(ModelBuilder modelBuilder)
  {
    var program = modelBuilder.Entity<WorkoutProgramEntity>();
    program.ToTable("WorkoutPrograms");
    program.HasKey(entity => entity.Id);

    var revision = modelBuilder.Entity<WorkoutProgramRevisionEntity>();
    revision.ToTable("WorkoutProgramRevisions", table =>
    {
      table.HasCheckConstraint("CK_WorkoutProgramRevisions_Number", "\"RevisionNumber\" > 0");
      table.HasCheckConstraint("CK_WorkoutProgramRevisions_Name", "length(\"Name\") > 0");
      table.HasCheckConstraint("CK_WorkoutProgramRevisions_Category", "length(\"Category\") > 0");
      table.HasCheckConstraint("CK_WorkoutProgramRevisions_Hash", "length(\"ContentSha256\") = 64");
    });
    revision.HasKey(entity => entity.Id);
    revision.Property(entity => entity.Name).HasMaxLength(160);
    revision.Property(entity => entity.Description).HasMaxLength(2000);
    revision.Property(entity => entity.Category).HasMaxLength(40);
    revision.Property(entity => entity.ContentSha256).HasMaxLength(64).IsFixedLength();
    revision.Property(entity => entity.TemplateId).HasMaxLength(100);
    revision.Property(entity => entity.TemplateVersion).HasMaxLength(40);
    revision.HasIndex(entity => new { entity.WorkoutProgramId, entity.RevisionNumber }).IsUnique();
    revision.HasIndex(entity => new { entity.WorkoutProgramId, entity.ContentSha256 }).IsUnique();
    revision.HasOne(entity => entity.WorkoutProgram)
      .WithMany(entity => entity.Revisions)
      .HasForeignKey(entity => entity.WorkoutProgramId)
      .OnDelete(DeleteBehavior.Restrict);
    revision.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.OwnerProfileId)
      .OnDelete(DeleteBehavior.Restrict);

    var item = modelBuilder.Entity<WorkoutProgramItemEntity>();
    item.ToTable("WorkoutProgramItems", table =>
      table.HasCheckConstraint("CK_WorkoutProgramItems_Position", "\"Position\" > 0"));
    item.HasKey(entity => entity.Id);
    item.Property(entity => entity.Phase).HasMaxLength(80);
    item.HasIndex(entity => new { entity.WorkoutProgramRevisionId, entity.Position }).IsUnique();
    item.HasOne(entity => entity.WorkoutProgramRevision)
      .WithMany(entity => entity.Items)
      .HasForeignKey(entity => entity.WorkoutProgramRevisionId)
      .OnDelete(DeleteBehavior.Restrict);
    item.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);

    var itemAlternative = modelBuilder.Entity<WorkoutProgramItemAlternativeEntity>();
    itemAlternative.ToTable("WorkoutProgramItemAlternatives", table =>
      table.HasCheckConstraint("CK_WorkoutProgramItemAlternatives_DisplayOrder", "\"DisplayOrder\" > 0"));
    itemAlternative.HasKey(entity => entity.Id);
    itemAlternative.Property(entity => entity.Variant).HasMaxLength(40);
    itemAlternative.HasIndex(entity => new { entity.WorkoutProgramItemId, entity.DisplayOrder }).IsUnique();
    itemAlternative.HasIndex(entity => new { entity.WorkoutProgramItemId, entity.WorkoutRevisionId }).IsUnique();
    itemAlternative.HasOne(entity => entity.WorkoutProgramItem)
      .WithMany(entity => entity.Alternatives)
      .HasForeignKey(entity => entity.WorkoutProgramItemId)
      .OnDelete(DeleteBehavior.Cascade);
    itemAlternative.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);

    var run = modelBuilder.Entity<WorkoutProgramRunEntity>();
    run.ToTable("WorkoutProgramRuns", table =>
    {
      table.HasCheckConstraint("CK_WorkoutProgramRuns_Status", "\"Status\" IN ('Active', 'Completed', 'Abandoned')");
      table.HasCheckConstraint("CK_WorkoutProgramRuns_Version", "\"Version\" > 0");
      table.HasCheckConstraint(
        "CK_WorkoutProgramRuns_Schedule",
        "(\"ScheduledStartDate\" IS NULL AND \"ScheduledWeekdayMask\" = 0 AND \"ScheduleTimeZoneId\" IS NULL) OR " +
        "(\"ScheduledStartDate\" IS NOT NULL AND \"ScheduledWeekdayMask\" BETWEEN 1 AND 127 AND length(\"ScheduleTimeZoneId\") > 0)");
    });
    run.HasKey(entity => entity.Id);
    run.Property(entity => entity.Status).HasMaxLength(20);
    run.Property(entity => entity.ScheduleTimeZoneId).HasMaxLength(100);
    run.Property(entity => entity.Version).IsConcurrencyToken();
    run.HasIndex(entity => entity.UserProfileId).IsUnique().HasFilter("\"Status\" = 'Active'");
    run.HasIndex(entity => new { entity.UserProfileId, entity.StartedAtUtc });
    run.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Restrict);
    run.HasOne<WorkoutProgramRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramRevisionId)
      .OnDelete(DeleteBehavior.Restrict);

    var scheduleOverride = modelBuilder.Entity<WorkoutProgramScheduleOverrideEntity>();
    scheduleOverride.ToTable("WorkoutProgramScheduleOverrides", table =>
      table.HasCheckConstraint(
        "CK_WorkoutProgramScheduleOverrides_Value",
        "(\"IsSkipped\" = 1 AND \"TargetDate\" IS NULL) OR (\"IsSkipped\" = 0 AND \"TargetDate\" IS NOT NULL)"));
    scheduleOverride.HasKey(entity => entity.Id);
    scheduleOverride.HasIndex(entity => new { entity.WorkoutProgramRunId, entity.WorkoutProgramItemId }).IsUnique();
    scheduleOverride.HasOne<WorkoutProgramRunEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramRunId)
      .OnDelete(DeleteBehavior.Cascade);
    scheduleOverride.HasOne<WorkoutProgramItemEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramItemId)
      .OnDelete(DeleteBehavior.Restrict);

    var extraOccurrence = modelBuilder.Entity<WorkoutProgramExtraOccurrenceEntity>();
    extraOccurrence.ToTable("WorkoutProgramExtraOccurrences");
    extraOccurrence.HasKey(entity => entity.Id);
    extraOccurrence.HasIndex(entity => new { entity.WorkoutProgramRunId, entity.Date });
    extraOccurrence.HasOne<WorkoutProgramRunEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramRunId)
      .OnDelete(DeleteBehavior.Cascade);
    extraOccurrence.HasOne<WorkoutProgramItemEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramItemId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigurePremadePlanInstallations(ModelBuilder modelBuilder)
  {
    var installation = modelBuilder.Entity<PremadePlanInstallationEntity>();
    installation.ToTable("PremadePlanInstallations", table =>
    {
      table.HasCheckConstraint("CK_PremadePlanInstallations_CopyNumber", "\"CopyNumber\" > 0");
      table.HasCheckConstraint("CK_PremadePlanInstallations_TemplateId", "length(\"TemplateId\") > 0");
      table.HasCheckConstraint("CK_PremadePlanInstallations_TemplateVersion", "length(\"TemplateVersion\") > 0");
      table.HasCheckConstraint("CK_PremadePlanInstallations_Hash", "length(\"TemplateContentSha256\") = 64");
    });
    installation.HasKey(entity => entity.Id);
    installation.Property(entity => entity.TemplateId).HasMaxLength(100);
    installation.Property(entity => entity.TemplateVersion).HasMaxLength(40);
    installation.Property(entity => entity.TemplateContentSha256).HasMaxLength(64).IsFixedLength();
    installation.HasIndex(entity => new
    {
      entity.UserProfileId,
      entity.TemplateId,
      entity.TemplateVersion,
      entity.CopyNumber,
    }).IsUnique();
    installation.HasIndex(entity => entity.WorkoutProgramId).IsUnique();
    installation.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Restrict);
    installation.HasOne<WorkoutProgramEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramId)
      .OnDelete(DeleteBehavior.Restrict);
  }

  private static void ConfigureSessions(ModelBuilder modelBuilder)
  {
    var session = modelBuilder.Entity<WorkoutSessionEntity>();
    session.ToTable("WorkoutSessions", table =>
    {
      table.HasCheckConstraint("CK_WorkoutSessions_State", "length(\"State\") > 0");
      table.HasCheckConstraint("CK_WorkoutSessions_Duration", "\"DurationSeconds\" >= 0");
      table.HasCheckConstraint("CK_WorkoutSessions_Distance", "\"DistanceKilometers\" >= 0");
      table.HasCheckConstraint("CK_WorkoutSessions_Calories", "\"EstimatedCalories\" >= 0");
      table.HasCheckConstraint("CK_WorkoutSessions_Rpe", "\"PerceivedExertion\" IS NULL OR (\"PerceivedExertion\" >= 1 AND \"PerceivedExertion\" <= 10)");
      table.HasCheckConstraint("CK_WorkoutSessions_Origin", "\"SessionOrigin\" IN ('Legacy', 'Hardware', 'Simulator', 'SystemTest')");
    });
    session.HasKey(entity => entity.Id);
    session.Property(entity => entity.State).HasMaxLength(40);
    session.Property(entity => entity.UserProfileName).HasMaxLength(100);
    session.Property(entity => entity.WorkoutTitle).HasMaxLength(160);
    session.Property(entity => entity.SelectionSource).HasMaxLength(20);
    session.Property(entity => entity.SessionOrigin).HasMaxLength(20);
    session.Property(entity => entity.MetricAlgorithmVersion).HasMaxLength(60);
    session.Property(entity => entity.RecoveryCheckpointJson).HasMaxLength(16_384);
    session.Property(entity => entity.DebriefNote).HasMaxLength(1000);
    session.HasIndex(entity => new { entity.UserProfileId, entity.ArmedAtUtc });
    session.HasIndex(entity => entity.State);
    session.HasIndex(entity => new { entity.UserProfileId, entity.SessionOrigin, entity.EndedAtUtc });
    session.HasIndex(entity => new { entity.WorkoutProgramRunId, entity.WorkoutProgramItemId })
      .IsUnique()
      .HasFilter("\"State\" = 'Completed' AND \"WorkoutProgramRunId\" IS NOT NULL");
    session.HasOne<UserProfileEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.UserProfileId)
      .OnDelete(DeleteBehavior.Restrict);
    session.HasOne<WorkoutRevisionEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutRevisionId)
      .OnDelete(DeleteBehavior.Restrict);
    session.HasOne<WorkoutProgramRunEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramRunId)
      .OnDelete(DeleteBehavior.Restrict);
    session.HasOne<WorkoutProgramItemEntity>()
      .WithMany()
      .HasForeignKey(entity => entity.WorkoutProgramItemId)
      .OnDelete(DeleteBehavior.Restrict);

    var sample = modelBuilder.Entity<SessionSampleEntity>();
    sample.ToTable("SessionSamples", table =>
    {
      table.HasCheckConstraint("CK_SessionSamples_Sequence", "\"Sequence\" >= 0");
      table.HasCheckConstraint("CK_SessionSamples_Elapsed", "\"ElapsedMilliseconds\" >= 0");
      table.HasCheckConstraint("CK_SessionSamples_Speeds", "(\"PlannedSpeedKph\" IS NULL OR \"PlannedSpeedKph\" >= 0) AND \"RequestedSpeedKph\" >= 0 AND \"MeasuredSpeedKph\" >= 0");
      table.HasCheckConstraint("CK_SessionSamples_Distance", "\"DistanceKilometers\" >= 0");
      table.HasCheckConstraint("CK_SessionSamples_Calories", "\"EstimatedCalories\" >= 0");
      table.HasCheckConstraint("CK_SessionSamples_TelemetryAge", "\"TelemetryAgeMilliseconds\" >= 0");
    });
    sample.HasKey(entity => new { entity.WorkoutSessionId, entity.Sequence });
    sample.Property(entity => entity.MetricAlgorithmVersion).HasMaxLength(60);
    sample.HasIndex(entity => new { entity.WorkoutSessionId, entity.CapturedAtUtc });
    sample.HasOne(entity => entity.WorkoutSession)
      .WithMany(entity => entity.Samples)
      .HasForeignKey(entity => entity.WorkoutSessionId)
      .OnDelete(DeleteBehavior.Cascade);

    var sessionEvent = modelBuilder.Entity<SessionEventEntity>();
    sessionEvent.ToTable("SessionEvents", table =>
      table.HasCheckConstraint("CK_SessionEvents_Kind", "length(\"Kind\") > 0"));
    sessionEvent.HasKey(entity => entity.Id);
    sessionEvent.Property(entity => entity.Kind).HasMaxLength(80);
    sessionEvent.HasIndex(entity => new { entity.WorkoutSessionId, entity.OccurredAtUtc });
    sessionEvent.HasOne(entity => entity.WorkoutSession)
      .WithMany(entity => entity.Events)
      .HasForeignKey(entity => entity.WorkoutSessionId)
      .OnDelete(DeleteBehavior.Cascade);
  }

  private void RejectWorkoutRevisionMutation()
  {
    var mutation = ChangeTracker.Entries<WorkoutRevisionEntity>()
      .FirstOrDefault(entry => entry.State is EntityState.Modified or EntityState.Deleted);
    if (mutation is not null)
    {
      throw new InvalidOperationException(
        "Workout revisions are immutable and cannot be updated or deleted.");
    }

    var programMutation = ChangeTracker.Entries<WorkoutProgramRevisionEntity>()
      .FirstOrDefault(entry => entry.State is EntityState.Modified or EntityState.Deleted);
    if (programMutation is not null)
    {
      throw new InvalidOperationException(
        "Workout program revisions are immutable and cannot be updated or deleted.");
    }
  }
}
