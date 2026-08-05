using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddBleReliabilityIncidents : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "BleReliabilityIncidents",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            DeviceEnrollmentId = table.Column<Guid>(type: "TEXT", nullable: false),
            Role = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            DeviceDisplayName = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            StartedAtUnixMilliseconds = table.Column<long>(type: "INTEGER", nullable: false),
            RecoveredAtUnixMilliseconds = table.Column<long>(type: "INTEGER", nullable: true),
            FirstConnectionGeneration = table.Column<long>(type: "INTEGER", nullable: false),
            RecoveredConnectionGeneration = table.Column<long>(type: "INTEGER", nullable: true),
            FailedAttemptCount = table.Column<int>(type: "INTEGER", nullable: false),
            FailureKind = table.Column<string>(type: "TEXT", maxLength: 50, nullable: false),
            LastSanitizedFault = table.Column<string>(type: "TEXT", maxLength: 256, nullable: false),
            MaximumReconnectDelaySeconds = table.Column<double>(type: "REAL", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_BleReliabilityIncidents", x => x.Id);
            table.CheckConstraint("CK_BleReliabilityIncidents_Attempts", "\"FailedAttemptCount\" > 0");
            table.CheckConstraint("CK_BleReliabilityIncidents_Delay", "\"MaximumReconnectDelaySeconds\" >= 0");
            table.CheckConstraint("CK_BleReliabilityIncidents_DisplayName", "length(\"DeviceDisplayName\") > 0");
            table.CheckConstraint("CK_BleReliabilityIncidents_FailureKind", "\"FailureKind\" IN ('NativeDisconnected', 'TelemetrySilent', 'NotificationEnded', 'GattTimeout', 'InvalidTelemetry', 'RequiredCharacteristicMissing', 'AdapterUnavailable')");
            table.CheckConstraint("CK_BleReliabilityIncidents_Fault", "length(\"LastSanitizedFault\") > 0");
            table.CheckConstraint("CK_BleReliabilityIncidents_RecoveryTime", "\"RecoveredAtUnixMilliseconds\" IS NULL OR \"RecoveredAtUnixMilliseconds\" >= \"StartedAtUnixMilliseconds\"");
            table.CheckConstraint("CK_BleReliabilityIncidents_Role", "\"Role\" IN ('Treadmill', 'HeartRate')");
            table.CheckConstraint("CK_BleReliabilityIncidents_StartedAt", "\"StartedAtUnixMilliseconds\" >= 0");
          });

      migrationBuilder.CreateIndex(
          name: "UX_BleReliabilityIncidents_OneOpenPerDevice",
          table: "BleReliabilityIncidents",
          column: "DeviceEnrollmentId",
          unique: true,
          filter: "\"RecoveredAtUnixMilliseconds\" IS NULL");

      migrationBuilder.CreateIndex(
          name: "IX_BleReliabilityIncidents_DeviceEnrollmentId_RecoveredAtUnixMilliseconds",
          table: "BleReliabilityIncidents",
          columns: new[] { "DeviceEnrollmentId", "RecoveredAtUnixMilliseconds" });

      migrationBuilder.CreateIndex(
          name: "IX_BleReliabilityIncidents_StartedAtUnixMilliseconds",
          table: "BleReliabilityIncidents",
          column: "StartedAtUnixMilliseconds");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "BleReliabilityIncidents");
    }
  }
}
