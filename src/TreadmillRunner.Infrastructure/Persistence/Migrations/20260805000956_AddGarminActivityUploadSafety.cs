using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddGarminActivityUploadSafety : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<string>(
          name: "FailureKind",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          maxLength: 30,
          nullable: true);

      migrationBuilder.AddColumn<DateTimeOffset>(
          name: "UploadFromUtc",
          table: "GarminActivityUploadAccounts",
          type: "TEXT",
          nullable: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropColumn(
          name: "FailureKind",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropColumn(
          name: "UploadFromUtc",
          table: "GarminActivityUploadAccounts");
    }
  }
}
