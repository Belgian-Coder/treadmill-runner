using System.Text.Json;
using System.Text.Json.Serialization;

namespace TreadmillRunner.Gateway.Operations;

[JsonConverter(typeof(JsonStringEnumConverter<DatabaseIntegrityState>))]
public enum DatabaseIntegrityState
{
  NotChecked,
  Checking,
  Deferred,
  Healthy,
  HealthyWithBackupWarning,
  Unhealthy,
}

public sealed record DatabaseIntegrityStatus(
  DatabaseIntegrityState State,
  string Message,
  DateTimeOffset UpdatedAtUtc,
  DateTimeOffset? LastQuickCheckAtUtc,
  DateTimeOffset? LastFullCheckAtUtc,
  DateTimeOffset? LastHealthyAtUtc,
  DateTimeOffset? LastMaintenanceAtUtc,
  DateTimeOffset? LastBackupAtUtc,
  string? LastBackupFileName,
  string? LastBackupSha256,
  DateTimeOffset? NextCheckAtUtc,
  bool RecoveryRequired,
  IReadOnlyList<string> Issues)
{
  public static DatabaseIntegrityStatus Initial(DateTimeOffset nowUtc) => new(
    DatabaseIntegrityState.NotChecked,
    "No database integrity check has completed yet.",
    nowUtc,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    false,
    []);
}

public interface IDatabaseIntegrityStatusStore
{
  DatabaseIntegrityStatus Current { get; }
  Task SaveAsync(DatabaseIntegrityStatus status, CancellationToken cancellationToken = default);
}

public sealed class DatabaseIntegrityStatusStore : IDatabaseIntegrityStatusStore
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
  {
    WriteIndented = true,
  };

  private readonly string _statusPath;
  private readonly ILogger<DatabaseIntegrityStatusStore> _logger;
  private DatabaseIntegrityStatus _current;

  public DatabaseIntegrityStatusStore(
    IConfiguration configuration,
    TimeProvider timeProvider,
    ILogger<DatabaseIntegrityStatusStore> logger)
  {
    _logger = logger;
    string databasePath = configuration["Persistence:DatabasePath"]
      ?? Path.Combine(AppContext.BaseDirectory, "data", "treadmillrunner.db");
    _statusPath = Path.GetFullPath(configuration["Persistence:IntegrityStatusPath"]
      ?? Path.Combine(Path.GetDirectoryName(Path.GetFullPath(databasePath))!, "database-integrity-status.json"));
    _current = Load(_statusPath) ?? DatabaseIntegrityStatus.Initial(timeProvider.GetUtcNow());
  }

  public DatabaseIntegrityStatus Current => Volatile.Read(ref _current);

  public async Task SaveAsync(
    DatabaseIntegrityStatus status,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(status);
    Volatile.Write(ref _current, status);
    string directory = Path.GetDirectoryName(_statusPath)!;
    Directory.CreateDirectory(directory);
    string temporaryPath = Path.Combine(directory, $".database-integrity-{Guid.NewGuid():N}.tmp");
    try
    {
      await using (var output = new FileStream(
        temporaryPath,
        FileMode.CreateNew,
        FileAccess.Write,
        FileShare.None,
        bufferSize: 16 * 1024,
        FileOptions.Asynchronous | FileOptions.WriteThrough))
      {
        await JsonSerializer.SerializeAsync(output, status, JsonOptions, cancellationToken);
        await output.FlushAsync(cancellationToken);
      }

      File.Move(temporaryPath, _statusPath, overwrite: true);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
    {
      _logger.LogWarning(exception, "The database integrity sidecar status could not be persisted.");
    }
    finally
    {
      try { File.Delete(temporaryPath); }
      catch (IOException)
      {
        // A later integrity pass can safely replace this uniquely named sidecar temp file.
      }
      catch (UnauthorizedAccessException)
      {
        // The primary status is already retained in memory; installation diagnostics report ACL issues.
      }
    }
  }

  private static DatabaseIntegrityStatus? Load(string path)
  {
    try
    {
      if (!File.Exists(path)) return null;
      using FileStream input = File.OpenRead(path);
      return JsonSerializer.Deserialize<DatabaseIntegrityStatus>(input, JsonOptions);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
    {
      return null;
    }
  }
}
