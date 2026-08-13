namespace TreadmillRunner.Gateway.Security;

public sealed class OperatorAccessOptions
{
  public const string SectionName = "OperatorAccess";
  public bool Enabled { get; set; }
  public string? SecretHash { get; set; }
  public int SessionMinutes { get; set; } = 30;
  public int MaximumFailedAttempts { get; set; } = 5;
  public int FailureWindowMinutes { get; set; } = 5;

  public static bool IsValid(OperatorAccessOptions options) =>
    !options.Enabled ||
    (OperatorAccessService.TryParseSecretHash(options.SecretHash, out _, out _, out _) &&
      options.SessionMinutes is >= 5 and <= 240 &&
      options.MaximumFailedAttempts is >= 3 and <= 20 &&
      options.FailureWindowMinutes is >= 1 and <= 60);
}
