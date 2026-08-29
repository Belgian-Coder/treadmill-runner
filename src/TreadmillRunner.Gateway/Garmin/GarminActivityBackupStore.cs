using System.Security.Cryptography;

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

  /// <summary>
  /// Retains the deterministic local-session FIT alongside the watch-original
  /// and merged-replacement backups.  Undo uses this copy as the authoritative
  /// proof for the one plain TreadmillRunner activity that must remain.
  /// </summary>
  public async Task BackupLocalAsync(Guid jobId, string sourcePath, CancellationToken cancellationToken)
  {
    await BackupAsync(LocalPath(jobId), sourcePath, cancellationToken);
    PruneExpired();
  }

  public bool HasOriginal(Guid jobId, string remoteId)
  {
    string path = OriginalPath(jobId, remoteId);
    return File.Exists(path) && new FileInfo(path).Length > 0;
  }

  public bool MatchesOriginal(Guid jobId, string originalRemoteId, string candidatePath)
  {
    string originalPath = OriginalPath(jobId, originalRemoteId);
    return FilesMatch(originalPath, candidatePath);
  }

  public bool MatchesReplacement(Guid jobId, string originalRemoteId, string candidatePath)
  {
    string replacementPath = ReplacementPath(jobId, originalRemoteId);
    return FilesMatch(replacementPath, candidatePath);
  }

  public bool MatchesLocal(Guid jobId, string candidatePath) =>
    FilesMatch(LocalPath(jobId), candidatePath);

  public bool MatchesRecoveryOriginal(Guid jobId, string candidatePath) =>
    TryFindRecoveryOriginalRemoteId(jobId, out string? backupRemoteId) &&
    !string.IsNullOrWhiteSpace(backupRemoteId) &&
    MatchesOriginal(jobId, backupRemoteId, candidatePath);

  public bool MatchesRecoveryReplacement(Guid jobId, string candidatePath) =>
    TryFindRecoveryOriginalRemoteId(jobId, out string? backupRemoteId) &&
    !string.IsNullOrWhiteSpace(backupRemoteId) &&
    MatchesReplacement(jobId, backupRemoteId, candidatePath);

  public bool TryGetOriginalPath(Guid jobId, string originalRemoteId, out string? path) =>
    TryGetRetainedPath(OriginalPath(jobId, originalRemoteId), out path);

  public bool TryGetReplacementPath(Guid jobId, string originalRemoteId, out string? path) =>
    TryGetRetainedPath(ReplacementPath(jobId, originalRemoteId), out path);

  public bool TryGetLocalPath(Guid jobId, out string? path) =>
    TryGetRetainedPath(LocalPath(jobId), out path);

  public bool TryGetRecoveryOriginalPath(Guid jobId, out string? path)
  {
    path = null;
    return TryFindRecoveryOriginalRemoteId(jobId, out string? backupRemoteId) &&
      !string.IsNullOrWhiteSpace(backupRemoteId) &&
      TryGetOriginalPath(jobId, backupRemoteId, out path);
  }

  public bool TryGetRecoveryReplacementPath(Guid jobId, out string? path)
  {
    path = null;
    return TryFindRecoveryOriginalRemoteId(jobId, out string? backupRemoteId) &&
      !string.IsNullOrWhiteSpace(backupRemoteId) &&
      TryGetReplacementPath(jobId, backupRemoteId, out path);
  }

  public bool HasLocal(Guid jobId)
  {
    string path = LocalPath(jobId);
    return File.Exists(path) && new FileInfo(path).Length > 0;
  }

  public bool TryFindRecoveryOriginalRemoteId(Guid jobId, out string? originalRemoteId)
  {
    originalRemoteId = null;
    if (!Directory.Exists(root)) return false;

    string suffix = $"_{jobId:N}_original.fit";
    string[] matches;
    try
    {
      matches = Directory.EnumerateFiles(root, $"*{suffix}", SearchOption.TopDirectoryOnly)
        .Where(path => new FileInfo(path).Length > 0)
        .Where(path => TryGetRetainedPath(
          Path.Combine(root, $"{Path.GetFileName(path)[..^suffix.Length]}_{jobId:N}_replacement.fit"),
          out _))
        .Take(2)
        .ToArray();
    }
    catch (IOException) { return false; }
    catch (UnauthorizedAccessException) { return false; }

    if (matches.Length != 1) return false;
    string fileName = Path.GetFileName(matches[0]);
    originalRemoteId = fileName[..^suffix.Length];
    return !string.IsNullOrWhiteSpace(originalRemoteId);
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

  private string ReplacementPath(Guid jobId, string originalRemoteId) =>
    Path.Combine(root, $"{Safe(originalRemoteId)}_{jobId:N}_replacement.fit");

  private string LocalPath(Guid jobId) =>
    Path.Combine(root, $"{jobId:N}_local.fit");

  private static bool FilesMatch(string expectedPath, string candidatePath)
  {
    if (!File.Exists(expectedPath) || !File.Exists(candidatePath)) return false;
    byte[] expectedHash = SHA256.HashData(File.ReadAllBytes(expectedPath));
    byte[] candidateHash = SHA256.HashData(File.ReadAllBytes(candidatePath));
    return CryptographicOperations.FixedTimeEquals(expectedHash, candidateHash);
  }

  private static bool TryGetRetainedPath(string candidatePath, out string? path)
  {
    path = File.Exists(candidatePath) && new FileInfo(candidatePath).Length > 0 ? candidatePath : null;
    return path is not null;
  }

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
