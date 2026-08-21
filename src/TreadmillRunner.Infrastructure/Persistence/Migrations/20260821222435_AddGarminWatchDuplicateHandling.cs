using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddGarminWatchDuplicateHandling : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<string>(
          name: "MatchEvidence",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          maxLength: 1000,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "MatchedRemoteId",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          maxLength: 256,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "OperationPhase",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          maxLength: 30,
          nullable: false,
          defaultValue: "WatchSearch");

      migrationBuilder.AddColumn<string>(
          name: "ReplacementRemoteId",
          table: "GarminActivityUploadJobs",
          type: "TEXT",
          maxLength: 256,
          nullable: true);

      migrationBuilder.AddColumn<string>(
          name: "WatchActivityHandling",
          table: "GarminActivityUploadAccounts",
          type: "TEXT",
          maxLength: 30,
          nullable: false,
          defaultValue: "PreferWatch");

      migrationBuilder.AddCheckConstraint(
          name: "CK_GarminActivityUploadAccounts_WatchHandling",
          table: "GarminActivityUploadAccounts",
          sql: "\"WatchActivityHandling\" IN ('PreferWatch', 'MergeAndReplace')");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_GarminActivityUploadAccounts_WatchHandling",
          table: "GarminActivityUploadAccounts");

      migrationBuilder.DropColumn(
          name: "MatchEvidence",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropColumn(
          name: "MatchedRemoteId",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropColumn(
          name: "OperationPhase",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropColumn(
          name: "ReplacementRemoteId",
          table: "GarminActivityUploadJobs");

      migrationBuilder.DropColumn(
          name: "WatchActivityHandling",
          table: "GarminActivityUploadAccounts");
    }
  }
}
