using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddGarminAccountSync : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.CreateTable(
          name: "GarminAccountLinks",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            ProviderSubject = table.Column<string>(type: "TEXT", maxLength: 256, nullable: false),
            AccountLabel = table.Column<string>(type: "TEXT", maxLength: 160, nullable: false),
            ProtectedAccessToken = table.Column<string>(type: "TEXT", maxLength: 8192, nullable: false),
            ProtectedRefreshToken = table.Column<string>(type: "TEXT", maxLength: 8192, nullable: true),
            AccessTokenExpiresAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            Scopes = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: false),
            ConnectedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            UpdatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            LastSyncAttemptAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            LastSyncSuccessAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: true),
            LastSyncError = table.Column<string>(type: "TEXT", maxLength: 1000, nullable: true),
            Version = table.Column<int>(type: "INTEGER", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_GarminAccountLinks", x => x.Id);
            table.CheckConstraint("CK_GarminAccountLinks_AccessToken", "length(\"ProtectedAccessToken\") > 0");
            table.CheckConstraint("CK_GarminAccountLinks_Label", "length(\"AccountLabel\") > 0");
            table.CheckConstraint("CK_GarminAccountLinks_Subject", "length(\"ProviderSubject\") > 0");
            table.CheckConstraint("CK_GarminAccountLinks_Version", "\"Version\" > 0");
            table.ForeignKey(
                      name: "FK_GarminAccountLinks_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "GarminOAuthStates",
          columns: table => new
          {
            StateHash = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            ProtectedCodeVerifier = table.Column<string>(type: "TEXT", maxLength: 4096, nullable: false),
            RedirectUri = table.Column<string>(type: "TEXT", maxLength: 2048, nullable: false),
            CreatedAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false),
            ExpiresAtUtc = table.Column<DateTimeOffset>(type: "TEXT", nullable: false)
          },
          constraints: table =>
          {
            table.PrimaryKey("PK_GarminOAuthStates", x => x.StateHash);
            table.CheckConstraint("CK_GarminOAuthStates_Expiry", "\"ExpiresAtUtc\" > \"CreatedAtUtc\"");
            table.CheckConstraint("CK_GarminOAuthStates_Hash", "length(\"StateHash\") = 64");
            table.CheckConstraint("CK_GarminOAuthStates_Verifier", "length(\"ProtectedCodeVerifier\") > 0");
            table.ForeignKey(
                      name: "FK_GarminOAuthStates_UserProfiles_UserProfileId",
                      column: x => x.UserProfileId,
                      principalTable: "UserProfiles",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateTable(
          name: "GarminSyncItems",
          columns: table => new
          {
            Id = table.Column<Guid>(type: "TEXT", nullable: false),
            UserProfileId = table.Column<Guid>(type: "TEXT", nullable: false),
            GarminAccountLinkId = table.Column<Guid>(type: "TEXT", nullable: false),
            Kind = table.Column<string>(type: "TEXT", maxLength: 30, nullable: false),
            SourceId = table.Column<Guid>(type: "TEXT", nullable: false),
            SourceVersion = table.Column<string>(type: "TEXT", maxLength: 128, nullable: false),
            IdempotencyKey = table.Column<string>(type: "TEXT", fixedLength: true, maxLength: 64, nullable: false),
            PayloadJson = table.Column<string>(type: "TEXT", nullable: false),
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
            table.PrimaryKey("PK_GarminSyncItems", x => x.Id);
            table.CheckConstraint("CK_GarminSyncItems_Attempts", "\"AttemptCount\" >= 0");
            table.CheckConstraint("CK_GarminSyncItems_Key", "length(\"IdempotencyKey\") = 64");
            table.CheckConstraint("CK_GarminSyncItems_Kind", "\"Kind\" IN ('Workout', 'TrainingPlan', 'Calendar')");
            table.CheckConstraint("CK_GarminSyncItems_Status", "\"Status\" IN ('Pending', 'InFlight', 'Synced', 'Failed')");
            table.ForeignKey(
                      name: "FK_GarminSyncItems_GarminAccountLinks_GarminAccountLinkId",
                      column: x => x.GarminAccountLinkId,
                      principalTable: "GarminAccountLinks",
                      principalColumn: "Id",
                      onDelete: ReferentialAction.Cascade);
          });

      migrationBuilder.CreateIndex(
          name: "IX_GarminAccountLinks_ProviderSubject",
          table: "GarminAccountLinks",
          column: "ProviderSubject",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminAccountLinks_UserProfileId",
          table: "GarminAccountLinks",
          column: "UserProfileId",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminOAuthStates_ExpiresAtUtc",
          table: "GarminOAuthStates",
          column: "ExpiresAtUtc");

      migrationBuilder.CreateIndex(
          name: "IX_GarminOAuthStates_UserProfileId",
          table: "GarminOAuthStates",
          column: "UserProfileId");

      migrationBuilder.CreateIndex(
          name: "IX_GarminSyncItems_GarminAccountLinkId",
          table: "GarminSyncItems",
          column: "GarminAccountLinkId");

      migrationBuilder.CreateIndex(
          name: "IX_GarminSyncItems_IdempotencyKey",
          table: "GarminSyncItems",
          column: "IdempotencyKey",
          unique: true);

      migrationBuilder.CreateIndex(
          name: "IX_GarminSyncItems_Status_AvailableAtUtc",
          table: "GarminSyncItems",
          columns: new[] { "Status", "AvailableAtUtc" });

      migrationBuilder.CreateIndex(
          name: "IX_GarminSyncItems_UserProfileId_Kind_SourceId",
          table: "GarminSyncItems",
          columns: new[] { "UserProfileId", "Kind", "SourceId" });
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropTable(
          name: "GarminOAuthStates");

      migrationBuilder.DropTable(
          name: "GarminSyncItems");

      migrationBuilder.DropTable(
          name: "GarminAccountLinks");
    }
  }
}
