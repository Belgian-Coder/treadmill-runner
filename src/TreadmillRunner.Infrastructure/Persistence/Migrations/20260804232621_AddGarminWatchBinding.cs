using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddGarminWatchBinding : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "GarminWatchBindings",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            DeviceLabel = table.Column<string>(type: "TEXT", maxLength: 100, nullable: false),
            TokenSha256 = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            LastSeenAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_GarminWatchBindings", x => x.Id);
            table.CheckConstraint("CK_GarminWatchBindings_Label", "length(\"DeviceLabel\") > 0");
            table.CheckConstraint("CK_GarminWatchBindings_Token", "length(\"TokenSha256\") = 64");
            table.CheckConstraint("CK_GarminWatchBindings_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_GarminWatchBindings_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_GarminWatchBindings_TokenSha256",
          table: "GarminWatchBindings",
          column: "TokenSha256",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminWatchBindings_UserProfileId",
          table: "GarminWatchBindings",
          column: "UserProfileId",
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "GarminWatchBindings");
    }
  }
}
