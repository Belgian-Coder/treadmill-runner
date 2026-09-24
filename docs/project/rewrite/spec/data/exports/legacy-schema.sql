CREATE TABLE "BackupVerifications" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_BackupVerifications" PRIMARY KEY,
    "LocalBackupPolicyId" TEXT NOT NULL,
    "BackupPath" TEXT NOT NULL,
    "Status" TEXT NOT NULL,
    "Detail" TEXT NOT NULL,
    "BackupBytes" INTEGER NOT NULL,
    "StartedAtUtc" TEXT NOT NULL,
    "CompletedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_BackupVerifications_Bytes" CHECK ("BackupBytes" >= 0),
    CONSTRAINT "CK_BackupVerifications_Status" CHECK ("Status" IN ('Verified', 'Failed')),
    CONSTRAINT "CK_BackupVerifications_Time" CHECK ("CompletedAtUtc" >= "StartedAtUtc"),
    CONSTRAINT "FK_BackupVerifications_LocalBackupPolicies_LocalBackupPolicyId" FOREIGN KEY ("LocalBackupPolicyId") REFERENCES "LocalBackupPolicies" ("Id") ON DELETE CASCADE
);

CREATE TABLE "BleReliabilityIncidents" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_BleReliabilityIncidents" PRIMARY KEY,
    "DeviceEnrollmentId" TEXT NOT NULL,
    "Role" TEXT NOT NULL,
    "DeviceDisplayName" TEXT NOT NULL,
    "StartedAtUnixMilliseconds" INTEGER NOT NULL,
    "RecoveredAtUnixMilliseconds" INTEGER NULL,
    "FirstConnectionGeneration" INTEGER NOT NULL,
    "RecoveredConnectionGeneration" INTEGER NULL,
    "FailedAttemptCount" INTEGER NOT NULL,
    "FailureKind" TEXT NOT NULL,
    "LastSanitizedFault" TEXT NOT NULL,
    "MaximumReconnectDelaySeconds" REAL NOT NULL,
    CONSTRAINT "CK_BleReliabilityIncidents_Attempts" CHECK ("FailedAttemptCount" > 0),
    CONSTRAINT "CK_BleReliabilityIncidents_Delay" CHECK ("MaximumReconnectDelaySeconds" >= 0),
    CONSTRAINT "CK_BleReliabilityIncidents_DisplayName" CHECK (length("DeviceDisplayName") > 0),
    CONSTRAINT "CK_BleReliabilityIncidents_FailureKind" CHECK ("FailureKind" IN ('NativeDisconnected', 'TelemetrySilent', 'NotificationEnded', 'GattTimeout', 'InvalidTelemetry', 'RequiredCharacteristicMissing', 'AdapterUnavailable')),
    CONSTRAINT "CK_BleReliabilityIncidents_Fault" CHECK (length("LastSanitizedFault") > 0),
    CONSTRAINT "CK_BleReliabilityIncidents_RecoveryTime" CHECK ("RecoveredAtUnixMilliseconds" IS NULL OR "RecoveredAtUnixMilliseconds" >= "StartedAtUnixMilliseconds"),
    CONSTRAINT "CK_BleReliabilityIncidents_Role" CHECK ("Role" IN ('Treadmill', 'HeartRate')),
    CONSTRAINT "CK_BleReliabilityIncidents_StartedAt" CHECK ("StartedAtUnixMilliseconds" >= 0)
);

CREATE TABLE "CalendarExceptionOptions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_CalendarExceptionOptions" PRIMARY KEY,
    "CalendarExceptionId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "DisplayOrder" INTEGER NOT NULL,
    CONSTRAINT "FK_CalendarExceptionOptions_CalendarExceptions_CalendarExceptionId" FOREIGN KEY ("CalendarExceptionId") REFERENCES "CalendarExceptions" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_CalendarExceptionOptions_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "CalendarExceptions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_CalendarExceptions" PRIMARY KEY,
    "CalendarSeriesId" TEXT NOT NULL,
    "LocalDate" TEXT NOT NULL,
    "Kind" TEXT NOT NULL,
    "Note" TEXT NULL,
    CONSTRAINT "FK_CalendarExceptions_CalendarSeries_CalendarSeriesId" FOREIGN KEY ("CalendarSeriesId") REFERENCES "CalendarSeries" ("Id") ON DELETE CASCADE
);

CREATE TABLE "CalendarSeries" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_CalendarSeries" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "Name" TEXT NOT NULL,
    "TimeZoneId" TEXT NOT NULL,
    "StartDate" TEXT NOT NULL,
    "EndDate" TEXT NULL,
    "IntervalWeeks" INTEGER NOT NULL,
    "WeekdayMask" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL, "ScheduleGroupId" TEXT NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    CONSTRAINT "CK_CalendarSeries_DateRange" CHECK ("EndDate" IS NULL OR "EndDate" >= "StartDate"),
    CONSTRAINT "CK_CalendarSeries_Interval" CHECK ("IntervalWeeks" > 0),
    CONSTRAINT "CK_CalendarSeries_Version" CHECK ("Version" > 0),
    CONSTRAINT "CK_CalendarSeries_Weekdays" CHECK ("WeekdayMask" > 0 AND "WeekdayMask" <= 127),
    CONSTRAINT "FK_CalendarSeries_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "CalendarSeriesOptions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_CalendarSeriesOptions" PRIMARY KEY,
    "CalendarSeriesId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "DisplayOrder" INTEGER NOT NULL,
    CONSTRAINT "FK_CalendarSeriesOptions_CalendarSeries_CalendarSeriesId" FOREIGN KEY ("CalendarSeriesId") REFERENCES "CalendarSeries" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_CalendarSeriesOptions_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "DeviceEnrollments" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_DeviceEnrollments" PRIMARY KEY,
    "Role" TEXT NOT NULL,
    "DeviceId" TEXT NOT NULL,
    "ProtocolId" TEXT NOT NULL,
    "IdentityFingerprint" TEXT NOT NULL,
    "DisplayName" TEXT NOT NULL,
    "ModelNumber" TEXT NULL,
    "FirmwareRevision" TEXT NULL,
    "TelemetryMode" TEXT NULL,
    "CapabilitiesJson" TEXT NULL,
    "Evidence" TEXT NOT NULL,
    "LastVerifiedAtUtc" TEXT NULL,
    "Version" INTEGER NOT NULL,
    "IsArchived" INTEGER NOT NULL,
    "ArchivedAtUtc" TEXT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL, "HeartRateDeviceFamily" TEXT NULL, "HeartRateDeviceKind" TEXT NULL,
    CONSTRAINT "CK_DeviceEnrollments_Archive" CHECK (("IsArchived" = 0 AND "ArchivedAtUtc" IS NULL) OR ("IsArchived" = 1 AND "ArchivedAtUtc" IS NOT NULL)),
    CONSTRAINT "CK_DeviceEnrollments_DeviceId" CHECK (length("DeviceId") > 0),
    CONSTRAINT "CK_DeviceEnrollments_Fingerprint" CHECK (length("IdentityFingerprint") = 64),
    CONSTRAINT "CK_DeviceEnrollments_Protocol" CHECK (length("ProtocolId") > 0),
    CONSTRAINT "CK_DeviceEnrollments_Role" CHECK ("Role" IN ('Treadmill', 'HeartRate')),
    CONSTRAINT "CK_DeviceEnrollments_TreadmillSettings" CHECK (("Role" = 'Treadmill' AND "TelemetryMode" IS NOT NULL AND "CapabilitiesJson" IS NOT NULL) OR ("Role" = 'HeartRate' AND "TelemetryMode" IS NULL AND "CapabilitiesJson" IS NULL)),
    CONSTRAINT "CK_DeviceEnrollments_Version" CHECK ("Version" > 0)
);

CREATE TABLE "GarminAccountLinks" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_GarminAccountLinks" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "ProviderSubject" TEXT NOT NULL,
    "AccountLabel" TEXT NOT NULL,
    "ProtectedAccessToken" TEXT NOT NULL,
    "ProtectedRefreshToken" TEXT NULL,
    "AccessTokenExpiresAtUtc" TEXT NULL,
    "Scopes" TEXT NOT NULL,
    "ConnectedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    "LastSyncAttemptAtUtc" TEXT NULL,
    "LastSyncSuccessAtUtc" TEXT NULL,
    "LastSyncError" TEXT NULL,
    "Version" INTEGER NOT NULL,
    CONSTRAINT "CK_GarminAccountLinks_AccessToken" CHECK (length("ProtectedAccessToken") > 0),
    CONSTRAINT "CK_GarminAccountLinks_Label" CHECK (length("AccountLabel") > 0),
    CONSTRAINT "CK_GarminAccountLinks_Subject" CHECK (length("ProviderSubject") > 0),
    CONSTRAINT "CK_GarminAccountLinks_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_GarminAccountLinks_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "GarminActivityUploadAccounts" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_GarminActivityUploadAccounts" PRIMARY KEY,
    "AccountLabel" TEXT NOT NULL,
    "ConnectedAtUtc" TEXT NOT NULL,
    "Enabled" INTEGER NOT NULL,
    "LastError" TEXT NULL,
    "LastUploadSuccessAtUtc" TEXT NULL,
    "ProtectedTokenStore" TEXT NOT NULL,
    "State" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    "UploadFromUtc" TEXT NULL,
    "UserProfileId" TEXT NOT NULL,
    "Version" INTEGER NOT NULL,
    "WatchActivityHandling" TEXT NOT NULL,
    CONSTRAINT "CK_GarminActivityUploadAccounts_Label" CHECK (length("AccountLabel") > 0),
    CONSTRAINT "CK_GarminActivityUploadAccounts_State" CHECK ("State" IN ('Connected', 'NeedsAuthentication', 'ProviderUnavailable')),
    CONSTRAINT "CK_GarminActivityUploadAccounts_Tokens" CHECK (length("ProtectedTokenStore") > 0),
    CONSTRAINT "CK_GarminActivityUploadAccounts_Version" CHECK ("Version" > 0),
    CONSTRAINT "CK_GarminActivityUploadAccounts_WatchHandling" CHECK ("WatchActivityHandling" IN ('PreferWatch', 'MergeAndReplace')),
    CONSTRAINT "FK_GarminActivityUploadAccounts_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "GarminActivityUploadJobs" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_GarminActivityUploadJobs" PRIMARY KEY,
    "AcknowledgedAtUtc" TEXT NULL,
    "AttemptCount" INTEGER NOT NULL,
    "AvailableAtUtc" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "FailureKind" TEXT NULL,
    "GarminActivityUploadAccountId" TEXT NOT NULL,
    "IdempotencyKey" TEXT NOT NULL,
    "LastError" TEXT NULL,
    "LeaseExpiresAtUtc" TEXT NULL,
    "MatchEvidence" TEXT NULL,
    "MatchedRemoteId" TEXT NULL,
    "OperationPhase" TEXT NOT NULL,
    "RemoteId" TEXT NULL,
    "ReplacementRemoteId" TEXT NULL,
    "Status" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    "UserProfileId" TEXT NOT NULL,
    "WorkoutSessionId" TEXT NOT NULL,
    CONSTRAINT "CK_GarminActivityUploadJobs_Attempts" CHECK ("AttemptCount" >= 0 AND "AttemptCount" <= 3),
    CONSTRAINT "CK_GarminActivityUploadJobs_Key" CHECK (length("IdempotencyKey") = 64),
    CONSTRAINT "CK_GarminActivityUploadJobs_Status" CHECK ("Status" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed', 'FoundInGarmin', 'ReviewRequired')),
    CONSTRAINT "FK_GarminActivityUploadJobs_GarminActivityUploadAccounts_GarminActivityUploadAccountId" FOREIGN KEY ("GarminActivityUploadAccountId") REFERENCES "GarminActivityUploadAccounts" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_GarminActivityUploadJobs_WorkoutSessions_WorkoutSessionId" FOREIGN KEY ("WorkoutSessionId") REFERENCES "WorkoutSessions" ("Id") ON DELETE CASCADE
);

CREATE TABLE "GarminOAuthStates" (
    "StateHash" TEXT NOT NULL CONSTRAINT "PK_GarminOAuthStates" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "ProtectedCodeVerifier" TEXT NOT NULL,
    "RedirectUri" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "ExpiresAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_GarminOAuthStates_Expiry" CHECK ("ExpiresAtUtc" > "CreatedAtUtc"),
    CONSTRAINT "CK_GarminOAuthStates_Hash" CHECK (length("StateHash") = 64),
    CONSTRAINT "CK_GarminOAuthStates_Verifier" CHECK (length("ProtectedCodeVerifier") > 0),
    CONSTRAINT "FK_GarminOAuthStates_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "GarminSyncItems" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_GarminSyncItems" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "GarminAccountLinkId" TEXT NOT NULL,
    "Kind" TEXT NOT NULL,
    "SourceId" TEXT NOT NULL,
    "SourceVersion" TEXT NOT NULL,
    "IdempotencyKey" TEXT NOT NULL,
    "PayloadJson" TEXT NOT NULL,
    "Status" TEXT NOT NULL,
    "AttemptCount" INTEGER NOT NULL,
    "AvailableAtUtc" TEXT NOT NULL,
    "LeaseExpiresAtUtc" TEXT NULL,
    "RemoteId" TEXT NULL,
    "LastError" TEXT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_GarminSyncItems_Attempts" CHECK ("AttemptCount" >= 0),
    CONSTRAINT "CK_GarminSyncItems_Key" CHECK (length("IdempotencyKey") = 64),
    CONSTRAINT "CK_GarminSyncItems_Kind" CHECK ("Kind" IN ('Workout', 'TrainingPlan', 'Calendar')),
    CONSTRAINT "CK_GarminSyncItems_Status" CHECK ("Status" IN ('Pending', 'InFlight', 'Synced', 'Failed')),
    CONSTRAINT "FK_GarminSyncItems_GarminAccountLinks_GarminAccountLinkId" FOREIGN KEY ("GarminAccountLinkId") REFERENCES "GarminAccountLinks" ("Id") ON DELETE CASCADE
);

CREATE TABLE "GarminWatchBindings" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_GarminWatchBindings" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "DeviceLabel" TEXT NOT NULL,
    "TokenSha256" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "LastSeenAtUtc" TEXT NULL,
    "Version" INTEGER NOT NULL,
    CONSTRAINT "CK_GarminWatchBindings_Label" CHECK (length("DeviceLabel") > 0),
    CONSTRAINT "CK_GarminWatchBindings_Token" CHECK (length("TokenSha256") = 64),
    CONSTRAINT "CK_GarminWatchBindings_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_GarminWatchBindings_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "HeartRateDeviceAssignments" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_HeartRateDeviceAssignments" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "DeviceEnrollmentId" TEXT NOT NULL,
    "Priority" INTEGER NOT NULL,
    "AutoConnect" INTEGER NOT NULL,
    "IsPreferred" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_HeartRateDeviceAssignments_Priority" CHECK ("Priority" >= 0 AND "Priority" <= 99),
    CONSTRAINT "CK_HeartRateDeviceAssignments_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_HeartRateDeviceAssignments_DeviceEnrollments_DeviceEnrollmentId" FOREIGN KEY ("DeviceEnrollmentId") REFERENCES "DeviceEnrollments" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_HeartRateDeviceAssignments_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "HeartRateZones" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_HeartRateZones" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "Number" INTEGER NOT NULL,
    "Name" TEXT NOT NULL,
    "MinimumBpm" INTEGER NOT NULL,
    "MaximumBpm" INTEGER NOT NULL,
    CONSTRAINT "CK_HeartRateZones_Number" CHECK ("Number" > 0),
    CONSTRAINT "CK_HeartRateZones_Range" CHECK ("MinimumBpm" <= "MaximumBpm"),
    CONSTRAINT "FK_HeartRateZones_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "ImportAudits" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_ImportAudits" PRIMARY KEY,
    "UserProfileId" TEXT NULL,
    "WorkoutId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "OriginalFileName" TEXT NOT NULL,
    "Format" TEXT NOT NULL,
    "SourceSha256" TEXT NOT NULL,
    "WarningSummaryJson" TEXT NOT NULL,
    "ImportedAtUtc" TEXT NOT NULL,
    CONSTRAINT "FK_ImportAudits_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE SET NULL,
    CONSTRAINT "FK_ImportAudits_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_ImportAudits_Workouts_WorkoutId" FOREIGN KEY ("WorkoutId") REFERENCES "Workouts" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "LocalBackupPolicies" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_LocalBackupPolicies" PRIMARY KEY,
    "DestinationPath" TEXT NOT NULL,
    "IntervalHours" INTEGER NOT NULL,
    "RetentionCount" INTEGER NOT NULL,
    "Enabled" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_LocalBackupPolicies_Interval" CHECK ("IntervalHours" >= 1 AND "IntervalHours" <= 168),
    CONSTRAINT "CK_LocalBackupPolicies_Retention" CHECK ("RetentionCount" >= 2 AND "RetentionCount" <= 60),
    CONSTRAINT "CK_LocalBackupPolicies_Version" CHECK ("Version" > 0)
);

CREATE TABLE "LocalGoals" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_LocalGoals" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "Kind" TEXT NOT NULL,
    "Period" TEXT NOT NULL,
    "TargetValue" REAL NOT NULL,
    "Enabled" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_LocalGoals_Kind" CHECK ("Kind" IN ('Sessions', 'Minutes', 'Distance', 'PlanCompletion')),
    CONSTRAINT "CK_LocalGoals_Period" CHECK ("Period" IN ('Weekly', 'Monthly', 'Plan')),
    CONSTRAINT "CK_LocalGoals_Target" CHECK ("TargetValue" > 0),
    CONSTRAINT "CK_LocalGoals_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_LocalGoals_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "OperationReceipts" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_OperationReceipts" PRIMARY KEY,
    "ClientOperationId" TEXT NOT NULL,
    "OperationType" TEXT NOT NULL,
    "RequestFingerprint" TEXT NOT NULL,
    "StatusCode" INTEGER NOT NULL,
    "OutcomeJson" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL
);

CREATE TABLE "PolarH10RecordingSamples" (
    "PolarH10RecordingId" TEXT NOT NULL,
    "Sequence" INTEGER NOT NULL,
    "CapturedAtUtc" TEXT NOT NULL,
    "BeatsPerMinute" INTEGER NULL,
    "RrIntervalMilliseconds" INTEGER NULL,
    CONSTRAINT "PK_PolarH10RecordingSamples" PRIMARY KEY ("PolarH10RecordingId", "Sequence"),
    CONSTRAINT "FK_PolarH10RecordingSamples_PolarH10Recordings_PolarH10RecordingId" FOREIGN KEY ("PolarH10RecordingId") REFERENCES "PolarH10Recordings" ("Id") ON DELETE CASCADE
);

CREATE TABLE "PolarH10Recordings" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_PolarH10Recordings" PRIMARY KEY,
    "WorkoutSessionId" TEXT NULL,
    "UserProfileId" TEXT NULL,
    "DeviceEnrollmentId" TEXT NOT NULL,
    "ExerciseId" TEXT NOT NULL,
    "Status" TEXT NOT NULL,
    "Origin" TEXT NOT NULL,
    "SampleType" TEXT NOT NULL,
    "SampleIntervalSeconds" INTEGER NOT NULL,
    "StartRequestedAtUtc" TEXT NULL,
    "StartConfirmedAtUtc" TEXT NULL,
    "StopRequestedAtUtc" TEXT NULL,
    "StopConfirmedAtUtc" TEXT NULL,
    "ExternalRecordingId" TEXT NULL,
    "RemotePath" TEXT NULL,
    "Payload" BLOB NULL,
    "PayloadSha256" TEXT NULL,
    "PayloadBytes" INTEGER NOT NULL,
    "QueuedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    "StartedAtUtc" TEXT NULL,
    "EndedAtUtc" TEXT NULL,
    "LeaseExpiresAtUtc" TEXT NULL,
    "AttemptCount" INTEGER NOT NULL,
    "LastError" TEXT NULL,
    "MergeCount" INTEGER NOT NULL,
    "RemovalCount" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "AvailableAtUtc" TEXT NOT NULL,
    "OperationFingerprint" TEXT NULL,
    CONSTRAINT "CK_PolarH10Recordings_Attempts" CHECK ("AttemptCount" >= 0),
    CONSTRAINT "CK_PolarH10Recordings_Exercise" CHECK (length("ExerciseId") BETWEEN 1 AND 64),
    CONSTRAINT "CK_PolarH10Recordings_Status" CHECK ("Status" IN ('StartPending','Recording','StopPending','AwaitingDevice','Downloading','Downloaded','ReviewRequired','Merging','Merged','RemovalPending','Completed','Retained','Skipped','NotStarted','DiscardCleanupPending','Retryable')),
    CONSTRAINT "FK_PolarH10Recordings_DeviceEnrollments_DeviceEnrollmentId" FOREIGN KEY ("DeviceEnrollmentId") REFERENCES "DeviceEnrollments" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_PolarH10Recordings_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE SET NULL,
    CONSTRAINT "FK_PolarH10Recordings_WorkoutSessions_WorkoutSessionId" FOREIGN KEY ("WorkoutSessionId") REFERENCES "WorkoutSessions" ("Id") ON DELETE SET NULL
);

CREATE TABLE "PremadePlanInstallations" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_PremadePlanInstallations" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "TemplateId" TEXT NOT NULL,
    "TemplateVersion" TEXT NOT NULL,
    "TemplateContentSha256" TEXT NOT NULL,
    "CopyNumber" INTEGER NOT NULL,
    "WorkoutProgramId" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_PremadePlanInstallations_CopyNumber" CHECK ("CopyNumber" > 0),
    CONSTRAINT "CK_PremadePlanInstallations_Hash" CHECK (length("TemplateContentSha256") = 64),
    CONSTRAINT "CK_PremadePlanInstallations_TemplateId" CHECK (length("TemplateId") > 0),
    CONSTRAINT "CK_PremadePlanInstallations_TemplateVersion" CHECK (length("TemplateVersion") > 0),
    CONSTRAINT "FK_PremadePlanInstallations_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_PremadePlanInstallations_WorkoutPrograms_WorkoutProgramId" FOREIGN KEY ("WorkoutProgramId") REFERENCES "WorkoutPrograms" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "ProgressionRecommendations" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_ProgressionRecommendations" PRIMARY KEY,
    "OperationId" TEXT NOT NULL,
    "UserProfileId" TEXT NOT NULL,
    "WorkoutSessionId" TEXT NOT NULL,
    "Action" TEXT NOT NULL,
    "Reason" TEXT NOT NULL,
    "AlgorithmVersion" TEXT NOT NULL,
    "EvidenceJson" TEXT NOT NULL,
    "Status" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "DecidedAtUtc" TEXT NULL,
    "Version" INTEGER NOT NULL,
    CONSTRAINT "CK_ProgressionRecommendations_Action" CHECK ("Action" IN ('Maintain', 'Repeat', 'Reduce', 'Advance', 'Reschedule')),
    CONSTRAINT "CK_ProgressionRecommendations_Decision" CHECK (("Status" = 'Pending' AND "DecidedAtUtc" IS NULL) OR ("Status" <> 'Pending' AND "DecidedAtUtc" IS NOT NULL)),
    CONSTRAINT "CK_ProgressionRecommendations_Reason" CHECK (length("Reason") > 0),
    CONSTRAINT "CK_ProgressionRecommendations_Status" CHECK ("Status" IN ('Pending', 'Accepted', 'Rejected')),
    CONSTRAINT "CK_ProgressionRecommendations_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_ProgressionRecommendations_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_ProgressionRecommendations_WorkoutSessions_WorkoutSessionId" FOREIGN KEY ("WorkoutSessionId") REFERENCES "WorkoutSessions" ("Id") ON DELETE CASCADE
);

CREATE TABLE "RunnerExperiencePreferences" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_RunnerExperiencePreferences" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "DisplayStyle" TEXT NOT NULL,
    "PrimaryMetricsJson" TEXT NOT NULL,
    "CueStepChanges" INTEGER NOT NULL,
    "CueHeartRateDeparture" INTEGER NOT NULL,
    "CueHalfway" INTEGER NOT NULL,
    "CueConnectionProblems" INTEGER NOT NULL,
    "CueCompletion" INTEGER NOT NULL,
    "CueVolumePercent" INTEGER NOT NULL,
    "Version" INTEGER NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_RunnerExperiencePreferences_Style" CHECK ("DisplayStyle" IN ('Balanced', 'LargeText', 'HighContrast')),
    CONSTRAINT "CK_RunnerExperiencePreferences_Version" CHECK ("Version" > 0),
    CONSTRAINT "CK_RunnerExperiencePreferences_Volume" CHECK ("CueVolumePercent" >= 0 AND "CueVolumePercent" <= 100),
    CONSTRAINT "FK_RunnerExperiencePreferences_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE
);

CREATE TABLE "SessionEvents" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_SessionEvents" PRIMARY KEY,
    "WorkoutSessionId" TEXT NOT NULL,
    "OccurredAtUtc" TEXT NOT NULL,
    "Kind" TEXT NOT NULL,
    "DetailsJson" TEXT NOT NULL,
    CONSTRAINT "CK_SessionEvents_Kind" CHECK (length("Kind") > 0),
    CONSTRAINT "FK_SessionEvents_WorkoutSessions_WorkoutSessionId" FOREIGN KEY ("WorkoutSessionId") REFERENCES "WorkoutSessions" ("Id") ON DELETE CASCADE
);

CREATE TABLE "SessionSamples" (
    "WorkoutSessionId" TEXT NOT NULL,
    "Sequence" INTEGER NOT NULL,
    "CapturedAtUtc" TEXT NOT NULL,
    "ElapsedMilliseconds" REAL NOT NULL,
    "PlannedSpeedKph" REAL NULL,
    "RequestedSpeedKph" REAL NOT NULL,
    "MeasuredSpeedKph" REAL NOT NULL,
    "PlannedInclinePercent" REAL NULL,
    "RequestedInclinePercent" REAL NOT NULL,
    "MeasuredInclinePercent" REAL NOT NULL,
    "HeartRateBpm" INTEGER NULL,
    "DistanceKilometers" REAL NOT NULL,
    "EstimatedCalories" REAL NOT NULL,
    "TelemetryAgeMilliseconds" REAL NOT NULL,
    "MetricAlgorithmVersion" TEXT NOT NULL,
    CONSTRAINT "PK_SessionSamples" PRIMARY KEY ("WorkoutSessionId", "Sequence"),
    CONSTRAINT "CK_SessionSamples_Calories" CHECK ("EstimatedCalories" >= 0),
    CONSTRAINT "CK_SessionSamples_Distance" CHECK ("DistanceKilometers" >= 0),
    CONSTRAINT "CK_SessionSamples_Elapsed" CHECK ("ElapsedMilliseconds" >= 0),
    CONSTRAINT "CK_SessionSamples_Sequence" CHECK ("Sequence" >= 0),
    CONSTRAINT "CK_SessionSamples_Speeds" CHECK (("PlannedSpeedKph" IS NULL OR "PlannedSpeedKph" >= 0) AND "RequestedSpeedKph" >= 0 AND "MeasuredSpeedKph" >= 0),
    CONSTRAINT "CK_SessionSamples_TelemetryAge" CHECK ("TelemetryAgeMilliseconds" >= 0),
    CONSTRAINT "FK_SessionSamples_WorkoutSessions_WorkoutSessionId" FOREIGN KEY ("WorkoutSessionId") REFERENCES "WorkoutSessions" ("Id") ON DELETE CASCADE
);

CREATE TABLE "TrainingDaySelections" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_TrainingDaySelections" PRIMARY KEY,
    "UserProfileId" TEXT NOT NULL,
    "LocalDate" TEXT NOT NULL,
    "CalendarSeriesId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "SelectedAtUtc" TEXT NOT NULL,
    CONSTRAINT "FK_TrainingDaySelections_CalendarSeries_CalendarSeriesId" FOREIGN KEY ("CalendarSeriesId") REFERENCES "CalendarSeries" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_TrainingDaySelections_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_TrainingDaySelections_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "TreadmillMaintenanceEvents" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_TreadmillMaintenanceEvents" PRIMARY KEY,
    "TreadmillMaintenancePolicyId" TEXT NOT NULL,
    "OperationId" TEXT NOT NULL,
    "PerformedAtUtc" TEXT NOT NULL,
    "AppDistanceBaselineKilometers" REAL NOT NULL,
    "Note" TEXT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_TreadmillMaintenanceEvents_Distance" CHECK ("AppDistanceBaselineKilometers" >= 0),
    CONSTRAINT "CK_TreadmillMaintenanceEvents_Note" CHECK ("Note" IS NULL OR length("Note") <= 500),
    CONSTRAINT "FK_TreadmillMaintenanceEvents_TreadmillMaintenancePolicies_TreadmillMaintenancePolicyId" FOREIGN KEY ("TreadmillMaintenancePolicyId") REFERENCES "TreadmillMaintenancePolicies" ("Id") ON DELETE CASCADE
);

CREATE TABLE "TreadmillMaintenancePolicies" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_TreadmillMaintenancePolicies" PRIMARY KEY,
    "DeviceEnrollmentId" TEXT NOT NULL,
    "IntervalMonths" INTEGER NOT NULL,
    "DistanceIntervalKilometers" REAL NOT NULL,
    "Version" INTEGER NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_TreadmillMaintenancePolicies_Distance" CHECK ("DistanceIntervalKilometers" >= 1 AND "DistanceIntervalKilometers" <= 5000),
    CONSTRAINT "CK_TreadmillMaintenancePolicies_Months" CHECK ("IntervalMonths" >= 1 AND "IntervalMonths" <= 24),
    CONSTRAINT "CK_TreadmillMaintenancePolicies_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_TreadmillMaintenancePolicies_DeviceEnrollments_DeviceEnrollmentId" FOREIGN KEY ("DeviceEnrollmentId") REFERENCES "DeviceEnrollments" ("Id") ON DELETE CASCADE
);

CREATE TABLE "UserProfiles" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_UserProfiles" PRIMARY KEY,
    "ArchivedAtUtc" TEXT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "DisplayName" TEXT NOT NULL,
    "HeartRateDecreaseCooldownSeconds" INTEGER NOT NULL,
    "HeartRateDecreaseStepKph" REAL NOT NULL,
    "HeartRateIncreaseCooldownSeconds" INTEGER NOT NULL,
    "HeartRateIncreaseStepKph" REAL NOT NULL,
    "IsArchived" INTEGER NOT NULL,
    "MaximumHeartRateBpm" INTEGER NULL,
    "MaximumSpeedKph" REAL NULL,
    "NormalizedDisplayName" TEXT NOT NULL,
    "UnitSystem" TEXT NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    "Version" INTEGER NOT NULL,
    "WeightKilograms" REAL NOT NULL,
    CONSTRAINT "CK_UserProfiles_Archive" CHECK (("IsArchived" = 0 AND "ArchivedAtUtc" IS NULL) OR ("IsArchived" = 1 AND "ArchivedAtUtc" IS NOT NULL)),
    CONSTRAINT "CK_UserProfiles_DisplayName" CHECK (length("DisplayName") > 0),
    CONSTRAINT "CK_UserProfiles_HrDecreaseCooldown" CHECK ("HeartRateDecreaseCooldownSeconds" >= 5 AND "HeartRateDecreaseCooldownSeconds" <= 120),
    CONSTRAINT "CK_UserProfiles_HrDecreaseStep" CHECK ("HeartRateDecreaseStepKph" >= 0.1 AND "HeartRateDecreaseStepKph" <= 1.0),
    CONSTRAINT "CK_UserProfiles_HrIncreaseCooldown" CHECK ("HeartRateIncreaseCooldownSeconds" >= 15 AND "HeartRateIncreaseCooldownSeconds" <= 180),
    CONSTRAINT "CK_UserProfiles_HrIncreaseStep" CHECK ("HeartRateIncreaseStepKph" >= 0.1 AND "HeartRateIncreaseStepKph" <= 0.5),
    CONSTRAINT "CK_UserProfiles_MaximumHeartRate" CHECK ("MaximumHeartRateBpm" IS NULL OR "MaximumHeartRateBpm" > 0),
    CONSTRAINT "CK_UserProfiles_MaximumSpeed" CHECK ("MaximumSpeedKph" IS NULL OR "MaximumSpeedKph" > 0),
    CONSTRAINT "CK_UserProfiles_UnitSystem" CHECK ("UnitSystem" = 'Metric'),
    CONSTRAINT "CK_UserProfiles_Version" CHECK ("Version" > 0),
    CONSTRAINT "CK_UserProfiles_Weight" CHECK ("WeightKilograms" > 0)
);

CREATE TABLE "WorkoutProgramExtraOccurrences" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramExtraOccurrences" PRIMARY KEY,
    "WorkoutProgramRunId" TEXT NOT NULL,
    "WorkoutProgramItemId" TEXT NOT NULL,
    "Date" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "FK_WorkoutProgramExtraOccurrences_WorkoutProgramItems_WorkoutProgramItemId" FOREIGN KEY ("WorkoutProgramItemId") REFERENCES "WorkoutProgramItems" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutProgramExtraOccurrences_WorkoutProgramRuns_WorkoutProgramRunId" FOREIGN KEY ("WorkoutProgramRunId") REFERENCES "WorkoutProgramRuns" ("Id") ON DELETE CASCADE
);

CREATE TABLE "WorkoutProgramItemAlternatives" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramItemAlternatives" PRIMARY KEY,
    "WorkoutProgramItemId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "DisplayOrder" INTEGER NOT NULL,
    "Variant" TEXT NOT NULL,
    CONSTRAINT "CK_WorkoutProgramItemAlternatives_DisplayOrder" CHECK ("DisplayOrder" > 0),
    CONSTRAINT "FK_WorkoutProgramItemAlternatives_WorkoutProgramItems_WorkoutProgramItemId" FOREIGN KEY ("WorkoutProgramItemId") REFERENCES "WorkoutProgramItems" ("Id") ON DELETE CASCADE,
    CONSTRAINT "FK_WorkoutProgramItemAlternatives_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "WorkoutProgramItems" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramItems" PRIMARY KEY,
    "WorkoutProgramRevisionId" TEXT NOT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "Position" INTEGER NOT NULL, "Phase" TEXT NULL, "SessionNumber" INTEGER NULL, "WeekNumber" INTEGER NULL,
    CONSTRAINT "CK_WorkoutProgramItems_Position" CHECK ("Position" > 0),
    CONSTRAINT "FK_WorkoutProgramItems_WorkoutProgramRevisions_WorkoutProgramRevisionId" FOREIGN KEY ("WorkoutProgramRevisionId") REFERENCES "WorkoutProgramRevisions" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutProgramItems_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "WorkoutProgramRevisions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramRevisions" PRIMARY KEY,
    "Category" TEXT NOT NULL,
    "ContentSha256" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    "Description" TEXT NULL,
    "Name" TEXT NOT NULL,
    "OwnerProfileId" TEXT NULL,
    "RevisionNumber" INTEGER NOT NULL,
    "TemplateId" TEXT NULL,
    "TemplateVersion" TEXT NULL,
    "WorkoutProgramId" TEXT NOT NULL,
    CONSTRAINT "CK_WorkoutProgramRevisions_Category" CHECK (length("Category") > 0),
    CONSTRAINT "CK_WorkoutProgramRevisions_Hash" CHECK (length("ContentSha256") = 64),
    CONSTRAINT "CK_WorkoutProgramRevisions_Name" CHECK (length("Name") > 0),
    CONSTRAINT "CK_WorkoutProgramRevisions_Number" CHECK ("RevisionNumber" > 0),
    CONSTRAINT "FK_WorkoutProgramRevisions_UserProfiles_OwnerProfileId" FOREIGN KEY ("OwnerProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutProgramRevisions_WorkoutPrograms_WorkoutProgramId" FOREIGN KEY ("WorkoutProgramId") REFERENCES "WorkoutPrograms" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "WorkoutProgramRuns" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramRuns" PRIMARY KEY,
    "EndedAtUtc" TEXT NULL,
    "ScheduleTimeZoneId" TEXT NULL,
    "ScheduledStartDate" TEXT NULL,
    "ScheduledWeekdayMask" INTEGER NOT NULL,
    "StartedAtUtc" TEXT NOT NULL,
    "Status" TEXT NOT NULL,
    "UserProfileId" TEXT NOT NULL,
    "Version" INTEGER NOT NULL,
    "WorkoutProgramRevisionId" TEXT NOT NULL,
    CONSTRAINT "CK_WorkoutProgramRuns_Schedule" CHECK (("ScheduledStartDate" IS NULL AND "ScheduledWeekdayMask" = 0 AND "ScheduleTimeZoneId" IS NULL) OR ("ScheduledStartDate" IS NOT NULL AND "ScheduledWeekdayMask" BETWEEN 1 AND 127 AND length("ScheduleTimeZoneId") > 0)),
    CONSTRAINT "CK_WorkoutProgramRuns_Status" CHECK ("Status" IN ('Active', 'Completed', 'Abandoned')),
    CONSTRAINT "CK_WorkoutProgramRuns_Version" CHECK ("Version" > 0),
    CONSTRAINT "FK_WorkoutProgramRuns_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutProgramRuns_WorkoutProgramRevisions_WorkoutProgramRevisionId" FOREIGN KEY ("WorkoutProgramRevisionId") REFERENCES "WorkoutProgramRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "WorkoutProgramScheduleOverrides" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutProgramScheduleOverrides" PRIMARY KEY,
    "WorkoutProgramRunId" TEXT NOT NULL,
    "WorkoutProgramItemId" TEXT NOT NULL,
    "TargetDate" TEXT NULL,
    "IsSkipped" INTEGER NOT NULL,
    "UpdatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_WorkoutProgramScheduleOverrides_Value" CHECK (("IsSkipped" = 1 AND "TargetDate" IS NULL) OR ("IsSkipped" = 0 AND "TargetDate" IS NOT NULL)),
    CONSTRAINT "FK_WorkoutProgramScheduleOverrides_WorkoutProgramItems_WorkoutProgramItemId" FOREIGN KEY ("WorkoutProgramItemId") REFERENCES "WorkoutProgramItems" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutProgramScheduleOverrides_WorkoutProgramRuns_WorkoutProgramRunId" FOREIGN KEY ("WorkoutProgramRunId") REFERENCES "WorkoutProgramRuns" ("Id") ON DELETE CASCADE
);

CREATE TABLE "WorkoutPrograms" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutPrograms" PRIMARY KEY,
    "IsArchived" INTEGER NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL
);

CREATE TABLE "WorkoutRevisions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutRevisions" PRIMARY KEY,
    "WorkoutId" TEXT NOT NULL,
    "RevisionNumber" INTEGER NOT NULL,
    "DefinitionJson" TEXT NOT NULL,
    "ContentSha256" TEXT NOT NULL,
    "CreatedAtUtc" TEXT NOT NULL,
    CONSTRAINT "CK_WorkoutRevisions_Hash" CHECK (length("ContentSha256") = 64),
    CONSTRAINT "CK_WorkoutRevisions_Json" CHECK (length("DefinitionJson") > 0),
    CONSTRAINT "CK_WorkoutRevisions_Number" CHECK ("RevisionNumber" > 0),
    CONSTRAINT "FK_WorkoutRevisions_Workouts_WorkoutId" FOREIGN KEY ("WorkoutId") REFERENCES "Workouts" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "WorkoutSessions" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_WorkoutSessions" PRIMARY KEY,
    "ArmedAtUtc" TEXT NOT NULL,
    "AverageHeartRateBpm" REAL NULL,
    "AverageInclinePercent" REAL NOT NULL,
    "AverageSpeedKph" REAL NOT NULL,
    "ControllerConfigurationJson" TEXT NOT NULL,
    "DebriefNote" TEXT NULL,
    "DebriefUpdatedAtUtc" TEXT NULL,
    "DistanceKilometers" REAL NOT NULL,
    "DurationSeconds" REAL NOT NULL,
    "EndedAtUtc" TEXT NULL,
    "EstimatedCalories" REAL NOT NULL,
    "MaximumHeartRateBpm" INTEGER NULL,
    "MetricAlgorithmVersion" TEXT NOT NULL,
    "PerceivedExertion" INTEGER NULL,
    "SelectionSource" TEXT NOT NULL,
    "SessionOrigin" TEXT NOT NULL,
    "StartedAtUtc" TEXT NULL,
    "State" TEXT NOT NULL,
    "UserProfileId" TEXT NOT NULL,
    "UserProfileName" TEXT NOT NULL,
    "WorkoutProgramItemId" TEXT NULL,
    "WorkoutProgramRunId" TEXT NULL,
    "WorkoutRevisionId" TEXT NOT NULL,
    "WorkoutTitle" TEXT NOT NULL, "RecoveryCheckpointJson" TEXT NULL, "RecoveryCheckpointUpdatedAtUtc" TEXT NULL, "ActiveSessionKey" AS (CASE WHEN "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume') THEN 1 ELSE NULL END), "RecordPolarH10Memory" INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT "CK_WorkoutSessions_Calories" CHECK ("EstimatedCalories" >= 0),
    CONSTRAINT "CK_WorkoutSessions_Distance" CHECK ("DistanceKilometers" >= 0),
    CONSTRAINT "CK_WorkoutSessions_Duration" CHECK ("DurationSeconds" >= 0),
    CONSTRAINT "CK_WorkoutSessions_Origin" CHECK ("SessionOrigin" IN ('Legacy', 'Hardware', 'Simulator', 'SystemTest')),
    CONSTRAINT "CK_WorkoutSessions_Rpe" CHECK ("PerceivedExertion" IS NULL OR ("PerceivedExertion" >= 1 AND "PerceivedExertion" <= 10)),
    CONSTRAINT "CK_WorkoutSessions_State" CHECK (length("State") > 0),
    CONSTRAINT "FK_WorkoutSessions_UserProfiles_UserProfileId" FOREIGN KEY ("UserProfileId") REFERENCES "UserProfiles" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutSessions_WorkoutProgramItems_WorkoutProgramItemId" FOREIGN KEY ("WorkoutProgramItemId") REFERENCES "WorkoutProgramItems" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutSessions_WorkoutProgramRuns_WorkoutProgramRunId" FOREIGN KEY ("WorkoutProgramRunId") REFERENCES "WorkoutProgramRuns" ("Id") ON DELETE RESTRICT,
    CONSTRAINT "FK_WorkoutSessions_WorkoutRevisions_WorkoutRevisionId" FOREIGN KEY ("WorkoutRevisionId") REFERENCES "WorkoutRevisions" ("Id") ON DELETE RESTRICT
);

CREATE TABLE "Workouts" (
    "Id" TEXT NOT NULL CONSTRAINT "PK_Workouts" PRIMARY KEY,
    "CreatedAtUtc" TEXT NOT NULL,
    "IsArchived" INTEGER NOT NULL,
    "Kind" TEXT NOT NULL,
    "Name" TEXT NOT NULL,
    CONSTRAINT "CK_Workouts_Kind" CHECK ("Kind" IN ('Structured', 'ManualTemplate', 'PlanInternal')),
    CONSTRAINT "CK_Workouts_Name" CHECK (length("Name") > 0)
);

CREATE TABLE "__EFMigrationsHistory" (
    "MigrationId" TEXT NOT NULL CONSTRAINT "PK___EFMigrationsHistory" PRIMARY KEY,
    "ProductVersion" TEXT NOT NULL
);

CREATE TABLE "__EFMigrationsLock" (
    "Id" INTEGER NOT NULL CONSTRAINT "PK___EFMigrationsLock" PRIMARY KEY,
    "Timestamp" TEXT NOT NULL
);

CREATE INDEX "IX_BackupVerifications_LocalBackupPolicyId_CompletedAtUtc" ON "BackupVerifications" ("LocalBackupPolicyId", "CompletedAtUtc");

CREATE INDEX "IX_BleReliabilityIncidents_DeviceEnrollmentId_RecoveredAtUnixMilliseconds" ON "BleReliabilityIncidents" ("DeviceEnrollmentId", "RecoveredAtUnixMilliseconds");

CREATE INDEX "IX_BleReliabilityIncidents_StartedAtUnixMilliseconds" ON "BleReliabilityIncidents" ("StartedAtUnixMilliseconds");

CREATE UNIQUE INDEX "UX_BleReliabilityIncidents_OneOpenPerDevice" ON "BleReliabilityIncidents" ("DeviceEnrollmentId") WHERE "RecoveredAtUnixMilliseconds" IS NULL;

CREATE UNIQUE INDEX "IX_CalendarExceptionOptions_CalendarExceptionId_DisplayOrder" ON "CalendarExceptionOptions" ("CalendarExceptionId", "DisplayOrder");

CREATE INDEX "IX_CalendarExceptionOptions_WorkoutRevisionId" ON "CalendarExceptionOptions" ("WorkoutRevisionId");

CREATE UNIQUE INDEX "IX_CalendarExceptions_CalendarSeriesId_LocalDate" ON "CalendarExceptions" ("CalendarSeriesId", "LocalDate");

CREATE INDEX "IX_CalendarSeries_UserProfileId_ScheduleGroupId" ON "CalendarSeries" ("UserProfileId", "ScheduleGroupId");

CREATE UNIQUE INDEX "IX_CalendarSeriesOptions_CalendarSeriesId_DisplayOrder" ON "CalendarSeriesOptions" ("CalendarSeriesId", "DisplayOrder");

CREATE INDEX "IX_CalendarSeriesOptions_WorkoutRevisionId" ON "CalendarSeriesOptions" ("WorkoutRevisionId");

CREATE INDEX "IX_DeviceEnrollments_IdentityFingerprint" ON "DeviceEnrollments" ("IdentityFingerprint");

CREATE UNIQUE INDEX "IX_DeviceEnrollments_Role" ON "DeviceEnrollments" ("Role") WHERE "Role" = 'Treadmill' AND "IsArchived" = 0;

CREATE UNIQUE INDEX "IX_DeviceEnrollments_Role_DeviceId" ON "DeviceEnrollments" ("Role", "DeviceId") WHERE "IsArchived" = 0;

CREATE UNIQUE INDEX "IX_GarminAccountLinks_ProviderSubject" ON "GarminAccountLinks" ("ProviderSubject");

CREATE UNIQUE INDEX "IX_GarminAccountLinks_UserProfileId" ON "GarminAccountLinks" ("UserProfileId");

CREATE UNIQUE INDEX "IX_GarminActivityUploadAccounts_UserProfileId" ON "GarminActivityUploadAccounts" ("UserProfileId");

CREATE INDEX "IX_GarminActivityUploadJobs_GarminActivityUploadAccountId" ON "GarminActivityUploadJobs" ("GarminActivityUploadAccountId");

CREATE UNIQUE INDEX "IX_GarminActivityUploadJobs_IdempotencyKey" ON "GarminActivityUploadJobs" ("IdempotencyKey");

CREATE INDEX "IX_GarminActivityUploadJobs_Status_AvailableAtUtc" ON "GarminActivityUploadJobs" ("Status", "AvailableAtUtc");

CREATE INDEX "IX_GarminActivityUploadJobs_Status_LeaseExpiresAtUtc" ON "GarminActivityUploadJobs" ("Status", "LeaseExpiresAtUtc");

CREATE UNIQUE INDEX "IX_GarminActivityUploadJobs_WorkoutSessionId" ON "GarminActivityUploadJobs" ("WorkoutSessionId");

CREATE INDEX "IX_GarminOAuthStates_ExpiresAtUtc" ON "GarminOAuthStates" ("ExpiresAtUtc");

CREATE INDEX "IX_GarminOAuthStates_UserProfileId" ON "GarminOAuthStates" ("UserProfileId");

CREATE INDEX "IX_GarminSyncItems_GarminAccountLinkId" ON "GarminSyncItems" ("GarminAccountLinkId");

CREATE UNIQUE INDEX "IX_GarminSyncItems_IdempotencyKey" ON "GarminSyncItems" ("IdempotencyKey");

CREATE INDEX "IX_GarminSyncItems_Status_AvailableAtUtc" ON "GarminSyncItems" ("Status", "AvailableAtUtc");

CREATE INDEX "IX_GarminSyncItems_Status_LeaseExpiresAtUtc" ON "GarminSyncItems" ("Status", "LeaseExpiresAtUtc");

CREATE INDEX "IX_GarminSyncItems_UserProfileId_Kind_SourceId" ON "GarminSyncItems" ("UserProfileId", "Kind", "SourceId");

CREATE UNIQUE INDEX "IX_GarminWatchBindings_TokenSha256" ON "GarminWatchBindings" ("TokenSha256");

CREATE UNIQUE INDEX "IX_GarminWatchBindings_UserProfileId" ON "GarminWatchBindings" ("UserProfileId");

CREATE INDEX "IX_HeartRateDeviceAssignments_DeviceEnrollmentId" ON "HeartRateDeviceAssignments" ("DeviceEnrollmentId");

CREATE UNIQUE INDEX "IX_HeartRateDeviceAssignments_UserProfileId" ON "HeartRateDeviceAssignments" ("UserProfileId") WHERE "IsPreferred" = 1;

CREATE UNIQUE INDEX "IX_HeartRateDeviceAssignments_UserProfileId_DeviceEnrollmentId" ON "HeartRateDeviceAssignments" ("UserProfileId", "DeviceEnrollmentId");

CREATE UNIQUE INDEX "IX_HeartRateZones_UserProfileId_Number" ON "HeartRateZones" ("UserProfileId", "Number");

CREATE INDEX "IX_ImportAudits_Format_SourceSha256" ON "ImportAudits" ("Format", "SourceSha256");

CREATE INDEX "IX_ImportAudits_UserProfileId" ON "ImportAudits" ("UserProfileId");

CREATE INDEX "IX_ImportAudits_WorkoutId" ON "ImportAudits" ("WorkoutId");

CREATE INDEX "IX_ImportAudits_WorkoutRevisionId" ON "ImportAudits" ("WorkoutRevisionId");

CREATE UNIQUE INDEX "IX_LocalGoals_UserProfileId_Kind_Period" ON "LocalGoals" ("UserProfileId", "Kind", "Period");

CREATE UNIQUE INDEX "IX_OperationReceipts_ClientOperationId" ON "OperationReceipts" ("ClientOperationId");

CREATE INDEX "IX_OperationReceipts_CreatedAtUtc" ON "OperationReceipts" ("CreatedAtUtc");

CREATE INDEX "IX_PolarH10RecordingSamples_PolarH10RecordingId_CapturedAtUtc" ON "PolarH10RecordingSamples" ("PolarH10RecordingId", "CapturedAtUtc");

CREATE UNIQUE INDEX "IX_PolarH10Recordings_DeviceEnrollmentId_ExerciseId" ON "PolarH10Recordings" ("DeviceEnrollmentId", "ExerciseId");

CREATE UNIQUE INDEX "IX_PolarH10Recordings_DeviceEnrollmentId_RemotePath" ON "PolarH10Recordings" ("DeviceEnrollmentId", "RemotePath") WHERE "RemotePath" IS NOT NULL;

CREATE INDEX "IX_PolarH10Recordings_Status_LeaseExpiresAtUtc" ON "PolarH10Recordings" ("Status", "LeaseExpiresAtUtc");

CREATE INDEX "IX_PolarH10Recordings_UserProfileId" ON "PolarH10Recordings" ("UserProfileId");

CREATE UNIQUE INDEX "IX_PolarH10Recordings_WorkoutSessionId" ON "PolarH10Recordings" ("WorkoutSessionId") WHERE "WorkoutSessionId" IS NOT NULL;

CREATE UNIQUE INDEX "IX_PremadePlanInstallations_UserProfileId_TemplateId_TemplateVersion_CopyNumber" ON "PremadePlanInstallations" ("UserProfileId", "TemplateId", "TemplateVersion", "CopyNumber");

CREATE UNIQUE INDEX "IX_PremadePlanInstallations_WorkoutProgramId" ON "PremadePlanInstallations" ("WorkoutProgramId");

CREATE UNIQUE INDEX "IX_ProgressionRecommendations_OperationId" ON "ProgressionRecommendations" ("OperationId");

CREATE UNIQUE INDEX "IX_ProgressionRecommendations_UserProfileId_WorkoutSessionId" ON "ProgressionRecommendations" ("UserProfileId", "WorkoutSessionId");

CREATE INDEX "IX_ProgressionRecommendations_WorkoutSessionId" ON "ProgressionRecommendations" ("WorkoutSessionId");

CREATE UNIQUE INDEX "IX_RunnerExperiencePreferences_UserProfileId" ON "RunnerExperiencePreferences" ("UserProfileId");

CREATE INDEX "IX_SessionEvents_WorkoutSessionId_OccurredAtUtc" ON "SessionEvents" ("WorkoutSessionId", "OccurredAtUtc");

CREATE INDEX "IX_SessionSamples_WorkoutSessionId_CapturedAtUtc" ON "SessionSamples" ("WorkoutSessionId", "CapturedAtUtc");

CREATE INDEX "IX_TrainingDaySelections_CalendarSeriesId" ON "TrainingDaySelections" ("CalendarSeriesId");

CREATE UNIQUE INDEX "IX_TrainingDaySelections_UserProfileId_LocalDate" ON "TrainingDaySelections" ("UserProfileId", "LocalDate");

CREATE INDEX "IX_TrainingDaySelections_WorkoutRevisionId" ON "TrainingDaySelections" ("WorkoutRevisionId");

CREATE UNIQUE INDEX "IX_TreadmillMaintenanceEvents_OperationId" ON "TreadmillMaintenanceEvents" ("OperationId");

CREATE INDEX "IX_TreadmillMaintenanceEvents_TreadmillMaintenancePolicyId_PerformedAtUtc" ON "TreadmillMaintenanceEvents" ("TreadmillMaintenancePolicyId", "PerformedAtUtc");

CREATE UNIQUE INDEX "IX_TreadmillMaintenancePolicies_DeviceEnrollmentId" ON "TreadmillMaintenancePolicies" ("DeviceEnrollmentId");

CREATE UNIQUE INDEX "IX_UserProfiles_NormalizedDisplayName" ON "UserProfiles" ("NormalizedDisplayName");

CREATE INDEX "IX_WorkoutProgramExtraOccurrences_WorkoutProgramItemId" ON "WorkoutProgramExtraOccurrences" ("WorkoutProgramItemId");

CREATE INDEX "IX_WorkoutProgramExtraOccurrences_WorkoutProgramRunId_Date" ON "WorkoutProgramExtraOccurrences" ("WorkoutProgramRunId", "Date");

CREATE UNIQUE INDEX "IX_WorkoutProgramItemAlternatives_WorkoutProgramItemId_DisplayOrder" ON "WorkoutProgramItemAlternatives" ("WorkoutProgramItemId", "DisplayOrder");

CREATE UNIQUE INDEX "IX_WorkoutProgramItemAlternatives_WorkoutProgramItemId_WorkoutRevisionId" ON "WorkoutProgramItemAlternatives" ("WorkoutProgramItemId", "WorkoutRevisionId");

CREATE INDEX "IX_WorkoutProgramItemAlternatives_WorkoutRevisionId" ON "WorkoutProgramItemAlternatives" ("WorkoutRevisionId");

CREATE UNIQUE INDEX "IX_WorkoutProgramItems_WorkoutProgramRevisionId_Position" ON "WorkoutProgramItems" ("WorkoutProgramRevisionId", "Position");

CREATE INDEX "IX_WorkoutProgramItems_WorkoutRevisionId" ON "WorkoutProgramItems" ("WorkoutRevisionId");

CREATE INDEX "IX_WorkoutProgramRevisions_OwnerProfileId" ON "WorkoutProgramRevisions" ("OwnerProfileId");

CREATE UNIQUE INDEX "IX_WorkoutProgramRevisions_WorkoutProgramId_ContentSha256" ON "WorkoutProgramRevisions" ("WorkoutProgramId", "ContentSha256");

CREATE UNIQUE INDEX "IX_WorkoutProgramRevisions_WorkoutProgramId_RevisionNumber" ON "WorkoutProgramRevisions" ("WorkoutProgramId", "RevisionNumber");

CREATE UNIQUE INDEX "IX_WorkoutProgramRuns_UserProfileId" ON "WorkoutProgramRuns" ("UserProfileId") WHERE "Status" = 'Active';

CREATE INDEX "IX_WorkoutProgramRuns_UserProfileId_StartedAtUtc" ON "WorkoutProgramRuns" ("UserProfileId", "StartedAtUtc");

CREATE INDEX "IX_WorkoutProgramRuns_WorkoutProgramRevisionId" ON "WorkoutProgramRuns" ("WorkoutProgramRevisionId");

CREATE INDEX "IX_WorkoutProgramScheduleOverrides_WorkoutProgramItemId" ON "WorkoutProgramScheduleOverrides" ("WorkoutProgramItemId");

CREATE UNIQUE INDEX "IX_WorkoutProgramScheduleOverrides_WorkoutProgramRunId_WorkoutProgramItemId" ON "WorkoutProgramScheduleOverrides" ("WorkoutProgramRunId", "WorkoutProgramItemId");

CREATE UNIQUE INDEX "IX_WorkoutRevisions_WorkoutId_ContentSha256" ON "WorkoutRevisions" ("WorkoutId", "ContentSha256");

CREATE UNIQUE INDEX "IX_WorkoutRevisions_WorkoutId_RevisionNumber" ON "WorkoutRevisions" ("WorkoutId", "RevisionNumber");

CREATE TRIGGER TR_WorkoutRevisions_ImmutableDelete
BEFORE DELETE ON WorkoutRevisions
BEGIN
    SELECT RAISE(ABORT, 'Workout revisions are immutable.');
END;

CREATE TRIGGER TR_WorkoutRevisions_ImmutableUpdate
BEFORE UPDATE ON WorkoutRevisions
BEGIN
    SELECT RAISE(ABORT, 'Workout revisions are immutable.');
END;

CREATE INDEX "IX_WorkoutSessions_HistoryList" ON "WorkoutSessions" ("UserProfileId", "EndedAtUtc" DESC) WHERE "StartedAtUtc" IS NOT NULL AND "EndedAtUtc" IS NOT NULL AND "State" IN ('Completed', 'Stopped', 'Interrupted', 'Faulted') AND "SessionOrigin" <> 'SystemTest';

CREATE INDEX "IX_WorkoutSessions_RecoveryCandidates" ON "WorkoutSessions" ("State", "SessionOrigin", "RecoveryCheckpointUpdatedAtUtc") WHERE "State" = 'Running' AND "SessionOrigin" = 'Hardware' AND "RecoveryCheckpointJson" IS NOT NULL;

CREATE INDEX "IX_WorkoutSessions_State" ON "WorkoutSessions" ("State");

CREATE INDEX "IX_WorkoutSessions_UserProfileId_ArmedAtUtc" ON "WorkoutSessions" ("UserProfileId", "ArmedAtUtc");

CREATE INDEX "IX_WorkoutSessions_UserProfileId_SessionOrigin_EndedAtUtc" ON "WorkoutSessions" ("UserProfileId", "SessionOrigin", "EndedAtUtc");

CREATE INDEX "IX_WorkoutSessions_WorkoutProgramItemId" ON "WorkoutSessions" ("WorkoutProgramItemId");

CREATE UNIQUE INDEX "IX_WorkoutSessions_WorkoutProgramRunId_WorkoutProgramItemId" ON "WorkoutSessions" ("WorkoutProgramRunId", "WorkoutProgramItemId") WHERE "State" = 'Completed' AND "WorkoutProgramRunId" IS NOT NULL;

CREATE INDEX "IX_WorkoutSessions_WorkoutRevisionId" ON "WorkoutSessions" ("WorkoutRevisionId");

CREATE UNIQUE INDEX "UX_WorkoutSessions_ActiveSession" ON "WorkoutSessions" ("ActiveSessionKey");

