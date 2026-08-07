using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddPlanScheduleAdjustments : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "WorkoutProgramExtraOccurrences",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramRunId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramItemId = table.Column<Guid>(type: "TEXT", nullable: false),
            Date = table.Column<DateOnly>(type: "TEXT", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramExtraOccurrences", x => x.Id);
            table.ForeignKey(
                      name: "FK_WorkoutProgramExtraOccurrences_WorkoutProgramItems_WorkoutProgramItemId",
                      column: x => x.WorkoutProgramItemId,
                      principalTable: "WorkoutProgramItems",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_WorkoutProgramExtraOccurrences_WorkoutProgramRuns_WorkoutProgramRunId",
                      column: x => x.WorkoutProgramRunId,
                      principalTable: "WorkoutProgramRuns",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "WorkoutProgramScheduleOverrides",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramRunId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramItemId = table.Column<Guid>(type: "TEXT", nullable: false),
            TargetDate = table.Column<DateOnly>(type: "TEXT", nullable: true),
            IsSkipped = table.Column<bool>(type: "INTEGER", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramScheduleOverrides", x => x.Id);
            table.CheckConstraint("CK_WorkoutProgramScheduleOverrides_Value", "(\"IsSkipped\" = 1 AND \"TargetDate\" IS NULL) OR (\"IsSkipped\" = 0 AND \"TargetDate\" IS NOT NULL)");
            table.ForeignKey(
                      name: "FK_WorkoutProgramScheduleOverrides_WorkoutProgramItems_WorkoutProgramItemId",
                      column: x => x.WorkoutProgramItemId,
                      principalTable: "WorkoutProgramItems",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_WorkoutProgramScheduleOverrides_WorkoutProgramRuns_WorkoutProgramRunId",
                      column: x => x.WorkoutProgramRunId,
                      principalTable: "WorkoutProgramRuns",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramExtraOccurrences_WorkoutProgramItemId",
          table: "WorkoutProgramExtraOccurrences",
          column: "WorkoutProgramItemId");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramExtraOccurrences_WorkoutProgramRunId_Date",
          table: "WorkoutProgramExtraOccurrences",
          columns: new[] { "WorkoutProgramRunId", "Date" });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramScheduleOverrides_WorkoutProgramItemId",
          table: "WorkoutProgramScheduleOverrides",
          column: "WorkoutProgramItemId");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramScheduleOverrides_WorkoutProgramRunId_WorkoutProgramItemId",
          table: "WorkoutProgramScheduleOverrides",
          columns: new[] { "WorkoutProgramRunId", "WorkoutProgramItemId" },
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "WorkoutProgramExtraOccurrences");

      migrationBuilder.DropTable(
          name: "WorkoutProgramScheduleOverrides");
    }
  }
}
