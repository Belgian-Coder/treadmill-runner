using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddWorkoutProgramAlternatives : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "WorkoutProgramItemAlternatives",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramItemId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            DisplayOrder = table.Column<int>(type: "INTEGER", nullable: false),
            Variant = table.Column<string>(type: "TEXT", maxLength: 40, nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramItemAlternatives", x => x.Id);
            table.CheckConstraint("CK_WorkoutProgramItemAlternatives_DisplayOrder", "\"DisplayOrder\" > 0");
            table.ForeignKey(
                      name: "FK_WorkoutProgramItemAlternatives_WorkoutProgramItems_WorkoutProgramItemId",
                      column: x => x.WorkoutProgramItemId,
                      principalTable: "WorkoutProgramItems",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_WorkoutProgramItemAlternatives_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramItemAlternatives_WorkoutProgramItemId_DisplayOrder",
          table: "WorkoutProgramItemAlternatives",
          columns: new[] { "WorkoutProgramItemId", "DisplayOrder" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramItemAlternatives_WorkoutProgramItemId_WorkoutRevisionId",
          table: "WorkoutProgramItemAlternatives",
          columns: new[] { "WorkoutProgramItemId", "WorkoutRevisionId" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramItemAlternatives_WorkoutRevisionId",
          table: "WorkoutProgramItemAlternatives",
          column: "WorkoutRevisionId");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "WorkoutProgramItemAlternatives");
    }
  }
}
