using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddPremadePlanCatalog : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<Guid>(
          name: "OwnerProfileId",
          table: "WorkoutProgramRevisions",
          type: "TEXT",
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "TemplateId",
          table: "WorkoutProgramRevisions",
          type: "TEXT",
          maxLength: 100,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "TemplateVersion",
          table: "WorkoutProgramRevisions",
          type: "TEXT",
          maxLength: 40,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "Phase",
          table: "WorkoutProgramItems",
          type: "TEXT",
          maxLength: 80,
          nullable: true);

      migrationBuilder.AddColumn<int>(
          name: "SessionNumber",
          table: "WorkoutProgramItems",
          type: "INTEGER",
          nullable: true);

      migrationBuilder.AddColumn<int>(
          name: "WeekNumber",
          table: "WorkoutProgramItems",
          type: "INTEGER",
          nullable: true);

      migrationBuilder.CreateTable(
          name: "PremadePlanInstallations",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            TemplateId = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            TemplateVersion = table.Column<string>(type: "TEXT", maxLength: 40, nullable: false),
            TemplateContentSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            CopyNumber = table.Column<int>(type: "INTEGER", nullable: false),
            WorkoutProgramId = table.Column<Guid>(type: "TEXT", nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_PremadePlanInstallations", x => x.Id);
            table.CheckConstraint("CK_PremadePlanInstallations_CopyNumber", "\"CopyNumber\" > 0");
            table.CheckConstraint("CK_PremadePlanInstallations_Hash", "length(\"TemplateContentSha256\") = 64");
            table.CheckConstraint("CK_PremadePlanInstallations_TemplateId", "length(\"TemplateId\") > 0");
            table.CheckConstraint("CK_PremadePlanInstallations_TemplateVersion", "length(\"TemplateVersion\") > 0");
            table.ForeignKey(
                      name: "FK_PremadePlanInstallations_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
            table.ForeignKey(
                      name: "FK_PremadePlanInstallations_WorkoutPrograms_WorkoutProgramId",
                      column: x => x.WorkoutProgramId,
                      principalTable: "WorkoutPrograms",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Restrict);
          });

      migrationBuilder.CreateIndex(
          name: "IX_WorkoutProgramRevisions_OwnerProfileId",
          table: "WorkoutProgramRevisions",
          column: "OwnerProfileId");

      migrationBuilder.CreateIndex(
          name: "IX_PremadePlanInstallations_UserProfileId_TemplateId_TemplateVersion_CopyNumber",
          table: "PremadePlanInstallations",
          columns: new[] { "UserProfileId", "TemplateId", "TemplateVersion", "CopyNumber" },
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_PremadePlanInstallations_WorkoutProgramId",
          table: "PremadePlanInstallations",
          column: "WorkoutProgramId",
          unique: true);

      migrationBuilder.AddForeignKey(
          name: "FK_WorkoutProgramRevisions_UserProfiles_OwnerProfileId",
          table: "WorkoutProgramRevisions",
          column: "OwnerProfileId",
          principalTable: "UserProfiles",
          principalColumn: "Id",
          onDelete: ReferentialAction.Restrict);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropForeignKey(
          name: "FK_WorkoutProgramRevisions_UserProfiles_OwnerProfileId",
          table: "WorkoutProgramRevisions");

      migrationBuilder.DropTable(
          name: "PremadePlanInstallations");

      migrationBuilder.DropIndex(
          name: "IX_WorkoutProgramRevisions_OwnerProfileId",
          table: "WorkoutProgramRevisions");

      migrationBuilder.DropColumn(
          name: "OwnerProfileId",
          table: "WorkoutProgramRevisions");

      migrationBuilder.DropColumn(
          name: "TemplateId",
          table: "WorkoutProgramRevisions");

      migrationBuilder.DropColumn(
          name: "TemplateVersion",
          table: "WorkoutProgramRevisions");

      migrationBuilder.DropColumn(
          name: "Phase",
          table: "WorkoutProgramItems");

      migrationBuilder.DropColumn(
          name: "SessionNumber",
          table: "WorkoutProgramItems");

      migrationBuilder.DropColumn(
          name: "WeekNumber",
          table: "WorkoutProgramItems");
    }
  }
}
