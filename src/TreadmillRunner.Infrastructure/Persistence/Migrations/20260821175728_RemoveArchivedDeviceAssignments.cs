using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations;

/// <inheritdoc />
public partial class RemoveArchivedDeviceAssignments : Migration
{
  /// <inheritdoc />
  protected override void Up(MigrationBuilder migrationBuilder)
  {
    migrationBuilder.Sql(
      """
      DELETE FROM "HeartRateDeviceAssignments"
      WHERE EXISTS (
        SELECT 1
        FROM "DeviceEnrollments"
        WHERE "DeviceEnrollments"."Id" = "HeartRateDeviceAssignments"."DeviceEnrollmentId"
          AND "DeviceEnrollments"."IsArchived" = 1
      );
      """);
  }

  /// <inheritdoc />
  protected override void Down(MigrationBuilder migrationBuilder)
  {
    // Removed orphaned configuration cannot be reconstructed safely.
  }
}
