namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminActivityBackupStore(IConfiguration configuration, TimeProvider timeProvider)
{
  private static readonly TimeSpan Retention = TimeSpan.FromDays(7);
  private readonly string root = ResolveRoot(configuration);

  public async Task BackupOriginalAsync(Guid jobId, string remoteId, string sourcePath, CancellationToken cancellationToken)
  {
    await BackupAsync(OriginalPath(jobId, remoteId), sourcePath, cancellationToken);
    PruneExpired();
  }

  public async Task BackupReplacementAsync(Guid jobId, string originalRemoteId, string sourcePath, CancellationToken cancellationToken)
  {
    await BackupAsync(Path.Combine(root, $"{Safe(originalRemoteId)}_{jobId:N}_replacement.fit"), sourcePath, cancellationToken);
    PruneExpired();
  }

  public bool HasOriginal(Guid jobId, string remoteId)
  {
    string path = OriginalPath(jobId, remoteId);
    return File.Exists(path) && new FileInfo(path).Length > 0;
  }

  private async Task BackupAsync(string destinationPath, string sourcePath, CancellationToken cancellationToken)
  {
    Directory.CreateDirectory(root);
    string temporaryPath = destinationPath + $".{Guid.NewGuid():N}.tmp";
    try
    {
      await using (FileStream source = File.OpenRead(sourcePath))
      await using (FileStream destination = new(temporaryPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, 81920, FileOptions.Asynchronous | FileOptions.WriteThrough))
        await source.CopyToAsync(destination, cancellationToken);
      File.Move(temporaryPath, destinationPath, true);
    }
    finally
    {
      if (File.Exists(temporaryPath)) File.Delete(temporaryPath);
    }
  }

  private void PruneExpired()
  {
    if (!Directory.Exists(root)) return;
    DateTimeOffset cutoff = timeProvider.GetUtcNow() - Retention;
    foreach (string path in Directory.EnumerateFiles(root, "*.fit", SearchOption.TopDirectoryOnly))
    {
      try
      {
        if (File.GetLastWriteTimeUtc(path) < cutoff.UtcDateTime) File.Delete(path);
      }
      catch (IOException) { }
      catch (UnauthorizedAccessException) { }
    }
  }

  private string OriginalPath(Guid jobId, string remoteId) =>
    Path.Combine(root, $"{Safe(remoteId)}_{jobId:N}_original.fit");

  private static string Safe(string remoteId)
  {
    string value = string.Concat(remoteId.Where(character => char.IsAsciiLetterOrDigit(character) || character is '-' or '_'));
    if (string.IsNullOrWhiteSpace(value)) throw new ArgumentException("A Garmin activity ID is required.", nameof(remoteId));
    return value;
  }

  private static string ResolveRoot(IConfiguration configuration)
  {
    string? configured = configuration["GarminActivityUpload:BackupRoot"];
    if (!string.IsNullOrWhiteSpace(configured)) return Path.GetFullPath(configured);
    string databasePath = Path.GetFullPath(configuration["Persistence:DatabasePath"]
      ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db"));
    string dataDirectory = Path.GetDirectoryName(databasePath)!;
    string applicationDirectory = string.Equals(Path.GetFileName(dataDirectory), "data", StringComparison.OrdinalIgnoreCase)
      ? Directory.GetParent(dataDirectory)?.FullName ?? dataDirectory
      : dataDirectory;
    return Path.Combine(applicationDirectory, "backups", "garmin-source");
  }
}
