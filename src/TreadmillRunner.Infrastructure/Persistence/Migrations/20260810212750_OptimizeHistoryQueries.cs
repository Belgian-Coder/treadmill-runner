using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class OptimizeHistoryQueries : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_HistoryList",
          table: "WorkoutSessions",
          columns: new[] { "UserProfileId", "EndedAtUtc" },
          descending: new[] { false, true },
          filter: "\"StartedAtUtc\" IS NOT NULL AND \"EndedAtUtc\" IS NOT NULL AND \"State\" IN ('Completed', 'Stopped', 'Interrupted', 'Faulted') AND \"SessionOrigin\" <> 'SystemTest'");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropIndex(
          name: "IX_WorkoutSessions_HistoryList",
          table: "WorkoutSessions");
    }
  }
}
