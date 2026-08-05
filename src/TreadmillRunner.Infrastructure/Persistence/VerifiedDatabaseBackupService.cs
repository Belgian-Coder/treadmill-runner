using System.Security.Cryptography;
using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record VerifiedDatabaseBackup(
  string FileName,
  long SizeBytes,
  string Sha256,
  DateTimeOffset CreatedAtUtc);

public interface IVerifiedDatabaseBackupService
{
  Task<int> CleanupStaleTemporaryFilesAsync(
    string backupRoot,
    TimeSpan minimumAge,
    CancellationToken cancellationToken = default);

  Task<VerifiedDatabaseBackup> CreateAsync(
    string backupRoot,
    int retentionCount,
    CancellationToken cancellationToken = default);
}

public sealed class VerifiedDatabaseBackupService(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
  TimeProvider timeProvider) : IVerifiedDatabaseBackupService
{
  private const long MaximumDatabaseBytes = 256L * 1024 * 1024;
  private const string BackupPrefix = "integrity-last-known-good-";
  private const string TemporaryPrefix = "integrity-backup-";

  public Task<int> CleanupStaleTemporaryFilesAsync(
    string backupRoot,
    TimeSpan minimumAge,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(backupRoot);
    cancellationToken.ThrowIfCancellationRequested();
    string root = Path.GetFullPath(backupRoot);
    if (!Directory.Exists(root)) return Task.FromResult(0);

    DateTimeOffset threshold = timeProvider.GetUtcNow() - minimumAge;
    var deleted = 0;
    try
    {
      foreach (string path in Directory.EnumerateFiles(root, $"{TemporaryPrefix}*.tmp", SearchOption.TopDirectoryOnly))
      {
        cancellationToken.ThrowIfCancellationRequested();
        var file = new FileInfo(path);
        if (file.LastWriteTimeUtc > threshold.UtcDateTime) continue;
        try
        {
          file.Delete();
          deleted++;
        }
        catch (IOException)
        {
          // A current or externally inspected temporary file remains recoverable for the next pass.
        }
        catch (UnauthorizedAccessException)
        {
          // Installation diagnostics can report an ACL problem separately.
        }
      }
    }
    catch (IOException)
    {
      // Missing or transiently unavailable backup storage leaves cleanup for the next pass.
    }
    catch (UnauthorizedAccessException)
    {
      // Installation diagnostics report ACL failures; cleanup must not fail the integrity check.
    }

    return Task.FromResult(deleted);
  }

  public async Task<VerifiedDatabaseBackup> CreateAsync(
    string backupRoot,
    int retentionCount,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(backupRoot);
    retentionCount = Math.Clamp(retentionCount, 2, 10);
    string root = Path.GetFullPath(backupRoot);
    Directory.CreateDirectory(root);
    DateTimeOffset createdAt = timeProvider.GetUtcNow();
    string suffix = $"{createdAt:yyyyMMdd-HHmmssfff}-{Guid.NewGuid():N}";
    string temporaryPath = Path.Combine(root, $"{TemporaryPrefix}{suffix}.tmp");
    string finalPath = Path.Combine(root, $"{BackupPrefix}{suffix}.db");

    try
    {
      await new SqliteOnlineBackupService(contextFactory).BackupAsync(temporaryPath, cancellationToken);
      var info = new FileInfo(temporaryPath);
      if (info.Length <= 0 || info.Length > MaximumDatabaseBytes)
      {
        throw new InvalidDataException("The automatic database backup is empty or exceeds 256 MiB.");
      }

      IDbContextFactory<TreadmillRunnerDbContext> candidateFactory =
        TreadmillRunnerDatabase.CreateFactory(temporaryPath, pooling: false);
      var checker = new DatabaseIntegrityChecker(candidateFactory, timeProvider);
      DatabaseIntegrityCheckResult verification = await checker.CheckAsync(
        DatabaseIntegrityCheckLevel.Full,
        cancellationToken);
      if (!verification.IsHealthy)
      {
        throw new InvalidDataException("The automatic database backup failed full integrity validation.");
      }

      byte[] hash;
      await using (FileStream input = new(
        temporaryPath,
        FileMode.Open,
        FileAccess.Read,
        FileShare.Read,
        bufferSize: 64 * 1024,
        FileOptions.Asynchronous | FileOptions.SequentialScan))
      {
        hash = await SHA256.HashDataAsync(input, cancellationToken);
      }

      File.Move(temporaryPath, finalPath);
      Prune(root, finalPath, retentionCount);
      return new VerifiedDatabaseBackup(
        Path.GetFileName(finalPath),
        info.Length,
        Convert.ToHexString(hash),
        createdAt);
    }
    finally
    {
      TryDeleteSidecar(temporaryPath);
      TryDeleteSidecar(temporaryPath + "-wal");
      TryDeleteSidecar(temporaryPath + "-shm");
    }
  }

  private static void Prune(string root, string currentPath, int retentionCount)
  {
    FileInfo[] backups;
    try
    {
      backups = Directory
        .EnumerateFiles(root, $"{BackupPrefix}*.db", SearchOption.TopDirectoryOnly)
        .Select(static path => new FileInfo(path))
        .OrderByDescending(static file => file.LastWriteTimeUtc)
        .ToArray();
    }
    catch (IOException) { return; }
    catch (UnauthorizedAccessException) { return; }
    foreach (FileInfo backup in backups.Skip(retentionCount))
    {
      if (string.Equals(backup.FullName, currentPath, StringComparison.OrdinalIgnoreCase)) continue;
      try { backup.Delete(); }
      catch (IOException)
      {
        // A locked older backup remains available and will be reconsidered on the next rotation.
      }
      catch (UnauthorizedAccessException)
      {
        // Backup promotion succeeded; an ACL issue must not make the verified current copy fail.
      }
    }
  }

  private static void TryDeleteSidecar(string path)
  {
    try { File.Delete(path); }
    catch (IOException)
    {
      // SQLite sidecars can remain briefly locked and are uniquely named for later cleanup.
    }
    catch (UnauthorizedAccessException)
    {
      // The promoted backup remains valid even when a stale temporary sidecar cannot be removed.
    }
  }
}
