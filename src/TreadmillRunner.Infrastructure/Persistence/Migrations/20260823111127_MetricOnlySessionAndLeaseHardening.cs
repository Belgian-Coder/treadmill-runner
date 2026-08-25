using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class MetricOnlySessionAndLeaseHardening : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs");

      // UnitSystem stays in the persisted/API contract for compatibility,
      // but Metric is the product's sole supported value.
      migrationBuilder.Sql(
          "UPDATE \"UserProfiles\" SET \"UnitSystem\" = 'Metric' WHERE \"UnitSystem\" <> 'Metric';");

      // Older databases could contain more than one active row because
      // enforcement previously lived only in the process. Keep the newest
      // session and terminate every older conflict before adding the unique
      // computed key. The gateway repeats this reconciliation at startup.
      migrationBuilder.Sql(
          """
                INSERT INTO "SessionEvents" ("Id", "WorkoutSessionId", "OccurredAtUtc", "Kind", "DetailsJson")
                SELECT
                  lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(2))) || '-' || lower(hex(randomblob(6))),
                  "Id",
                  strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
                  'session-interrupted',
                  json_object(
                    'reason', 'A newer active session was found during database migration reconciliation.',
                    'occurredAt', strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
                FROM "WorkoutSessions"
                WHERE "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume')
                  AND "Id" NOT IN (
                    SELECT "Id"
                    FROM "WorkoutSessions"
                    WHERE "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume')
                    ORDER BY "ArmedAtUtc" DESC, "Id" DESC
                    LIMIT 1);

                UPDATE "WorkoutSessions"
                SET
                  "State" = 'Interrupted',
                  "EndedAtUtc" = COALESCE("EndedAtUtc", strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
                  "DurationSeconds" = CASE
                    WHEN "StartedAtUtc" IS NULL THEN "DurationSeconds"
                    ELSE max("DurationSeconds", (julianday('now') - julianday("StartedAtUtc")) * 86400.0)
                  END
                WHERE "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume')
                  AND "Id" NOT IN (
                    SELECT "Id"
                    FROM "WorkoutSessions"
                    WHERE "State" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume')
                    ORDER BY "ArmedAtUtc" DESC, "Id" DESC
                    LIMIT 1);
                """);

      migrationBuilder.AddColumn<int>(
          name: "ActiveSessionKey",
          table: "WorkoutSessions",
          type: "INTEGER",
          nullable: true,
          computedColumnSql: "CASE WHEN \"State\" IN ('ArmedWaitingForPhysicalStart', 'Running', 'PausedWaitingForPhysicalResume') THEN 1 ELSE NULL END",
          stored: false);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_RecoveryCandidates",
          table: "WorkoutSessions",
          columns: new[] { "State", "SessionOrigin", "RecoveryCheckpointUpdatedAtUtc" },
          filter: "\"State\" = 'Running' AND \"SessionOrigin\" = 'Hardware' AND \"RecoveryCheckpointJson\" IS NOT NULL");

      migrationBuilder.CreateIndex(
          name: "UX_WorkoutSessions_ActiveSession",
          table: "WorkoutSessions",
          column: "ActiveSessionKey",
          unique: true);

      migrationBuilder.AddCheckConstraint(
          name: "CK_UserProfiles_UnitSystem",
          table: "UserProfiles",
          sql: "\"UnitSystem\" = 'Metric'");

      migrationBuilder.CreateIndex(
          name: "IX_OperationReceipts_CreatedAtUtc",
          table: "OperationReceipts",
          column: "CreatedAtUtc");

      migrationBuilder.CreateIndex(
          name: "IX_GarminSyncItems_Status_LeaseExpiresAtUtc",
          table: "GarminSyncItems",
          columns: new[] { "Status", "LeaseExpiresAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadJobs_Status_LeaseExpiresAtUtc",
          table: "GarminActivityUploadJobs",
          columns: new[] { "Status", "LeaseExpiresAtUtc" });

      migrationBuilder.AddCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs",
          sql: "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed', 'FoundInGarmin', 'ReviewRequired')");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropIndex(
          name: "IX_WorkoutSessions_RecoveryCandidates",
          table: "WorkoutSessions");

      migrationBuilder.DropIndex(
          name: "UX_WorkoutSessions_ActiveSession",
          table: "WorkoutSessions");

      migrationBuilder.DropCheckConstraint(
          name: "CK_UserProfiles_UnitSystem",
          table: "UserProfiles");

      migrationBuilder.DropIndex(
          name: "IX_OperationReceipts_CreatedAtUtc",
          table: "OperationReceipts");

      migrationBuilder.DropIndex(
          name: "IX_GarminSyncItems_Status_LeaseExpiresAtUtc",
          table: "GarminSyncItems");

      migrationBuilder.DropIndex(
          name: "IX_GarminActivityUploadJobs_Status_LeaseExpiresAtUtc",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs");

      migrationBuilder.Sql(
          "UPDATE \"GarminActivityUploadJobs\" SET \"Status\" = 'Dismissed' WHERE \"Status\" = 'ReviewRequired';");

      migrationBuilder.DropColumn(
          name: "ActiveSessionKey",
          table: "WorkoutSessions");

      migrationBuilder.AddCheckConstraint(
          name: "CK_GarminActivityUploadJobs_Status",
          table: "GarminActivityUploadJobs",
          sql: "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed', 'FoundInGarmin')");
    }
  }
}
