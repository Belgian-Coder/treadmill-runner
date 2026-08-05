using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.IO.Compression;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.Gateway.Operations;

public sealed record RestorePreview(
  Guid Token,
  long SizeBytes,
  string Sha256,
  int AppliedMigrationCount,
  int ProfileCount,
  int WorkoutCount,
  int SessionCount,
  DateTimeOffset ExpiresAtUtc);

public sealed record ConfirmRestoreRequest(Guid Token, string Confirmation);

public sealed class RestorePreviewStore(TimeProvider timeProvider)
{
  private readonly ConcurrentDictionary<Guid, StoredPreview> _previews = new();

  public RestorePreview Add(string path, RestorePreview preview)
  {
    RemoveExpired();
    _previews[preview.Token] = new StoredPreview(path, preview);
    return preview;
  }

  public StoredPreview Take(Guid token)
  {
    RemoveExpired();
    return _previews.TryRemove(token, out StoredPreview? preview)
      ? preview
      : throw new KeyNotFoundException("The restore preview expired or was already consumed.");
  }

  private void RemoveExpired()
  {
    DateTimeOffset now = timeProvider.GetUtcNow();
    foreach ((Guid token, StoredPreview preview) in _previews)
    {
      if (preview.Preview.ExpiresAtUtc > now || !_previews.TryRemove(token, out _)) continue;
      if (File.Exists(preview.Path)) File.Delete(preview.Path);
    }
  }

  public sealed record StoredPreview(string Path, RestorePreview Preview);
}

public static class DataRecoveryEndpoints
{
  private const long MaximumDatabaseBytes = 256L * 1024 * 1024;

  public static IEndpointRouteBuilder MapDataRecovery(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/operations");
    group.MapAppAccess();
    group.MapGet("/backup", DownloadBackupAsync);
    group.MapPost("/restore/preview", PreviewRestoreAsync).DisableAntiforgery();
    group.MapPost("/restore/confirm", ConfirmRestoreAsync);
    group.MapGet("/diagnostics", DownloadDiagnosticsAsync);
    return endpoints;
  }

  private static async Task<IResult> DownloadDiagnosticsAsync(
    HttpContext httpContext,
    ILiveSessionCoordinator live,
    IReadOnlyDeviceCoordinator devices,
    IBleReliabilityStore reliability,
    IDatabaseIntegrityCoordinator databaseIntegrity,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    httpContext.Response.Headers.CacheControl = "no-store";
    DateTimeOffset capturedAt = timeProvider.GetUtcNow();
    IReadOnlyList<TreadmillRunner.Core.Devices.BleReliabilityIncident> incidents =
      await reliability.ListSinceAsync(capturedAt.AddDays(-7), maximumCount: 1000, cancellationToken);
    using var output = new MemoryStream();
    using (var archive = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
      ZipArchiveEntry entry = archive.CreateEntry("diagnostics.json", CompressionLevel.SmallestSize);
      using Stream stream = entry.Open();
      JsonSerializer.Serialize(stream, new
      {
        SchemaVersion = 2,
        CapturedAtUtc = capturedAt,
        Version = typeof(DataRecoveryEndpoints).Assembly.GetName().Version?.ToString(),
        Treadmill = new
        {
          devices.Current.Treadmill.DisplayName,
          devices.Current.Treadmill.ProtocolId,
          devices.Current.Treadmill.TelemetryMode,
          devices.Current.Treadmill.State,
          devices.Current.Treadmill.ConnectionGeneration,
          TelemetryAgeMilliseconds = devices.Current.TreadmillAge?.TotalMilliseconds,
        },
        HeartRate = new
        {
          devices.Current.HeartRate.DisplayName,
          devices.Current.HeartRate.ProtocolId,
          devices.Current.HeartRate.State,
          devices.Current.HeartRate.ConnectionGeneration,
          TelemetryAgeMilliseconds = devices.Current.HeartRateAge?.TotalMilliseconds,
          devices.Current.SelectedHeartRateBatteryPercent,
          devices.Current.SelectedHeartRateBatteryObservedAt,
        },
        DatabaseIntegrity = databaseIntegrity.Current,
        BluetoothReliability = new
        {
          WindowDays = 7,
          IncidentCount = incidents.Count,
          OpenIncidentCount = incidents.Count(incident => incident.RecoveredAtUtc is null),
          Incidents = incidents.Select(incident => new
          {
            Role = incident.Role.ToString(),
            incident.DeviceDisplayName,
            incident.StartedAtUtc,
            incident.RecoveredAtUtc,
            incident.FailedAttemptCount,
            FailureKind = incident.FailureKind.ToString(),
            incident.LastSanitizedFault,
            incident.MaximumReconnectDelaySeconds,
          }),
        },
        Session = live.CurrentSession is { } session
          ? new
          {
            session.SessionId,
            session.Live.SessionState,
            session.Version,
            session.HeartRateAutomationMode,
            session.Warnings,
            LastCommand = session.LastCommandResult,
          }
          : null,
      }, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true });
    }
    byte[] bundle = output.ToArray();
    if (bundle.Length > 5 * 1024 * 1024)
      return Results.Problem("The diagnostic bundle exceeded 5 MiB.", statusCode: 413);
    return Results.File(bundle, "application/zip", $"treadmillrunner-diagnostics-{DateTime.UtcNow:yyyyMMdd-HHmmss}.zip");
  }

  private static async Task<IResult> DownloadBackupAsync(
    ILiveSessionCoordinator live,
    SqliteOnlineBackupService backup,
    CancellationToken cancellationToken)
  {
    if (!IsIdle(live.CurrentSession)) return Results.Conflict(new { error = "Backup download requires an idle session." });
    string path = Path.Combine(Path.GetTempPath(), $"treadmillrunner-backup-{Guid.NewGuid():N}.db");
    try
    {
      await backup.BackupAsync(path, cancellationToken);
      var info = new FileInfo(path);
      if (info.Length > MaximumDatabaseBytes)
        return Results.Problem("The database exceeds the 256 MiB backup limit.", statusCode: 413);
      byte[] content = await File.ReadAllBytesAsync(path, cancellationToken);
      return Results.File(content, "application/vnd.treadmillrunner.backup", $"treadmillrunner-{DateTime.UtcNow:yyyyMMdd-HHmmss}.trb");
    }
    finally
    {
      if (File.Exists(path)) File.Delete(path);
    }
  }

  private static async Task<IResult> PreviewRestoreAsync(
    HttpRequest request,
    ILiveSessionCoordinator live,
    RestorePreviewStore previews,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (!IsIdle(live.CurrentSession)) return Results.Conflict(new { error = "Restore preview requires an idle session." });
    if (request.ContentLength is null or <= 0 || request.ContentLength > MaximumDatabaseBytes)
      return Results.BadRequest(new { error = "A non-empty SQLite backup no larger than 256 MiB is required." });

    string path = Path.Combine(Path.GetTempPath(), $"treadmillrunner-preview-{Guid.NewGuid():N}.db");
    try
    {
      await using (var output = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.Asynchronous))
      {
        await request.Body.CopyToAsync(output, cancellationToken);
      }
      var info = new FileInfo(path);
      if (info.Length <= 0 || info.Length > MaximumDatabaseBytes)
        throw new InvalidDataException("The uploaded backup is empty or exceeds 256 MiB.");
      byte[] header = new byte[16];
      await using (var input = File.OpenRead(path))
      {
        int read = await input.ReadAsync(header, cancellationToken);
        if (read != header.Length || !header.SequenceEqual("SQLite format 3\0"u8.ToArray()))
          throw new InvalidDataException("The uploaded file is not a SQLite database.");
      }

      IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(
        path,
        pooling: false);
      int migrations;
      int profiles;
      int workouts;
      int sessions;
      await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync(cancellationToken))
      {
        if (!string.Equals(await SqliteRestoreService.IntegrityAsync(context, cancellationToken), "ok", StringComparison.OrdinalIgnoreCase))
          throw new InvalidDataException("The uploaded database failed SQLite integrity validation.");
        migrations = (await context.Database.GetAppliedMigrationsAsync(cancellationToken)).Count();
        profiles = await CountAsync(context, "UserProfiles", cancellationToken);
        workouts = await CountAsync(context, "Workouts", cancellationToken);
        sessions = await CountAsync(context, "WorkoutSessions", cancellationToken);
      }
      await using FileStream hashStream = File.OpenRead(path);
      byte[] hash = await SHA256.HashDataAsync(hashStream, cancellationToken);
      var preview = new RestorePreview(
        Guid.NewGuid(),
        info.Length,
        Convert.ToHexString(hash),
        migrations,
        profiles,
        workouts,
        sessions,
        timeProvider.GetUtcNow().AddMinutes(15));
      previews.Add(path, preview);
      path = string.Empty;
      return Results.Ok(preview);
    }
    catch (Exception exception) when (exception is InvalidDataException or Microsoft.Data.Sqlite.SqliteException)
    {
      return Results.BadRequest(new { error = exception.Message });
    }
    finally
    {
      if (!string.IsNullOrEmpty(path) && File.Exists(path)) File.Delete(path);
    }
  }

  private static async Task<IResult> ConfirmRestoreAsync(
    ConfirmRestoreRequest request,
    ILiveSessionCoordinator live,
    IReadOnlyDeviceCoordinator devices,
    RestorePreviewStore previews,
    SqliteRestoreService restore,
    IDatabaseIntegrityCoordinator databaseIntegrity,
    CancellationToken cancellationToken)
  {
    if (!string.Equals(request.Confirmation, "RESTORE", StringComparison.Ordinal))
      return Results.BadRequest(new { error = "Confirmation must exactly equal RESTORE." });

    if (!await live.TryBeginMaintenanceAsync(cancellationToken))
      return Results.Conflict(new { error = "Restore requires an idle session and no other maintenance operation." });

    RestorePreviewStore.StoredPreview preview;
    try
    {
      preview = previews.Take(request.Token);
    }
    catch (KeyNotFoundException exception)
    {
      await live.CancelMaintenanceAsync(CancellationToken.None);
      return Results.NotFound(new { error = exception.Message });
    }

    bool restored = false;
    try
    {
      await restore.RestoreAsync(preview.Path, cancellationToken);
      restored = true;
      await devices.RefreshAsync(CancellationToken.None);
      await live.ResetAsync(CancellationToken.None);
    }
    finally
    {
      if (File.Exists(preview.Path)) File.Delete(preview.Path);
      await live.CancelMaintenanceAsync(CancellationToken.None);
    }

    DatabaseIntegrityStatus integrity = await databaseIntegrity.CheckNowAsync(cancellationToken);
    return Results.Ok(new
    {
      restored,
      preview = preview.Preview,
      stateReloaded = true,
      databaseIntegrity = integrity,
    });
  }

  private static bool IsIdle(ActiveSessionSnapshot? session) => session is null || session.Live.SessionState is
    SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted;

  private static async Task<int> CountAsync(
    TreadmillRunnerDbContext context,
    string table,
    CancellationToken cancellationToken)
  {
    await context.Database.OpenConnectionAsync(cancellationToken);
    await using var command = context.Database.GetDbConnection().CreateCommand();
    command.CommandText = $"SELECT COUNT(*) FROM \"{table}\";";
    return Convert.ToInt32(await command.ExecuteScalarAsync(cancellationToken), System.Globalization.CultureInfo.InvariantCulture);
  }
}
