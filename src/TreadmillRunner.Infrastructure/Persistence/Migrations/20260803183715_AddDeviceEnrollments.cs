using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddDeviceEnrollments : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "DeviceEnrollments",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            Role = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            DeviceId = table.Column<string>(type: "TEXT", maxLength: 256, nullable: false),
            ProtocolId = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            IdentityFingerprint = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            DisplayName = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            ModelNumber = table.Column<string>(type: "TEXT", maxLength: 100, nullable: true),
            FirmwareRevision = table.Column<string>(type: "TEXT", maxLength: 100, nullable: true),
            TelemetryMode = table.Column<string>(type: "TEXT", maxLength: 20, nullable: true),
            CapabilitiesJson = table.Column<string>(type: "TEXT", nullable: true),
            Evidence = table.Column<string>(type: "TEXT", maxLength: 30, nullable: false),
            LastVerifiedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            IsArchived = table.Column<bool>(type: "INTEGER", nullable: false),
            ArchivedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_DeviceEnrollments", x => x.Id);
            table.CheckConstraint("CK_DeviceEnrollments_Archive", "(\"IsArchived\" = 0 AND \"ArchivedAtUtc\" IS NULL) OR (\"IsArchived\" = 1 AND \"ArchivedAtUtc\" IS NOT NULL)");
            table.CheckConstraint("CK_DeviceEnrollments_DeviceId", "length(\"DeviceId\") > 0");
            table.CheckConstraint("CK_DeviceEnrollments_Fingerprint", "length(\"IdentityFingerprint\") = 64");
            table.CheckConstraint("CK_DeviceEnrollments_Protocol", "length(\"ProtocolId\") > 0");
            table.CheckConstraint("CK_DeviceEnrollments_Role", "\"Role\" IN ('Treadmill', 'HeartRate')");
            table.CheckConstraint("CK_DeviceEnrollments_TreadmillSettings", "(\"Role\" = 'Treadmill' AND \"TelemetryMode\" IS NOT NULL AND \"CapabilitiesJson\" IS NOT NULL) OR (\"Role\" = 'HeartRate' AND \"TelemetryMode\" IS NULL AND \"CapabilitiesJson\" IS NULL)");
            table.CheckConstraint("CK_DeviceEnrollments_Version", "\"Version\" > 0");
          });

      migrationBuilder.CreateIndex(
          name: "IX_DeviceEnrollments_IdentityFingerprint",
          table: "DeviceEnrollments",
          column: "IdentityFingerprint");

      migrationBuilder.CreateIndex(
          name: "IX_DeviceEnrollments_Role",
          table: "DeviceEnrollments",
          column: "Role",
          unique: true,
          filter: "\"IsArchived\" = 0");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "DeviceEnrollments");
    }
  }
}
