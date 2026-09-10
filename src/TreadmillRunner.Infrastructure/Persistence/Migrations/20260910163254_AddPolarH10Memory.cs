using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddPolarH10Memory : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<bool>(
          name: "RecordPolarH10Memory",
          table: "WorkoutSessions",
          type: "INTEGER",
          nullable: false,
          defaultValue: false);

      migrationBuilder.CreateTable(
          name: "PolarH10Recordings",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutSessionId = table.Column<Guid>(type: "TEXT", nullable: true),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: true),
            DeviceEnrollmentId = table.Column<Guid>(type: "TEXT", nullable: false),
            ExerciseId = table.Column<string>(type: "TEXT", maxLength: 64, nullable: false),
            Status = table.Column<string>(type: "TEXT", maxLength: 24, nullable: false),
            Origin = table.Column<string>(type: "TEXT", maxLength: 12, nullable: false),
            SampleType = table.Column<string>(type: "TEXT", maxLength: 16, nullable: false),
            SampleIntervalSeconds = table.Column<int>(type: "INTEGER", nullable: false),
            StartRequestedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            StartConfirmedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            StopRequestedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            StopConfirmedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            ExternalRecordingId = table.Column<string>(type: "TEXT", maxLength: 256, nullable: true),
            RemotePath = table.Column<string>(type: "TEXT", maxLength: 512, nullable: true),
            Payload = table.Column<byte[]>(type: "BLOB", maxLength: 8388608, nullable: true),
            PayloadSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: true),
            PayloadBytes = table.Column<int>(type: "INTEGER", nullable: false),
            QueuedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            StartedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            EndedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            LeaseExpiresAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            AttemptCount = table.Column<int>(type: "INTEGER", nullable: false),
            LastError = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: true),
            MergeCount = table.Column<int>(type: "INTEGER", nullable: false),
            RemovalCount = table.Column<int>(type: "INTEGER", nullable: false),
            Version = table.Column<int>(type: "INTEGER", nullable: false),
            AvailableAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            OperationFingerprint = table.Column<string>(type: "TEXT", maxLength: 64, nullable: true)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_PolarH10Recordings", x => x.Id);
            table.CheckConstraint("CK_PolarH10Recordings_Attempts", "\"AttemptCount\" >= 0");
            table.CheckConstraint("CK_PolarH10Recordings_Exercise", "length(\"ExerciseId\") BETWEEN 1 AND 64");
            table.CheckConstraint("CK_PolarH10Recordings_Status", "\"Status\" IN ('StartPending','Recording','StopPending','AwaitingDevice','Downloading','Downloaded','ReviewRequired','Merging','Merged','RemovalPending','Completed','Retained','Skipped','NotStarted','DiscardCleanupPending','Retryable')");
            table.ForeignKey(
                      name: "FK_PolarH10Recordings_DeviceEnrollments_DeviceEnrollmentId",
                      column: x => x.DeviceEnrollmentId,
                      principalTable: "DeviceEnrollments",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_PolarH10Recordings_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.SetNull);
            table.ForeignKey(
                      name: "FK_PolarH10Recordings_WorkoutSessions_WorkoutSessionId",
                      column: x => x.WorkoutSessionId,
                      principalTable: "WorkoutSessions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.SetNull);
          });

      migrationBuilder.CreateTable(
          name: "PolarH10RecordingSamples",
          columns: table => new
          {
            PolarH10RecordingId = table.Column<Guid>(type: "TEXT", nullable: false),
            Sequence = table.Column<long>(type: "INTEGER", nullable: false),
            CapturedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            BeatsPerMinute = table.Column<ushort>(type: "INTEGER", nullable: true),
            RrIntervalMilliseconds = table.Column<uint>(type: "INTEGER", nullable: true)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_PolarH10RecordingSamples", x => new { x.PolarH10RecordingId, x.Sequence });
            table.ForeignKey(
                      name: "FK_PolarH10RecordingSamples_PolarH10Recordings_PolarH10RecordingId",
                      column: x => x.PolarH10RecordingId,
                      principalTable: "PolarH10Recordings",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10Recordings_DeviceEnrollmentId_ExerciseId",
          table: "PolarH10Recordings",
          columns: new[] { "DeviceEnrollmentId", "ExerciseId" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10Recordings_DeviceEnrollmentId_RemotePath",
          table: "PolarH10Recordings",
          columns: new[] { "DeviceEnrollmentId", "RemotePath" },
          unique: true,
          filter: "\"RemotePath\" IS NOT NULL");

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10Recordings_Status_LeaseExpiresAtUtc",
          table: "PolarH10Recordings",
          columns: new[] { "Status", "LeaseExpiresAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10Recordings_UserProfileId",
          table: "PolarH10Recordings",
          column: "UserProfileId");

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10Recordings_WorkoutSessionId",
          table: "PolarH10Recordings",
          column: "WorkoutSessionId",
          unique: true,
          filter: "\"WorkoutSessionId\" IS NOT NULL");

      migrationBuilder.CreateIndex(
          name: "IX_PolarH10RecordingSamples_PolarH10RecordingId_CapturedAtUtc",
          table: "PolarH10RecordingSamples",
          columns: new[] { "PolarH10RecordingId", "CapturedAtUtc" });
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "PolarH10RecordingSamples");

      migrationBuilder.DropTable(
          name: "PolarH10Recordings");

      migrationBuilder.DropColumn(
          name: "RecordPolarH10Memory",
          table: "WorkoutSessions");
    }
  }
}
