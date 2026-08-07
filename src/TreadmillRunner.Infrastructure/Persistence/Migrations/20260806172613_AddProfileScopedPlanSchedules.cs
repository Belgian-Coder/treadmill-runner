using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddProfileScopedPlanSchedules : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts");

      migrationBuilder.AddColumn<string>(
          name: "ScheduleTimeZoneId",
          table: "WorkoutProgramRuns",
          type: "TEXT",
          maxLength: 100,
          nullable: true);

      migrationBuilder.AddColumn<DateOnly>(
          name: "ScheduledStartDate",
          table: "WorkoutProgramRuns",
          type: "TEXT",
          nullable: true);

      migrationBuilder.AddColumn<int>(
          name: "ScheduledWeekdayMask",
          table: "WorkoutProgramRuns",
          type: "INTEGER",
          nullable: false,
          defaultValue: 0);

      migrationBuilder.AddCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts",
          sql: "\"Kind\" IN ('Structured', 'ManualTemplate', 'PlanInternal')");

      migrationBuilder.AddCheckConstraint(
          name: "CK_WorkoutProgramRuns_Schedule",
          table: "WorkoutProgramRuns",
          sql: "(\"ScheduledStartDate\" IS NULL AND \"ScheduledWeekdayMask\" = 0 AND \"ScheduleTimeZoneId\" IS NULL) OR (\"ScheduledStartDate\" IS NOT NULL AND \"ScheduledWeekdayMask\" BETWEEN 1 AND 127 AND length(\"ScheduleTimeZoneId\") > 0)");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts");

      migrationBuilder.DropCheckConstraint(
          name: "CK_WorkoutProgramRuns_Schedule",
          table: "WorkoutProgramRuns");

      migrationBuilder.DropColumn(
          name: "ScheduleTimeZoneId",
          table: "WorkoutProgramRuns");

      migrationBuilder.DropColumn(
          name: "ScheduledStartDate",
          table: "WorkoutProgramRuns");

      migrationBuilder.DropColumn(
          name: "ScheduledWeekdayMask",
          table: "WorkoutProgramRuns");

      migrationBuilder.AddCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts",
          sql: "\"Kind\" IN ('Structured', 'ManualTemplate')");
    }
  }
}
