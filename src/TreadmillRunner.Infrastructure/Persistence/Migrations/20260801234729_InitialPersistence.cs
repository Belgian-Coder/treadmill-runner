using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class InitialPersistence : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "OperationReceipts",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            ClientOperationId = table.Column<Guid>(type: "TEXT", nullable: false),
            OperationType = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            RequestFingerprint = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            StatusCode = table.Column<int>(type: "INTEGER", nullable: false),
            OutcomeJson = table.Column<string>(type: "TEXT", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_OperationReceipts", x => x.Id);
          });

      migrationBuilder.CreateTable(
          name: "UserProfiles",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            DisplayName = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            NormalizedDisplayName = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            UnitSystem = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            WeightKilograms = table.Column<double>(type: "REAL", nullable: false),
            MaximumHeartRateBpm = table.Column<ushort>(type: "INTEGER", nullable: true),
            MaximumSpeedKph = table.Column<double>(type: "REAL", nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            IsArchived = table.Column<bool>(type: "INTEGER", nullable: false),
            ArchivedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_UserProfiles", x => x.Id);
            table.CheckConstraint("CK_UserProfiles_Archive", "(\"IsArchived\" = 0 AND \"ArchivedAtUtc\" IS NULL) OR (\"IsArchived\" = 1 AND \"ArchivedAtUtc\" IS NOT NULL)");
            table.CheckConstraint("CK_UserProfiles_DisplayName", "length(\"DisplayName\") > 0");
            table.CheckConstraint("CK_UserProfiles_MaximumHeartRate", "\"MaximumHeartRateBpm\" IS NULL OR \"MaximumHeartRateBpm\" > 0");
            table.CheckConstraint("CK_UserProfiles_MaximumSpeed", "\"MaximumSpeedKph\" IS NULL OR \"MaximumSpeedKph\" > 0");
            table.CheckConstraint("CK_UserProfiles_Version", "\"Version\" > 0");
            table.CheckConstraint("CK_UserProfiles_Weight", "\"WeightKilograms\" > 0");
          });

      migrationBuilder.CreateTable(
          name: "Workouts",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            Name = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            IsArchived = table.Column<bool>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_Workouts", x => x.Id);
            table.CheckConstraint("CK_Workouts_Name", "length(\"Name\") > 0");
          });

      migrationBuilder.CreateTable(
          name: "CalendarSeries",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            Name = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            TimeZoneId = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            StartDate = table.Column<DateOnly>(type: "TEXT", nullable: false),
            EndDate = table.Column<DateOnly>(type: "TEXT", nullable: true),
            IntervalWeeks = table.Column<int>(type: "INTEGER", nullable: false),
            WeekdayMask = table.Column<int>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_CalendarSeries", x => x.Id);
            table.CheckConstraint("CK_CalendarSeries_DateRange", "\"EndDate\" IS NULL OR \"EndDate\" >= \"StartDate\"");
            table.CheckConstraint("CK_CalendarSeries_Interval", "\"IntervalWeeks\" > 0");
            table.CheckConstraint("CK_CalendarSeries_Version", "\"Version\" > 0");
            table.CheckConstraint("CK_CalendarSeries_Weekdays", "\"WeekdayMask\" > 0 AND \"WeekdayMask\" <= 127");
            table.ForeignKey(
                      name: "FK_CalendarSeries_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "HeartRateZones",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            Number = table.Column<int>(type: "INTEGER", nullable: false),
            Name = table.Column<string>(type: "TEXT", maxLength: 60, nullable: false),
            MinimumBpm = table.Column<ushort>(type: "INTEGER", nullable: false),
            MaximumBpm = table.Column<ushort>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_HeartRateZones", x => x.Id);
            table.CheckConstraint("CK_HeartRateZones_Number", "\"Number\" > 0");
            table.CheckConstraint("CK_HeartRateZones_Range", "\"MinimumBpm\" <= \"MaximumBpm\"");
            table.ForeignKey(
                      name: "FK_HeartRateZones_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "WorkoutRevisions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutId = table.Column<Guid>(type: "TEXT", nullable: false),
            RevisionNumber = table.Column<int>(type: "INTEGER", nullable: false),
            DefinitionJson = table.Column<string>(type: "TEXT", nullable: false),
            ContentSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutRevisions", x => x.Id);
            table.CheckConstraint("CK_WorkoutRevisions_Hash", "length(\"ContentSha256\") = 64");
            table.CheckConstraint("CK_WorkoutRevisions_Json", "length(\"DefinitionJson\") > 0");
            table.CheckConstraint("CK_WorkoutRevisions_Number", "\"RevisionNumber\" > 0");
            table.ForeignKey(
                      name: "FK_WorkoutRevisions_Workouts_WorkoutId",
                      column: x => x.WorkoutId,
                      principalTable: "Workouts",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "CalendarExceptions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            CalendarSeriesId = table.Column<Guid>(type: "TEXT", nullable: false),
            LocalDate = table.Column<DateOnly>(type: "TEXT", nullable: false),
            Kind = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            Note = table.Column<string>(type: "TEXT", maxLength: 500, nullable: true)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_CalendarExceptions", x => x.Id);
            table.ForeignKey(
                      name: "FK_CalendarExceptions_CalendarSeries_CalendarSeriesId",
                      column: x => x.CalendarSeriesId,
                      principalTable: "CalendarSeries",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "CalendarSeriesOptions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            CalendarSeriesId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            DisplayOrder = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_CalendarSeriesOptions", x => x.Id);
            table.ForeignKey(
                      name: "FK_CalendarSeriesOptions_CalendarSeries_CalendarSeriesId",
                      column: x => x.CalendarSeriesId,
                      principalTable: "CalendarSeries",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_CalendarSeriesOptions_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "ImportAudits",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: true),
            WorkoutId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            OriginalFileName = table.Column<string>(type: "TEXT", maxLength: 255, nullable: false),
            Format = table.Column<string>(type: "TEXT", maxLength: 32, nullable: false),
            SourceSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            WarningSummaryJson = table.Column<string>(type: "TEXT", nullable: false),
            ImportedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_ImportAudits", x => x.Id);
            table.ForeignKey(
                      name: "FK_ImportAudits_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.SetNull);
            table.ForeignKey(
                      name: "FK_ImportAudits_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_ImportAudits_Workouts_WorkoutId",
                      column: x => x.WorkoutId,
                      principalTable: "Workouts",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "TrainingDaySelections",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            LocalDate = table.Column<DateOnly>(type: "TEXT", nullable: false),
            CalendarSeriesId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            SelectedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_TrainingDaySelections", x => x.Id);
            table.ForeignKey(
                      name: "FK_TrainingDaySelections_CalendarSeries_CalendarSeriesId",
                      column: x => x.CalendarSeriesId,
                      principalTable: "CalendarSeries",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_TrainingDaySelections_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_TrainingDaySelections_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "CalendarExceptionOptions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            CalendarExceptionId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            DisplayOrder = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_CalendarExceptionOptions", x => x.Id);
            table.ForeignKey(
                      name: "FK_CalendarExceptionOptions_CalendarExceptions_CalendarExceptionId",
                      column: x => x.CalendarExceptionId,
                      principalTable: "CalendarExceptions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_CalendarExceptionOptions_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateIndex(
          name: "IX_CalendarExceptionOptions_CalendarExceptionId_DisplayOrder",
          table: "CalendarExceptionOptions",
          columns: new[] { "CalendarExceptionId", "DisplayOrder" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_CalendarExceptionOptions_WorkoutRevisionId",
          table: "CalendarExceptionOptions",
          column: "WorkoutRevisionId");

      migrationBuilder.CreateIndex(
          name: "IX_CalendarExceptions_CalendarSeriesId_LocalDate",
          table: "CalendarExceptions",
          columns: new[] { "CalendarSeriesId", "LocalDate" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_CalendarSeries_UserProfileId",
          table: "CalendarSeries",
          column: "UserProfileId");

      migrationBuilder.CreateIndex(
          name: "IX_CalendarSeriesOptions_CalendarSeriesId_DisplayOrder",
          table: "CalendarSeriesOptions",
          columns: new[] { "CalendarSeriesId", "DisplayOrder" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_CalendarSeriesOptions_WorkoutRevisionId",
          table: "CalendarSeriesOptions",
          column: "WorkoutRevisionId");

      migrationBuilder.CreateIndex(
          name: "IX_HeartRateZones_UserProfileId_Number",
          table: "HeartRateZones",
          columns: new[] { "UserProfileId", "Number" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_ImportAudits_Format_SourceSha256",
          table: "ImportAudits",
          columns: new[] { "Format", "SourceSha256" });

      migrationBuilder.CreateIndex(
          name: "IX_ImportAudits_UserProfileId",
          table: "ImportAudits",
          column: "UserProfileId");

      migrationBuilder.CreateIndex(
          name: "IX_ImportAudits_WorkoutId",
          table: "ImportAudits",
          column: "WorkoutId");

      migrationBuilder.CreateIndex(
          name: "IX_ImportAudits_WorkoutRevisionId",
          table: "ImportAudits",
          column: "WorkoutRevisionId");

      migrationBuilder.CreateIndex(
          name: "IX_OperationReceipts_ClientOperationId",
          table: "OperationReceipts",
          column: "ClientOperationId",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_TrainingDaySelections_CalendarSeriesId",
          table: "TrainingDaySelections",
          column: "CalendarSeriesId");

      migrationBuilder.CreateIndex(
          name: "IX_TrainingDaySelections_UserProfileId_LocalDate",
          table: "TrainingDaySelections",
          columns: new[] { "UserProfileId", "LocalDate" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_TrainingDaySelections_WorkoutRevisionId",
          table: "TrainingDaySelections",
          column: "WorkoutRevisionId");

      migrationBuilder.CreateIndex(
          name: "IX_UserProfiles_NormalizedDisplayName",
          table: "UserProfiles",
          column: "NormalizedDisplayName",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutRevisions_WorkoutId_ContentSha256",
          table: "WorkoutRevisions",
          columns: new[] { "WorkoutId", "ContentSha256" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutRevisions_WorkoutId_RevisionNumber",
          table: "WorkoutRevisions",
          columns: new[] { "WorkoutId", "RevisionNumber" },
          unique: true);

      migrationBuilder.Sql("""
                CREATE TRIGGER TR_WorkoutRevisions_ImmutableUpdate
                BEFORE UPDATE ON WorkoutRevisions
                BEGIN
                    SELECT RAISE(ABORT, 'Workout revisions are immutable.');
                END;
                """);

      migrationBuilder.Sql("""
                CREATE TRIGGER TR_WorkoutRevisions_ImmutableDelete
                BEFORE DELETE ON WorkoutRevisions
                BEGIN
                    SELECT RAISE(ABORT, 'Workout revisions are immutable.');
                END;
                """);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.Sql("DROP TRIGGER IF EXISTS TR_WorkoutRevisions_ImmutableUpdate;");
      migrationBuilder.Sql("DROP TRIGGER IF EXISTS TR_WorkoutRevisions_ImmutableDelete;");

      migrationBuilder.DropTable(
          name: "CalendarExceptionOptions");

      migrationBuilder.DropTable(
          name: "CalendarSeriesOptions");

      migrationBuilder.DropTable(
          name: "HeartRateZones");

      migrationBuilder.DropTable(
          name: "ImportAudits");

      migrationBuilder.DropTable(
          name: "OperationReceipts");

      migrationBuilder.DropTable(
          name: "TrainingDaySelections");

      migrationBuilder.DropTable(
          name: "CalendarExceptions");

      migrationBuilder.DropTable(
          name: "WorkoutRevisions");

      migrationBuilder.DropTable(
          name: "CalendarSeries");

      migrationBuilder.DropTable(
          name: "Workouts");

      migrationBuilder.DropTable(
          name: "UserProfiles");
    }
  }
}
