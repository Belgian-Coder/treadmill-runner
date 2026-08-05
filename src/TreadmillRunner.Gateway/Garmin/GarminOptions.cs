namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminOptions
{
  public const string SectionName = "GarminConnect";

  public string Provider { get; set; } = "Disabled";
  public bool ApprovedTrainingContract { get; set; }
  public string? ClientId { get; set; }
  public string? ClientSecret { get; set; }
  public string? AuthorizationEndpoint { get; set; }
  public string? TokenEndpoint { get; set; }
  public string? IdentityEndpoint { get; set; }
  public string? WorkoutEndpoint { get; set; }
  public string? TrainingPlanEndpoint { get; set; }
  public string? CalendarEndpoint { get; set; }
  public string? CallbackUri { get; set; }
  public string Scope { get; set; } = "training";
  public int RequestTimeoutSeconds { get; set; } = 20;
  public int FutureCalendarDays { get; set; } = 180;
}
