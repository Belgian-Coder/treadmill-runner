using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddLocalFirstExperience : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "LocalBackupPolicies",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            DestinationPath = table.Column<string>(type: "TEXT", maxLength: 1024, nullable: false),
            IntervalHours = table.Column<int>(type: "INTEGER", nullable: false),
            RetentionCount = table.Column<int>(type: "INTEGER", nullable: false),
            Enabled = table.Column<bool>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_LocalBackupPolicies", x => x.Id);
            table.CheckConstraint("CK_LocalBackupPolicies_Interval", "\"IntervalHours\" >= 1 AND \"IntervalHours\" <= 168");
            table.CheckConstraint("CK_LocalBackupPolicies_Retention", "\"RetentionCount\" >= 2 AND \"RetentionCount\" <= 60");
            table.CheckConstraint("CK_LocalBackupPolicies_Version", "\"Version\" > 0");
          });

      migrationBuilder.CreateTable(
          name: "LocalGoals",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            Kind = table.Column<string>(type: "TEXT", maxLength: 30, nullable: false),
            Period = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            TargetValue = table.Column<double>(type: "REAL", nullable: false),
            Enabled = table.Column<bool>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_LocalGoals", x => x.Id);
            table.CheckConstraint("CK_LocalGoals_Kind", "\"Kind\" IN ('Sessions', 'Minutes', 'Distance', 'PlanCompletion')");
            table.CheckConstraint("CK_LocalGoals_Period", "\"Period\" IN ('Weekly', 'Monthly', 'Plan')");
            table.CheckConstraint("CK_LocalGoals_Target", "\"TargetValue\" > 0");
            table.CheckConstraint("CK_LocalGoals_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_LocalGoals_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "ProgressionRecommendations",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            OperationId = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutSessionId = table.Column<Guid>(type: "TEXT", nullable: false),
            Action = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            Reason = table.Column<string>(type: "TEXT", maxLength: 500, nullable: false),
            AlgorithmVersion = table.Column<string>(type: "TEXT", maxLength: 50, nullable: false),
            EvidenceJson = table.Column<string>(type: "TEXT", nullable: false),
            Status = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            DecidedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_ProgressionRecommendations", x => x.Id);
            table.CheckConstraint("CK_ProgressionRecommendations_Action", "\"Action\" IN ('Maintain', 'Repeat', 'Reduce', 'Advance', 'Reschedule')");
            table.CheckConstraint("CK_ProgressionRecommendations_Decision", "(\"Status\" = 'Pending' AND \"DecidedAtUtc\" IS NULL) OR (\"Status\" <> 'Pending' AND \"DecidedAtUtc\" IS NOT NULL)");
            table.CheckConstraint("CK_ProgressionRecommendations_Reason", "length(\"Reason\") > 0");
            table.CheckConstraint("CK_ProgressionRecommendations_Status", "\"Status\" IN ('Pending', 'Accepted', 'Rejected')");
            table.CheckConstraint("CK_ProgressionRecommendations_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_ProgressionRecommendations_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_ProgressionRecommendations_WorkoutSessions_WorkoutSessionId",
                      column: x => x.WorkoutSessionId,
                      principalTable: "WorkoutSessions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "RunnerExperiencePreferences",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            DisplayStyle = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            PrimaryMetricsJson = table.Column<string>(type: "TEXT", maxLength: 256, nullable: false),
            CueStepChanges = table.Column<bool>(type: "INTEGER", nullable: false),
            CueHeartRateDeparture = table.Column<bool>(type: "INTEGER", nullable: false),
            CueHalfway = table.Column<bool>(type: "INTEGER", nullable: false),
            CueConnectionProblems = table.Column<bool>(type: "INTEGER", nullable: false),
            CueCompletion = table.Column<bool>(type: "INTEGER", nullable: false),
            CueVolumePercent = table.Column<int>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_RunnerExperiencePreferences", x => x.Id);
            table.CheckConstraint("CK_RunnerExperiencePreferences_Style", "\"DisplayStyle\" IN ('Balanced', 'LargeText', 'HighContrast')");
            table.CheckConstraint("CK_RunnerExperiencePreferences_Version", "\"Version\" > 0");
            table.CheckConstraint("CK_RunnerExperiencePreferences_Volume", "\"CueVolumePercent\" >= 0 AND \"CueVolumePercent\" <= 100");
            table.ForeignKey(
                      name: "FK_RunnerExperiencePreferences_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "BackupVerifications",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            LocalBackupPolicyId = table.Column<Guid>(type: "TEXT", nullable: false),
            BackupPath = table.Column<string>(type: "TEXT", maxLength: 2048, nullable: false),
            Status = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            Detail = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: false),
            BackupBytes = table.Column<long>(type: "INTEGER", nullable: false),
            StartedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            CompletedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_BackupVerifications", x => x.Id);
            table.CheckConstraint("CK_BackupVerifications_Bytes", "\"BackupBytes\" >= 0");
            table.CheckConstraint("CK_BackupVerifications_Status", "\"Status\" IN ('Verified', 'Failed')");
            table.CheckConstraint("CK_BackupVerifications_Time", "\"CompletedAtUtc\" >= \"StartedAtUtc\"");
            table.ForeignKey(
                      name: "FK_BackupVerifications_LocalBackupPolicies_LocalBackupPolicyId",
                      column: x => x.LocalBackupPolicyId,
                      principalTable: "LocalBackupPolicies",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_BackupVerifications_LocalBackupPolicyId_CompletedAtUtc",
          table: "BackupVerifications",
          columns: new[] { "LocalBackupPolicyId", "CompletedAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_LocalGoals_UserProfileId_Kind_Period",
          table: "LocalGoals",
          columns: new[] { "UserProfileId", "Kind", "Period" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_ProgressionRecommendations_OperationId",
          table: "ProgressionRecommendations",
          column: "OperationId",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_ProgressionRecommendations_UserProfileId_WorkoutSessionId",
          table: "ProgressionRecommendations",
          columns: new[] { "UserProfileId", "WorkoutSessionId" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_ProgressionRecommendations_WorkoutSessionId",
          table: "ProgressionRecommendations",
          column: "WorkoutSessionId");

      migrationBuilder.CreateIndex(
          name: "IX_RunnerExperiencePreferences_UserProfileId",
          table: "RunnerExperiencePreferences",
          column: "UserProfileId",
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "BackupVerifications");

      migrationBuilder.DropTable(
          name: "LocalGoals");

      migrationBuilder.DropTable(
          name: "ProgressionRecommendations");

      migrationBuilder.DropTable(
          name: "RunnerExperiencePreferences");

      migrationBuilder.DropTable(
          name: "LocalBackupPolicies");
    }
  }
}
