using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddSessionHistory : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "WorkoutSessions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileName = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutTitle = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            State = table.Column<string>(type: "TEXT", maxLength: 40, nullable: false),
            ArmedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            StartedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            EndedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            DurationSeconds = table.Column<double>(type: "REAL", nullable: false),
            DistanceKilometers = table.Column<double>(type: "REAL", nullable: false),
            EstimatedCalories = table.Column<double>(type: "REAL", nullable: false),
            AverageHeartRateBpm = table.Column<double>(type: "REAL", nullable: true),
            MaximumHeartRateBpm = table.Column<ushort>(type: "INTEGER", nullable: true),
            AverageSpeedKph = table.Column<double>(type: "REAL", nullable: false),
            AverageInclinePercent = table.Column<double>(type: "REAL", nullable: false),
            MetricAlgorithmVersion = table.Column<string>(type: "TEXT", maxLength: 60, nullable: false),
            ControllerConfigurationJson = table.Column<string>(type: "TEXT", nullable: false),
            PerceivedExertion = table.Column<int>(type: "INTEGER", nullable: true),
            DebriefNote = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: true),
            DebriefUpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutSessions", x => x.Id);
            table.CheckConstraint("CK_WorkoutSessions_Calories", "\"EstimatedCalories\" >= 0");
            table.CheckConstraint("CK_WorkoutSessions_Distance", "\"DistanceKilometers\" >= 0");
            table.CheckConstraint("CK_WorkoutSessions_Duration", "\"DurationSeconds\" >= 0");
            table.CheckConstraint("CK_WorkoutSessions_Rpe", "\"PerceivedExertion\" IS NULL OR (\"PerceivedExertion\" >= 1 AND \"PerceivedExertion\" <= 10)");
            table.CheckConstraint("CK_WorkoutSessions_State", "length(\"State\") > 0");
            table.ForeignKey(
                      name: "FK_WorkoutSessions_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_WorkoutSessions_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "SessionEvents",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutSessionId = table.Column<Guid>(type: "TEXT", nullable: false),
            OccurredAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            Kind = table.Column<string>(type: "TEXT", maxLength: 80, nullable: false),
            DetailsJson = table.Column<string>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_SessionEvents", x => x.Id);
            table.CheckConstraint("CK_SessionEvents_Kind", "length(\"Kind\") > 0");
            table.ForeignKey(
                      name: "FK_SessionEvents_WorkoutSessions_WorkoutSessionId",
                      column: x => x.WorkoutSessionId,
                      principalTable: "WorkoutSessions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "SessionSamples",
          columns: table => new
          {
            WorkoutSessionId = table.Column<Guid>(type: "TEXT", nullable: false),
            Sequence = table.Column<long>(type: "INTEGER", nullable: false),
            CapturedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            ElapsedMilliseconds = table.Column<double>(type: "REAL", nullable: false),
            PlannedSpeedKph = table.Column<double>(type: "REAL", nullable: true),
            RequestedSpeedKph = table.Column<double>(type: "REAL", nullable: false),
            MeasuredSpeedKph = table.Column<double>(type: "REAL", nullable: false),
            PlannedInclinePercent = table.Column<double>(type: "REAL", nullable: true),
            RequestedInclinePercent = table.Column<double>(type: "REAL", nullable: false),
            MeasuredInclinePercent = table.Column<double>(type: "REAL", nullable: false),
            HeartRateBpm = table.Column<ushort>(type: "INTEGER", nullable: true),
            DistanceKilometers = table.Column<double>(type: "REAL", nullable: false),
            EstimatedCalories = table.Column<double>(type: "REAL", nullable: false),
            TelemetryAgeMilliseconds = table.Column<double>(type: "REAL", nullable: false),
            MetricAlgorithmVersion = table.Column<string>(type: "TEXT", maxLength: 60, nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_SessionSamples", x => new { x.WorkoutSessionId, x.Sequence });
            table.CheckConstraint("CK_SessionSamples_Calories", "\"EstimatedCalories\" >= 0");
            table.CheckConstraint("CK_SessionSamples_Distance", "\"DistanceKilometers\" >= 0");
            table.CheckConstraint("CK_SessionSamples_Elapsed", "\"ElapsedMilliseconds\" >= 0");
            table.CheckConstraint("CK_SessionSamples_Sequence", "\"Sequence\" >= 0");
            table.CheckConstraint("CK_SessionSamples_Speeds", "(\"PlannedSpeedKph\" IS NULL OR \"PlannedSpeedKph\" >= 0) AND \"RequestedSpeedKph\" >= 0 AND \"MeasuredSpeedKph\" >= 0");
            table.CheckConstraint("CK_SessionSamples_TelemetryAge", "\"TelemetryAgeMilliseconds\" >= 0");
            table.ForeignKey(
                      name: "FK_SessionSamples_WorkoutSessions_WorkoutSessionId",
                      column: x => x.WorkoutSessionId,
                      principalTable: "WorkoutSessions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_SessionEvents_WorkoutSessionId_OccurredAtUtc",
          table: "SessionEvents",
          columns: new[] { "WorkoutSessionId", "OccurredAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_SessionSamples_WorkoutSessionId_CapturedAtUtc",
          table: "SessionSamples",
          columns: new[] { "WorkoutSessionId", "CapturedAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_State",
          table: "WorkoutSessions",
          column: "State");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_UserProfileId_ArmedAtUtc",
          table: "WorkoutSessions",
          columns: new[] { "UserProfileId", "ArmedAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_WorkoutRevisionId",
          table: "WorkoutSessions",
          column: "WorkoutRevisionId");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "SessionEvents");

      migrationBuilder.DropTable(
          name: "SessionSamples");

      migrationBuilder.DropTable(
          name: "WorkoutSessions");
    }
  }
}
