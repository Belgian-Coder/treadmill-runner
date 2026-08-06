using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddDailyUsePolish : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs");

      migrationBuilder.AddColumn<string>(
          name: "SessionOrigin",
          table: "WorkoutSessions",
          type: "TEXT",
          maxLength: 20,
          nullable: false,
          defaultValue: "Legacy");

      migrationBuilder.Sql("""
                UPDATE "WorkoutSessions"
                SET "SessionOrigin" = CASE
                    WHEN json_valid("ControllerConfigurationJson") = 1
                         AND json_extract("ControllerConfigurationJson", '$.mode') = 'GarminUploadTest' THEN 'SystemTest'
                    WHEN json_valid("ControllerConfigurationJson") = 1
                         AND json_extract("ControllerConfigurationJson", '$.mode') LIKE 'hardware:%' THEN 'Hardware'
                    WHEN json_valid("ControllerConfigurationJson") = 1
                         AND json_extract("ControllerConfigurationJson", '$.mode') = 'simulator' THEN 'Simulator'
                    ELSE 'Legacy'
                END;
                """);

      migrationBuilder.AddColumn<DateTimeOffset>(
          name: "AcknowledgedAtUtc",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          nullable: true);

      migrationBuilder.CreateTable(
          name: "TreadmillMaintenancePolicies",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            DeviceEnrollmentId = table.Column<Guid>(type: "TEXT", nullable: false),
            IntervalMonths = table.Column<int>(type: "INTEGER", nullable: false),
            DistanceIntervalKilometers = table.Column<double>(type: "REAL", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_TreadmillMaintenancePolicies", x => x.Id);
            table.CheckConstraint("CK_TreadmillMaintenancePolicies_Distance", "\"DistanceIntervalKilometers\" >= 1 AND \"DistanceIntervalKilometers\" <= 5000");
            table.CheckConstraint("CK_TreadmillMaintenancePolicies_Months", "\"IntervalMonths\" >= 1 AND \"IntervalMonths\" <= 24");
            table.CheckConstraint("CK_TreadmillMaintenancePolicies_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_TreadmillMaintenancePolicies_DeviceEnrollments_DeviceEnrollmentId",
                      column: x => x.DeviceEnrollmentId,
                      principalTable: "DeviceEnrollments",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "TreadmillMaintenanceEvents",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            TreadmillMaintenancePolicyId = table.Column<Guid>(type: "TEXT", nullable: false),
            OperationId = table.Column<Guid>(type: "TEXT", nullable: false),
            PerformedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            AppDistanceBaselineKilometers = table.Column<double>(type: "REAL", nullable: false),
            Note = table.Column<string>(type: "TEXT", maxLength: 500, nullable: true),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_TreadmillMaintenanceEvents", x => x.Id);
            table.CheckConstraint("CK_TreadmillMaintenanceEvents_Distance", "\"AppDistanceBaselineKilometers\" >= 0");
            table.CheckConstraint("CK_TreadmillMaintenanceEvents_Note", "\"Note\" IS NULL OR length(\"Note\") <= 500");
            table.ForeignKey(
                      name: "FK_TreadmillMaintenanceEvents_TreadmillMaintenancePolicies_TreadmillMaintenancePolicyId",
                      column: x => x.TreadmillMaintenancePolicyId,
                      principalTable: "TreadmillMaintenancePolicies",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.Sql("""
                INSERT INTO "TreadmillMaintenancePolicies"
                    ("Id", "DeviceEnrollmentId", "IntervalMonths", "DistanceIntervalKilometers", "Version", "CreatedAtUtc", "UpdatedAtUtc")
                SELECT
                    lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' ||
                    substr('89ab', abs(random()) % 4 + 1, 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))),
                    "Id", 3, 241.0, 1, '2026-08-06 09:43:39+00:00', '2026-08-06 09:43:39+00:00'
                FROM "DeviceEnrollments"
                WHERE "Role" = 'Treadmill';
                """);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_UserProfileId_SessionOrigin_EndedAtUtc",
          table: "WorkoutSessions",
          columns: new[] { "UserProfileId", "SessionOrigin", "EndedAtUtc" });

      migrationBuilder.AddCheckConstraint(
          name: "CK_WorkoutSessions_Origin",
          table: "WorkoutSessions",
          sql: "\"SessionOrigin\" IN ('Legacy', 'Hardware', 'Simulator', 'SystemTest')");

      migrationBuilder.AddCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs",
          sql: "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed', 'FoundInGarmin')");

      migrationBuilder.CreateIndex(
          name: "IX_TreadmillMaintenanceEvents_OperationId",
          table: "TreadmillMaintenanceEvents",
          column: "OperationId",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_TreadmillMaintenanceEvents_TreadmillMaintenancePolicyId_PerformedAtUtc",
          table: "TreadmillMaintenanceEvents",
          columns: new[] { "TreadmillMaintenancePolicyId", "PerformedAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_TreadmillMaintenancePolicies_DeviceEnrollmentId",
          table: "TreadmillMaintenancePolicies",
          column: "DeviceEnrollmentId",
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "TreadmillMaintenanceEvents");

      migrationBuilder.DropTable(
          name: "TreadmillMaintenancePolicies");

      migrationBuilder.DropIndex(
          name: "IX_WorkoutSessions_UserProfileId_SessionOrigin_EndedAtUtc",
          table: "WorkoutSessions");

      migrationBuilder.DropCheckConstraint(
          name: "CK_WorkoutSessions_Origin",
          table: "WorkoutSessions");

      migrationBuilder.DropCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs");

      migrationBuilder.Sql("""
                UPDATE "GarminActivityUploadJobs"
                SET "Status" = 'Unknown', "AcknowledgedAtUtc" = NULL
                WHERE "Status" = 'FoundInGarmin';
                """);

      migrationBuilder.DropColumn(
          name: "SessionOrigin",
          table: "WorkoutSessions");

      migrationBuilder.DropColumn(
          name: "AcknowledgedAtUtc",
          table: "GarminActivityUploadJobs");

      migrationBuilder.AddCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs",
          sql: "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed')");
    }
  }
}
