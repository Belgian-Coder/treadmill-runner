using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddOrderedWorkoutPrograms : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<string>(
          name: "SelectionSource",
          table: "WorkoutSessions",
          type: "TEXT",
          maxLength: 20,
          nullable: false,
          defaultValue: "Legacy");

      migrationBuilder.AddColumn<Guid>(
          name: "WorkoutProgramItemId",
          table: "WorkoutSessions",
          type: "TEXT",
          nullable: true);

      migrationBuilder.AddColumn<Guid>(
          name: "WorkoutProgramRunId",
          table: "WorkoutSessions",
          type: "TEXT",
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "Kind",
          table: "Workouts",
          type: "TEXT",
          maxLength: 20,
          nullable: false,
          defaultValue: "Structured");

      migrationBuilder.Sql(
          "UPDATE \"Workouts\" SET \"Kind\" = 'ManualTemplate' WHERE lower(trim(\"Name\")) = 'manual run';");

      migrationBuilder.CreateTable(
          name: "WorkoutPrograms",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            IsArchived = table.Column<bool>(type: "INTEGER", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutPrograms", x => x.Id);
          });

      migrationBuilder.CreateTable(
          name: "WorkoutProgramRevisions",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramId = table.Column<Guid>(type: "TEXT", nullable: false),
            RevisionNumber = table.Column<int>(type: "INTEGER", nullable: false),
            Name = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            Description = table.Column<string>(type: "TEXT", maxLength: 2000, nullable: true),
            Category = table.Column<string>(type: "TEXT", maxLength: 40, nullable: false),
            ContentSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramRevisions", x => x.Id);
            table.CheckConstraint("CK_WorkoutProgramRevisions_Category", "length(\"Category\") > 0");
            table.CheckConstraint("CK_WorkoutProgramRevisions_Hash", "length(\"ContentSha256\") = 64");
            table.CheckConstraint("CK_WorkoutProgramRevisions_Name", "length(\"Name\") > 0");
            table.CheckConstraint("CK_WorkoutProgramRevisions_Number", "\"RevisionNumber\" > 0");
            table.ForeignKey(
                      name: "FK_WorkoutProgramRevisions_WorkoutPrograms_WorkoutProgramId",
                      column: x => x.WorkoutProgramId,
                      principalTable: "WorkoutPrograms",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "WorkoutProgramItems",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            Position = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramItems", x => x.Id);
            table.CheckConstraint("CK_WorkoutProgramItems_Position", "\"Position\" > 0");
            table.ForeignKey(
                      name: "FK_WorkoutProgramItems_WorkoutProgramRevisions_WorkoutProgramRevisionId",
                      column: x => x.WorkoutProgramRevisionId,
                      principalTable: "WorkoutProgramRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_WorkoutProgramItems_WorkoutRevisions_WorkoutRevisionId",
                      column: x => x.WorkoutRevisionId,
                      principalTable: "WorkoutRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateTable(
          name: "WorkoutProgramRuns",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutProgramRevisionId = table.Column<Guid>(type: "TEXT", nullable: false),
            Status = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            StartedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            EndedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_WorkoutProgramRuns", x => x.Id);
            table.CheckConstraint("CK_WorkoutProgramRuns_Status", "\"Status\" IN ('Active', 'Completed', 'Abandoned')");
            table.CheckConstraint("CK_WorkoutProgramRuns_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_WorkoutProgramRuns_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_WorkoutProgramRuns_WorkoutProgramRevisions_WorkoutProgramRevisionId",
                      column: x => x.WorkoutProgramRevisionId,
                      principalTable: "WorkoutProgramRevisions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_WorkoutProgramItemId",
          table: "WorkoutSessions",
          column: "WorkoutProgramItemId");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutSessions_WorkoutProgramRunId_WorkoutProgramItemId",
          table: "WorkoutSessions",
          columns: new[] { "WorkoutProgramRunId", "WorkoutProgramItemId" },
          unique: true,
          filter: "\"State\" = 'Completed' AND \"WorkoutProgramRunId\" IS NOT NULL");

      migrationBuilder.AddCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts",
          sql: "\"Kind\" IN ('Structured', 'ManualTemplate')");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramItems_WorkoutProgramRevisionId_Position",
          table: "WorkoutProgramItems",
          columns: new[] { "WorkoutProgramRevisionId", "Position" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramItems_WorkoutRevisionId",
          table: "WorkoutProgramItems",
          column: "WorkoutRevisionId");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRevisions_WorkoutProgramId_ContentSha256",
          table: "WorkoutProgramRevisions",
          columns: new[] { "WorkoutProgramId", "ContentSha256" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRevisions_WorkoutProgramId_RevisionNumber",
          table: "WorkoutProgramRevisions",
          columns: new[] { "WorkoutProgramId", "RevisionNumber" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRuns_UserProfileId",
          table: "WorkoutProgramRuns",
          column: "UserProfileId",
          unique: true,
          filter: "\"Status\" = 'Active'");

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRuns_UserProfileId_StartedAtUtc",
          table: "WorkoutProgramRuns",
          columns: new[] { "UserProfileId", "StartedAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRuns_WorkoutProgramRevisionId",
          table: "WorkoutProgramRuns",
          column: "WorkoutProgramRevisionId");

      migrationBuilder.AddForeignKey(
          name: "FK_WorkoutSessions_WorkoutProgramItems_WorkoutProgramItemId",
          table: "WorkoutSessions",
          column: "WorkoutProgramItemId",
          principalTable: "WorkoutProgramItems",
          principalColumn: "Id",
          onDelete: ReferentialAction.Restrict);

      migrationBuilder.AddForeignKey(
          name: "FK_WorkoutSessions_WorkoutProgramRuns_WorkoutProgramRunId",
          table: "WorkoutSessions",
          column: "WorkoutProgramRunId",
          principalTable: "WorkoutProgramRuns",
          principalColumn: "Id",
          onDelete: ReferentialAction.Restrict);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropForeignKey(
          name: "FK_WorkoutSessions_WorkoutProgramItems_WorkoutProgramItemId",
          table: "WorkoutSessions");

      migrationBuilder.DropForeignKey(
          name: "FK_WorkoutSessions_WorkoutProgramRuns_WorkoutProgramRunId",
          table: "WorkoutSessions");

      migrationBuilder.DropTable(
          name: "WorkoutProgramItems");

      migrationBuilder.DropTable(
          name: "WorkoutProgramRuns");

      migrationBuilder.DropTable(
          name: "WorkoutProgramRevisions");

      migrationBuilder.DropTable(
          name: "WorkoutPrograms");

      migrationBuilder.DropIndex(
          name: "IX_WorkoutSessions_WorkoutProgramItemId",
          table: "WorkoutSessions");

      migrationBuilder.DropIndex(
          name: "IX_WorkoutSessions_WorkoutProgramRunId_WorkoutProgramItemId",
          table: "WorkoutSessions");

      migrationBuilder.DropCheckConstraint(
          name: "CK_Workouts_Kind",
          table: "Workouts");

      migrationBuilder.DropColumn(
          name: "SelectionSource",
          table: "WorkoutSessions");

      migrationBuilder.DropColumn(
          name: "WorkoutProgramItemId",
          table: "WorkoutSessions");

      migrationBuilder.DropColumn(
          name: "WorkoutProgramRunId",
          table: "WorkoutSessions");

      migrationBuilder.DropColumn(
          name: "Kind",
          table: "Workouts");
    }
  }
}
