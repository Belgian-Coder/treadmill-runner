using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddScheduleGroups : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropIndex(
          name: "IX_CalendarSeries_UserProfileId",
          table: "CalendarSeries");

      migrationBuilder.AddColumn<Guid>(
          name: "ScheduleGroupId",
          table: "CalendarSeries",
          type: "TEXT",
          nullable: false,
          defaultValue: new Guid("00000000-0000-0000-0000-000000000000"));

      migrationBuilder.Sql(
          "UPDATE CalendarSeries SET ScheduleGroupId = Id WHERE ScheduleGroupId = '00000000-0000-0000-0000-000000000000'");

      migrationBuilder.CreateIndex(
          name: "IX_CalendarSeries_UserProfileId_ScheduleGroupId",
          table: "CalendarSeries",
          columns: new[] { "UserProfileId", "ScheduleGroupId" });
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropIndex(
          name: "IX_CalendarSeries_UserProfileId_ScheduleGroupId",
          table: "CalendarSeries");

      migrationBuilder.DropColumn(
          name: "ScheduleGroupId",
          table: "CalendarSeries");

      migrationBuilder.CreateIndex(
          name: "IX_CalendarSeries_UserProfileId",
          table: "CalendarSeries",
          column: "UserProfileId");
    }
  }
}
