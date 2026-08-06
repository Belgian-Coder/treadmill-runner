using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddSessionRecoveryCheckpoint : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<string>(
          name: "RecoveryCheckpointJson",
          table: "WorkoutSessions",
          type: "TEXT",
          maxLength: 16384,
          nullable: true);

      migrationBuilder.AddColumn<DateTimeOffset>(
          name: "RecoveryCheckpointUpdatedAtUtc",
          table: "WorkoutSessions",
          type: "TEXT",
          nullable: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropColumn(
          name: "RecoveryCheckpointJson",
          table: "WorkoutSessions");

      migrationBuilder.DropColumn(
          name: "RecoveryCheckpointUpdatedAtUtc",
          table: "WorkoutSessions");
    }
  }
}
