using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddGarminActivityUpload : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "GarminActivityUploadAccounts",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            AccountLabel = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            ProtectedTokenStore = table.Column<string>(type: "TEXT", maxLength: 32768, nullable: false),
            Enabled = table.Column<bool>(type: "INTEGER", nullable: false),
            State = table.Column<string>(type: "TEXT", maxLength: 30, nullable: false),
            ConnectedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            LastUploadSuccessAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            LastError = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_GarminActivityUploadAccounts", x => x.Id);
            table.CheckConstraint("CK_GarminActivityUploadAccounts_Label", "length(\"AccountLabel\") > 0");
            table.CheckConstraint("CK_GarminActivityUploadAccounts_State", "\"State\" IN ('Connected', 'NeedsAuthentication', 'ProviderUnavailable')");
            table.CheckConstraint("CK_GarminActivityUploadAccounts_Tokens", "length(\"ProtectedTokenStore\") > 0");
            table.CheckConstraint("CK_GarminActivityUploadAccounts_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_GarminActivityUploadAccounts_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "GarminActivityUploadJobs",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            GarminActivityUploadAccountId = table.Column<Guid>(type: "TEXT", nullable: false),
            WorkoutSessionId = table.Column<Guid>(type: "TEXT", nullable: false),
            IdempotencyKey = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            Status = table.Column<string>(type: "TEXT", maxLength: 20, nullable: false),
            AttemptCount = table.Column<int>(type: "INTEGER", nullable: false),
            AvailableAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            LeaseExpiresAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            RemoteId = table.Column<string>(type: "TEXT", maxLength: 256, nullable: true),
            LastError = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: true),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_GarminActivityUploadJobs", x => x.Id);
            table.CheckConstraint("CK_GarminActivityUploadJobs_Attempts", "\"AttemptCount\" >= 0 AND \"AttemptCount\" <= 3");
            table.CheckConstraint("CK_GarminActivityUploadJobs_Key", "length(\"IdempotencyKey\") = 64");
            table.CheckConstraint("CK_GarminActivityUploadJobs_Status", "\"Status\" IN ('Pending', 'InFlight', 'Confirmed', 'Failed', 'Unknown', 'Dismissed')");
            table.ForeignKey(
                      name: "FK_GarminActivityUploadJobs_GarminActivityUploadAccounts_GarminActivityUploadAccountId",
                      column: x => x.GarminActivityUploadAccountId,
                      principalTable: "GarminActivityUploadAccounts",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
            table.ForeignKey(
                      name: "FK_GarminActivityUploadJobs_WorkoutSessions_WorkoutSessionId",
                      column: x => x.WorkoutSessionId,
                      principalTable: "WorkoutSessions",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadAccounts_UserProfileId",
          table: "GarminActivityUploadAccounts",
          column: "UserProfileId",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadJobs_GarminActivityUploadAccountId",
          table: "GarminActivityUploadJobs",
          column: "GarminActivityUploadAccountId");

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadJobs_IdempotencyKey",
          table: "GarminActivityUploadJobs",
          column: "IdempotencyKey",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadJobs_Status_AvailableAtUtc",
          table: "GarminActivityUploadJobs",
          columns: new[] { "Status", "AvailableAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_GarminActivityUploadJobs_WorkoutSessionId",
          table: "GarminActivityUploadJobs",
          column: "WorkoutSessionId",
          unique: true);
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "GarminActivityUploadJobs");

      migrationBuilder.DropTable(
          name: "GarminActivityUploadAccounts");
    }
  }
}
