using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace TreadmillRunner.Infrastructure.Persistence.Migrations
{
  /// <inheritdoc />
  public partial class AddHeartRateControllerSettings : Migration
  {
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.AddColumn<int>(
          name: "HeartRateDecreaseCooldownSeconds",
          table: "UserProfiles",
          type: "INTEGER",
          nullable: false,
          defaultValue: 15);

      migrationBuilder.AddColumn<double>(
          name: "HeartRateDecreaseStepKph",
          table: "UserProfiles",
          type: "REAL",
          nullable: false,
          defaultValue: 0.5);

      migrationBuilder.AddColumn<int>(
          name: "HeartRateIncreaseCooldownSeconds",
          table: "UserProfiles",
          type: "INTEGER",
          nullable: false,
          defaultValue: 30);

      migrationBuilder.AddColumn<double>(
          name: "HeartRateIncreaseStepKph",
          table: "UserProfiles",
          type: "REAL",
          nullable: false,
          defaultValue: 0.2);

      migrationBuilder.AddCheckConstraint(
          name: "CK_UserProfiles_HrDecreaseCooldown",
          table: "UserProfiles",
          sql: "\"HeartRateDecreaseCooldownSeconds\" >= 5 AND \"HeartRateDecreaseCooldownSeconds\" <= 120");

      migrationBuilder.AddCheckConstraint(
          name: "CK_UserProfiles_HrDecreaseStep",
          table: "UserProfiles",
          sql: "\"HeartRateDecreaseStepKph\" >= 0.1 AND \"HeartRateDecreaseStepKph\" <= 1.0");

      migrationBuilder.AddCheckConstraint(
          name: "CK_UserProfiles_HrIncreaseCooldown",
          table: "UserProfiles",
          sql: "\"HeartRateIncreaseCooldownSeconds\" >= 15 AND \"HeartRateIncreaseCooldownSeconds\" <= 180");

      migrationBuilder.AddCheckConstraint(
          name: "CK_UserProfiles_HrIncreaseStep",
          table: "UserProfiles",
          sql: "\"HeartRateIncreaseStepKph\" >= 0.1 AND \"HeartRateIncreaseStepKph\" <= 0.5");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
      migrationBuilder.DropCheckConstraint(
          name: "CK_UserProfiles_HrDecreaseCooldown",
          table: "UserProfiles");

      migrationBuilder.DropCheckConstraint(
          name: "CK_UserProfiles_HrDecreaseStep",
          table: "UserProfiles");

      migrationBuilder.DropCheckConstraint(
          name: "CK_UserProfiles_HrIncreaseCooldown",
          table: "UserProfiles");

      migrationBuilder.DropCheckConstraint(
          name: "CK_UserProfiles_HrIncreaseStep",
          table: "UserProfiles");

      migrationBuilder.DropColumn(
          name: "HeartRateDecreaseCooldownSeconds",
          table: "UserProfiles");

      migrationBuilder.DropColumn(
          name: "HeartRateDecreaseStepKph",
          table: "UserProfiles");

      migrationBuilder.DropColumn(
          name: "HeartRateIncreaseCooldownSeconds",
          table: "UserProfiles");

      migrationBuilder.DropColumn(
          name: "HeartRateIncreaseStepKph",
          table: "UserProfiles");
    }
  }
}
