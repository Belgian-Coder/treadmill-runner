namespace TreadmillRunner.Gateway.Operations;

public static class DatabaseIntegrityEndpoints
{
  public static IEndpointRouteBuilder MapDatabaseIntegrity(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/operations/database");
    group.MapGet("/status", static (IDatabaseIntegrityCoordinator coordinator) =>
      TypedResults.Ok(coordinator.Current));
    group.MapGet("/verified-backup", DownloadVerifiedBackupAsync);
    group.MapPost("/check", CheckNowAsync);
    return endpoints;
  }

  private static async Task<IResult> CheckNowAsync(
    IDatabaseIntegrityCoordinator coordinator,
    CancellationToken cancellationToken)
  {
    DatabaseIntegrityStatus status = await coordinator.CheckNowAsync(cancellationToken);
    return status.State == DatabaseIntegrityState.Deferred
      ? Results.Conflict(status)
      : Results.Ok(status);
  }

  private static async Task<IResult> DownloadVerifiedBackupAsync(
    IDatabaseIntegrityCoordinator coordinator,
    IConfiguration configuration,
    HttpContext context,
    CancellationToken cancellationToken)
  {
    string? fileName = coordinator.Current.LastBackupFileName;
    if (string.IsNullOrWhiteSpace(fileName) ||
        !string.Equals(fileName, Path.GetFileName(fileName), StringComparison.Ordinal) ||
        !fileName.StartsWith("integrity-last-known-good-", StringComparison.Ordinal) ||
        !fileName.EndsWith(".db", StringComparison.OrdinalIgnoreCase))
    {
      return Results.NotFound(new { error = "No verified last-known-good backup is available." });
    }

    string databasePath = configuration["Persistence:DatabasePath"]
      ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db");
    string root = Path.GetFullPath(configuration["Persistence:IntegrityBackupRoot"]
      ?? Path.Combine(Path.GetDirectoryName(Path.GetFullPath(databasePath))!, "backups", "integrity"));
    string path = Path.GetFullPath(Path.Combine(root, fileName));
    if (!path.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
    {
      return Results.NotFound(new { error = "The verified backup file is no longer available." });
    }

    var info = new FileInfo(path);
    if (info.Length is <= 0 or > 268_435_456)
    {
      return Results.Problem("The verified backup is empty or exceeds 256 MiB.", statusCode: 413);
    }

    context.Response.Headers.CacheControl = "no-store";
    byte[] content = await File.ReadAllBytesAsync(path, cancellationToken);
    return Results.File(content, "application/vnd.treadmillrunner.backup", $"verified-{fileName}.trb");
  }
}
