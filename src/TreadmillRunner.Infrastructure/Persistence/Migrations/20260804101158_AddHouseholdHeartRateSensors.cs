using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddHouseholdHeartRateSensors : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropIndex(
          name: "IX_DeviceEnrollments_Role",
          table: "DeviceEnrollments");

      migrationBuilder.AddColumn<string>(
          name: "HeartRateDeviceFamily",
          table: "DeviceEnrollments",
          type: "TEXT",
          maxLength: 20,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "HeartRateDeviceKind",
          table: "DeviceEnrollments",
          type: "TEXT",
          maxLength: 20,
          nullable: true);

      migrationBuilder.CreateTable(
          name: "HeartRateDeviceAssignments",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            DeviceEnrollmentId = table.Column<Guid>(type: "TEXT", nullable: false),
            Priority = table.Column<int>(type: "INTEGER", nullable: false),
            AutoConnect = table.Column<bool>(type: "INTEGER", nullable: false),
            IsPreferred = table.Column<bool>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_HeartRateDeviceAssignments", x => x.Id);
            table.CheckConstraint("CK_HeartRateDeviceAssignments_Priority", "\"Priority\" >= 0 AND \"Priority\" <= 99");
            table.CheckConstraint("CK_HeartRateDeviceAssignments_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_HeartRateDeviceAssignments_DeviceEnrollments_DeviceEnrollmentId",
                      column: x => x.DeviceEnrollmentId,
                      principalTable: "DeviceEnrollments",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_HeartRateDeviceAssignments_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.Sql(
          """
                UPDATE "DeviceEnrollments"
                SET "HeartRateDeviceKind" = CASE
                      WHEN lower("DisplayName") LIKE '%polar h10%' OR lower("DisplayName") LIKE '%strap%' OR lower("DisplayName") LIKE '%belt%' THEN 'ChestStrap'
                      WHEN lower("DisplayName") LIKE '%garmin%' OR lower("DisplayName") LIKE '%fenix%' OR lower("DisplayName") LIKE '%vivoactive%' OR lower("DisplayName") LIKE '%watch%' THEN 'Watch'
                      ELSE 'Sensor'
                    END,
                    "HeartRateDeviceFamily" = CASE
                      WHEN lower("DisplayName") LIKE '%polar%' THEN 'Polar'
                      WHEN lower("DisplayName") LIKE '%garmin%' OR lower("DisplayName") LIKE '%fenix%' OR lower("DisplayName") LIKE '%vivoactive%' THEN 'Garmin'
                      ELSE 'Other'
                    END
                WHERE "Role" = 'HeartRate';

                INSERT INTO "HeartRateDeviceAssignments"
                  ("Id", "UserProfileId", "DeviceEnrollmentId", "Priority", "AutoConnect", "IsPreferred", "Version", "CreatedAtUtc", "UpdatedAtUtc")
                SELECT lower(hex(randomblob(16))), profile."Id", device."Id", 0, 1, 1, 1,
                       device."UpdatedAtUtc", device."UpdatedAtUtc"
                FROM "UserProfiles" profile
                CROSS JOIN "DeviceEnrollments" device
                WHERE profile."IsArchived" = 0
                  AND device."Role" = 'HeartRate'
                  AND device."IsArchived" = 0;
                """);

      migrationBuilder.CreateIndex(
          name: "IX_DeviceEnrollments_Role",
          table: "DeviceEnrollments",
          column: "Role",
          unique: true,
          filter: "\"Role\" = 'Treadmill' AND \"IsArchived\" = 0");

      migrationBuilder.CreateIndex(
          name: "IX_DeviceEnrollments_Role_DeviceId",
          table: "DeviceEnrollments",
          columns: new[] { "Role", "DeviceId" },
          unique: true,
          filter: "\"IsArchived\" = 0");

      migrationBuilder.CreateIndex(
          name: "IX_HeartRateDeviceAssignments_DeviceEnrollmentId",
          table: "HeartRateDeviceAssignments",
          column: "DeviceEnrollmentId");

      migrationBuilder.CreateIndex(
          name: "IX_HeartRateDeviceAssignments_UserProfileId",
          table: "HeartRateDeviceAssignments",
          column: "UserProfileId",
          unique: true,
          filter: "\"IsPreferred\" = 1");

      migrationBuilder.CreateIndex(
          name: "IX_HeartRateDeviceAssignments_UserProfileId_DeviceEnrollmentId",
          table: "HeartRateDeviceAssignments",
          columns: new[] { "UserProfileId", "DeviceEnrollmentId" },
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "HeartRateDeviceAssignments");

      migrationBuilder.DropIndex(
          name: "IX_DeviceEnrollments_Role",
          table: "DeviceEnrollments");

      migrationBuilder.DropIndex(
          name: "IX_DeviceEnrollments_Role_DeviceId",
          table: "DeviceEnrollments");

      migrationBuilder.DropColumn(
          name: "HeartRateDeviceFamily",
          table: "DeviceEnrollments");

      migrationBuilder.DropColumn(
          name: "HeartRateDeviceKind",
          table: "DeviceEnrollments");

      migrationBuilder.CreateIndex(
          name: "IX_DeviceEnrollments_Role",
          table: "DeviceEnrollments",
          column: "Role",
          unique: true,
          filter: "\"IsArchived\" = 0");
    }
  }
}
