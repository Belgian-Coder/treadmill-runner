using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Updates;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Infrastructure.Updates;

namespace TreadmillRunner.Gateway.Updates;

public sealed record StagedUpdate(
  string Version,
  string StagePath,
  string PackagePath,
  string ManifestPath,
  DateTimeOffset StagedAtUtc,
  string ReleaseNotes);

public sealed record StageUpdateRequest(string ExpectedVersion);
public sealed record ActivateUpdateRequest(string Confirmation, string ExpectedVersion);

public enum UpdateLifecycleState
{
  NotChecked,
  Current,
  Available,
  Rejected,
  Unavailable,
  Staged,
  Activating,
  Activated,
  RolledBack,
  Failed,
}

public sealed record UpdateStatusSnapshot(
  UpdateLifecycleState State,
  string CurrentVersion,
  string? AvailableVersion,
  string? StagedVersion,
  string? ReleaseNotes,
  DateTimeOffset? LastCheckedAtUtc,
  string Message,
  string? FeedSource = null);

public sealed class UpdateManager(
  IConfiguration configuration,
  TimeProvider timeProvider,
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
  SqliteOnlineBackupService databaseBackup,
  UpdateFeedFactory? updateFeedFactory = null)
{
  public const long MaximumUploadedBundleBytes = ReleaseVerifier.MaximumPackageBytes + (2L * 1024 * 1024);
  private readonly SemaphoreSlim _gate = new(1, 1);
  private readonly UpdateFeedFactory _updateFeedFactory = updateFeedFactory ?? new UpdateFeedFactory(configuration);
  private StagedUpdate? _staged;
  private UpdateStatusSnapshot _status = new(
    UpdateLifecycleState.NotChecked,
    CurrentVersion(),
    null,
    null,
    null,
    null,
    "No update check has run yet.");

  public StagedUpdate? Staged => Volatile.Read(ref _staged);
  public UpdateStatusSnapshot Status => Volatile.Read(ref _status);

  public async Task<ReleaseValidationResult> CheckAsync(CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      (IUpdateFeed feed, ReleaseVerifier verifier, string channel) = CreateServices();
      IUpdateFeedRelease? feedRelease = await feed.ReadLatestReleaseAsync(channel, cancellationToken);
      ReleaseValidationResult result;
      if (feedRelease is null)
      {
        result = new ReleaseValidationResult(ReleaseValidationStatus.NotNewer, "No release manifest is available.");
      }
      else
      {
        UpdateCheckContext context = await ContextAsync(channel, cancellationToken);
        result = verifier.VerifyManifest(feedRelease.ManifestContent.Span, context);
        if (result is { IsValid: true, Manifest: not null } && Staged is null)
        {
          StagedUpdate? adopted = await TryAdoptExistingStageAsync(
            verifier,
            context,
            result.Manifest,
            cancellationToken);
          if (adopted is not null) Volatile.Write(ref _staged, adopted);
        }
      }

      StagedUpdate? staged = Staged;
      UpdateLifecycleState state = staged is not null
          ? UpdateLifecycleState.Staged
          : result.Status switch
          {
            ReleaseValidationStatus.Valid => UpdateLifecycleState.Available,
            ReleaseValidationStatus.NotNewer => UpdateLifecycleState.Current,
            _ => UpdateLifecycleState.Rejected,
          };
      UpdateManifest? manifest = result.Manifest;
      UpdateTransactionJournal? journal = ReadLatestJournal();
      if (manifest is not null && string.Equals(journal?.Version, manifest.Version, StringComparison.Ordinal))
      {
        state = journal!.State switch
        {
          "Activated" => UpdateLifecycleState.Activated,
          "RolledBack" => UpdateLifecycleState.RolledBack,
          "RollbackFailed" => UpdateLifecycleState.Failed,
          _ => state,
        };
      }
      Volatile.Write(ref _status, Status with
      {
        State = state,
        AvailableVersion = staged?.Version ?? manifest?.Version,
        StagedVersion = staged?.Version,
        ReleaseNotes = staged?.ReleaseNotes ?? manifest?.ReleaseNotes,
        LastCheckedAtUtc = timeProvider.GetUtcNow(),
        Message = state is UpdateLifecycleState.Activated or UpdateLifecycleState.RolledBack or UpdateLifecycleState.Failed
          ? journal!.Reason
          : state == UpdateLifecycleState.Staged
            ? "The signed update is verified and staged for activation."
            : result.Message,
        FeedSource = staged is not null ? Status.FeedSource ?? feedRelease?.Source : feedRelease?.Source,
      });
      return result;
    }
    catch (Exception exception) when (exception is not OperationCanceledException)
    {
      const string message = "The update feed is unavailable or its configuration could not be validated.";
      RecordUnavailable(message);
      throw new InvalidOperationException(message, exception);
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task<StagedUpdate> StageAsync(string expectedVersion, CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ArgumentException.ThrowIfNullOrWhiteSpace(expectedVersion);
      (IUpdateFeed feed, ReleaseVerifier verifier, string channel) = CreateServices();
      IUpdateFeedRelease feedRelease = await feed.ReadLatestReleaseAsync(channel, cancellationToken)
        ?? throw new InvalidOperationException("No release manifest is available.");
      UpdateCheckContext context = await ContextAsync(channel, cancellationToken);
      ReleaseValidationResult manifestResult = verifier.VerifyManifest(feedRelease.ManifestContent.Span, context);
      if (!manifestResult.IsValid || manifestResult.Manifest is not { } manifest)
        throw new InvalidOperationException(manifestResult.Message);
      if (!string.Equals(manifest.Version, expectedVersion, StringComparison.Ordinal))
        throw new InvalidOperationException("The available update changed after it was reviewed. Check again before staging.");
      UpdateTransactionJournal? latestJournal = ReadLatestJournal();
      if (latestJournal is { } journal &&
          string.Equals(journal.Version, manifest.Version, StringComparison.Ordinal) &&
          journal.State is "RolledBack" or "RollbackFailed")
        throw new InvalidOperationException("This release was already rejected by activation health and cannot be restaged.");

      await using Stream package = await feedRelease.OpenPackageAsync(manifest.PackageFileName, cancellationToken);
      ReleaseValidationResult packageResult = await verifier.VerifyPackageAsync(manifest, package, cancellationToken);
      if (!packageResult.IsValid) throw new InvalidOperationException(packageResult.Message);
      if (package.CanSeek) package.Position = 0;

      string stagingRoot = RequiredFullPath("Updates:StagingRoot");
      Directory.CreateDirectory(stagingRoot);
      string finalPath = Path.Combine(stagingRoot, manifest.Version);
      string temporaryPath = Path.Combine(stagingRoot, $".{manifest.Version}-{Guid.NewGuid():N}.tmp");
      if (Directory.Exists(finalPath))
      {
        StagedUpdate? adopted = await TryAdoptExistingStageAsync(verifier, context, manifest, cancellationToken);
        if (adopted is null) throw new InvalidOperationException("The existing staged release is incomplete or invalid.");
        Volatile.Write(ref _staged, adopted);
        Volatile.Write(ref _status, Status with
        {
          State = UpdateLifecycleState.Staged,
          AvailableVersion = adopted.Version,
          StagedVersion = adopted.Version,
          ReleaseNotes = adopted.ReleaseNotes,
          Message = "The previously verified release is staged and ready for activation.",
        });
        return adopted;
      }
      Directory.CreateDirectory(temporaryPath);
      try
      {
        string packagePath = Path.Combine(temporaryPath, manifest.PackageFileName);
        await using (var packageFile = new FileStream(
          packagePath,
          FileMode.CreateNew,
          FileAccess.Write,
          FileShare.None,
          bufferSize: 64 * 1024,
          FileOptions.Asynchronous | FileOptions.WriteThrough))
        {
          await package.CopyToAsync(packageFile, cancellationToken);
          await packageFile.FlushAsync(cancellationToken);
        }
        string manifestPath = Path.Combine(temporaryPath, "verified-manifest.json");
        await File.WriteAllBytesAsync(manifestPath, feedRelease.ManifestContent.ToArray(), cancellationToken);
        Directory.Move(temporaryPath, finalPath);
      }
      catch
      {
        if (Directory.Exists(temporaryPath)) Directory.Delete(temporaryPath, recursive: true);
        throw;
      }

      var staged = new StagedUpdate(
        manifest.Version,
        finalPath,
        Path.Combine(finalPath, manifest.PackageFileName),
        Path.Combine(finalPath, "verified-manifest.json"),
        timeProvider.GetUtcNow(),
        manifest.ReleaseNotes);
      Volatile.Write(ref _staged, staged);
      Volatile.Write(ref _status, Status with
      {
        State = UpdateLifecycleState.Staged,
        AvailableVersion = staged.Version,
        StagedVersion = staged.Version,
        ReleaseNotes = staged.ReleaseNotes,
        Message = "The signed update is verified and staged for activation.",
        FeedSource = feedRelease.Source,
      });
      return staged;
    }
    finally
    {
      _gate.Release();
    }
  }

  public async Task<StagedUpdate> StageUploadedBundleAsync(
    Stream bundle,
    long contentLength,
    CancellationToken cancellationToken)
  {
    ArgumentNullException.ThrowIfNull(bundle);
    if (contentLength <= 0 || contentLength > MaximumUploadedBundleBytes)
      throw new InvalidDataException("The signed update bundle is empty or too large.");

    await _gate.WaitAsync(cancellationToken);
    string? temporaryPath = null;
    try
    {
      string certificatePath = RequiredFullPath("Updates:SigningCertificatePath");
      string channel = configuration["Updates:Channel"] ?? "stable";
      var verifier = new ReleaseVerifier(X509CertificateLoader.LoadCertificateFromFile(certificatePath));
      UpdateCheckContext context = await ContextAsync(channel, cancellationToken);
      string stagingRoot = RequiredFullPath("Updates:StagingRoot");
      Directory.CreateDirectory(stagingRoot);
      temporaryPath = Path.Combine(stagingRoot, $".upload-{Guid.NewGuid():N}.tmp");
      Directory.CreateDirectory(temporaryPath);
      string bundlePath = Path.Combine(temporaryPath, "signed-update-bundle.zip");
      await using (var output = new FileStream(
        bundlePath,
        FileMode.CreateNew,
        FileAccess.ReadWrite,
        FileShare.None,
        64 * 1024,
        FileOptions.Asynchronous | FileOptions.SequentialScan | FileOptions.WriteThrough))
      {
        await CopyBoundedAsync(bundle, output, MaximumUploadedBundleBytes, cancellationToken);
        await output.FlushAsync(cancellationToken);
      }

      byte[] manifestBytes;
      UpdateManifest manifest;
      string packagePath;
      await using (var bundleFile = new FileStream(
        bundlePath,
        FileMode.Open,
        FileAccess.Read,
        FileShare.Read,
        64 * 1024,
        FileOptions.Asynchronous | FileOptions.SequentialScan))
      using (var archive = new ZipArchive(bundleFile, ZipArchiveMode.Read, leaveOpen: false))
      {
        if (archive.Entries.Count != 2)
          throw new InvalidDataException("A signed update bundle must contain exactly a manifest and its package.");
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
          string normalized = entry.FullName.Replace('\\', '/');
          if (string.IsNullOrWhiteSpace(normalized) || normalized.Contains('/') || normalized.Contains(':') ||
              !string.Equals(entry.Name, normalized, StringComparison.Ordinal) || !names.Add(normalized))
            throw new InvalidDataException("The signed update bundle contains an unsafe or duplicate entry.");
        }

        string manifestName = $"{channel}.manifest.json";
        ZipArchiveEntry manifestEntry = archive.GetEntry(manifestName)
          ?? throw new InvalidDataException("The signed update bundle manifest is missing.");
        if (manifestEntry.Length <= 0 || manifestEntry.Length > LocalFolderUpdateFeed.MaximumManifestBytes)
          throw new InvalidDataException("The signed update bundle manifest is empty or too large.");
        await using (Stream manifestInput = manifestEntry.Open())
        {
          using var manifestOutput = new MemoryStream((int)manifestEntry.Length);
          await CopyBoundedAsync(
            manifestInput,
            manifestOutput,
            LocalFolderUpdateFeed.MaximumManifestBytes,
            cancellationToken);
          manifestBytes = manifestOutput.ToArray();
        }

        ReleaseValidationResult manifestResult = verifier.VerifyManifest(manifestBytes, context);
        if (!manifestResult.IsValid || manifestResult.Manifest is not { } verifiedManifest)
          throw new InvalidDataException(manifestResult.Message);
        manifest = verifiedManifest;
        UpdateTransactionJournal? latestJournal = ReadLatestJournal();
        if (latestJournal is { } journal &&
            string.Equals(journal.Version, manifest.Version, StringComparison.Ordinal) &&
            journal.State is "RolledBack" or "RollbackFailed")
          throw new InvalidOperationException("This release was already rejected by activation health and cannot be restaged.");

        ZipArchiveEntry packageEntry = archive.GetEntry(manifest.PackageFileName)
          ?? throw new InvalidDataException("The manifest-named package is missing from the signed update bundle.");
        if (packageEntry.Length <= 0 || packageEntry.Length > ReleaseVerifier.MaximumPackageBytes)
          throw new InvalidDataException("The signed update package is empty or too large.");
        packagePath = Path.Combine(temporaryPath, manifest.PackageFileName);
        await using (Stream packageInput = packageEntry.Open())
        await using (var packageOutput = new FileStream(
          packagePath,
          FileMode.CreateNew,
          FileAccess.ReadWrite,
          FileShare.None,
          64 * 1024,
          FileOptions.Asynchronous | FileOptions.SequentialScan | FileOptions.WriteThrough))
        {
          await CopyBoundedAsync(packageInput, packageOutput, ReleaseVerifier.MaximumPackageBytes, cancellationToken);
          await packageOutput.FlushAsync(cancellationToken);
        }
      }

      await using (var package = new FileStream(
        packagePath,
        FileMode.Open,
        FileAccess.Read,
        FileShare.Read,
        64 * 1024,
        FileOptions.Asynchronous | FileOptions.SequentialScan))
      {
        ReleaseValidationResult packageResult = await verifier.VerifyPackageAsync(manifest, package, cancellationToken);
        if (!packageResult.IsValid) throw new InvalidDataException(packageResult.Message);
      }

      string finalPath = Path.Combine(stagingRoot, manifest.Version);
      if (Directory.Exists(finalPath))
      {
        StagedUpdate? adopted = await TryAdoptExistingStageAsync(verifier, context, manifest, cancellationToken);
        if (adopted is null) throw new InvalidOperationException("The existing staged release is incomplete or invalid.");
        Volatile.Write(ref _staged, adopted);
        Volatile.Write(ref _status, Status with
        {
          State = UpdateLifecycleState.Staged,
          AvailableVersion = adopted.Version,
          StagedVersion = adopted.Version,
          ReleaseNotes = adopted.ReleaseNotes,
          Message = "The previously verified release is staged and ready for activation.",
          FeedSource = "Manual signed bundle",
        });
        return adopted;
      }

      File.Delete(bundlePath);
      string manifestPath = Path.Combine(temporaryPath, "verified-manifest.json");
      await File.WriteAllBytesAsync(manifestPath, manifestBytes, cancellationToken);
      Directory.Move(temporaryPath, finalPath);
      temporaryPath = null;
      var staged = new StagedUpdate(
        manifest.Version,
        finalPath,
        Path.Combine(finalPath, manifest.PackageFileName),
        Path.Combine(finalPath, "verified-manifest.json"),
        timeProvider.GetUtcNow(),
        manifest.ReleaseNotes);
      Volatile.Write(ref _staged, staged);
      Volatile.Write(ref _status, Status with
      {
        State = UpdateLifecycleState.Staged,
        AvailableVersion = staged.Version,
        StagedVersion = staged.Version,
        ReleaseNotes = staged.ReleaseNotes,
        LastCheckedAtUtc = timeProvider.GetUtcNow(),
        Message = "The uploaded signed update is verified and staged for activation.",
        FeedSource = "Manual signed bundle",
      });
      return staged;
    }
    finally
    {
      if (temporaryPath is not null && Directory.Exists(temporaryPath))
        Directory.Delete(temporaryPath, recursive: true);
      _gate.Release();
    }
  }

  public async Task<string> ActivateAsync(string expectedVersion, CancellationToken cancellationToken)
  {
    await _gate.WaitAsync(cancellationToken);
    try
    {
      ArgumentException.ThrowIfNullOrWhiteSpace(expectedVersion);
      StagedUpdate staged = Staged ?? throw new InvalidOperationException("No verified release is staged.");
      if (!string.Equals(staged.Version, expectedVersion, StringComparison.Ordinal))
        throw new InvalidOperationException("The staged update changed after it was reviewed.");
      string backupRoot = RequiredFullPath("Updates:BackupRoot");
      string planRoot = RequiredFullPath("Updates:PlanRoot");
      string taskName = configuration["Updates:ScheduledTaskName"]
        ?? throw new InvalidOperationException("Updates:ScheduledTaskName is required.");
      Directory.CreateDirectory(backupRoot);
      Directory.CreateDirectory(planRoot);
      string transactionId = Guid.NewGuid().ToString("N");
      string backupPath = Path.Combine(backupRoot, $"pre-update-{transactionId}.db");
      string planPath = Path.Combine(planRoot, "pending-activation.json");
      if (File.Exists(planPath))
        throw new InvalidOperationException("A pending activation plan already exists.");
      await databaseBackup.BackupAsync(backupPath, cancellationToken);
      try
      {
        await using var planFile = new FileStream(
          planPath,
          FileMode.CreateNew,
          FileAccess.Write,
          FileShare.None,
          bufferSize: 16 * 1024,
          FileOptions.Asynchronous | FileOptions.WriteThrough);
        await JsonSerializer.SerializeAsync(planFile, new
        {
          TransactionId = transactionId,
          Version = staged.Version,
        }, cancellationToken: cancellationToken);
        await planFile.FlushAsync(cancellationToken);
      }
      catch
      {
        File.Delete(backupPath);
        throw;
      }

      try
      {
        var startInfo = new ProcessStartInfo
        {
          FileName = "schtasks.exe",
          UseShellExecute = false,
          CreateNoWindow = true,
          WindowStyle = ProcessWindowStyle.Hidden,
        };
        startInfo.ArgumentList.Add("/Run");
        startInfo.ArgumentList.Add("/TN");
        startInfo.ArgumentList.Add(taskName);
        using Process process = Process.Start(startInfo)
          ?? throw new InvalidOperationException("The privileged update task could not be started.");
        await process.WaitForExitAsync(cancellationToken);
        if (process.ExitCode != 0)
          throw new InvalidOperationException("The privileged update task rejected the activation request.");
      }
      catch
      {
        File.Delete(planPath);
        File.Delete(backupPath);
        throw;
      }

      Volatile.Write(ref _staged, null);
      Volatile.Write(ref _status, Status with
      {
        State = UpdateLifecycleState.Activating,
        StagedVersion = null,
        Message = "The signed update is activating. The service will reconnect after promotion or rollback.",
      });
      return transactionId;
    }
    finally
    {
      _gate.Release();
    }
  }

  public void RecordUnavailable(string message)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(message);
    Volatile.Write(ref _status, Status with
    {
      State = UpdateLifecycleState.Unavailable,
      LastCheckedAtUtc = timeProvider.GetUtcNow(),
      Message = message,
    });
  }

  private (IUpdateFeed Feed, ReleaseVerifier Verifier, string Channel) CreateServices()
  {
    string certificatePath = RequiredFullPath("Updates:SigningCertificatePath");
    string channel = configuration["Updates:Channel"] ?? "stable";
    return (_updateFeedFactory.Create(), new ReleaseVerifier(X509CertificateLoader.LoadCertificateFromFile(certificatePath)), channel);
  }

  private async Task<UpdateCheckContext> ContextAsync(string channel, CancellationToken cancellationToken)
  {
    await using TreadmillRunnerDbContext context = await contextFactory.CreateDbContextAsync(cancellationToken);
    int schema = (await context.Database.GetAppliedMigrationsAsync(cancellationToken)).Count();
    Version current = typeof(UpdateManager).Assembly.GetName().Version ?? new Version(0, 0);
    return new UpdateCheckContext(current, channel, schema);
  }

  private async Task<StagedUpdate?> TryAdoptExistingStageAsync(
    ReleaseVerifier verifier,
    UpdateCheckContext context,
    UpdateManifest expectedManifest,
    CancellationToken cancellationToken)
  {
    string stagePath = Path.Combine(RequiredFullPath("Updates:StagingRoot"), expectedManifest.Version);
    string manifestPath = Path.Combine(stagePath, "verified-manifest.json");
    if (!File.Exists(manifestPath)) return null;
    try
    {
      byte[] manifestBytes = await File.ReadAllBytesAsync(manifestPath, cancellationToken);
      ReleaseValidationResult manifestResult = verifier.VerifyManifest(manifestBytes, context);
      if (!manifestResult.IsValid || manifestResult.Manifest is not { } stagedManifest ||
          !string.Equals(stagedManifest.Version, expectedManifest.Version, StringComparison.Ordinal) ||
          !string.Equals(stagedManifest.PackageSha256, expectedManifest.PackageSha256, StringComparison.OrdinalIgnoreCase))
        return null;
      string packagePath = Path.Combine(stagePath, stagedManifest.PackageFileName);
      if (!File.Exists(packagePath)) return null;
      await using FileStream package = File.OpenRead(packagePath);
      ReleaseValidationResult packageResult = await verifier.VerifyPackageAsync(stagedManifest, package, cancellationToken);
      if (!packageResult.IsValid) return null;
      return new StagedUpdate(
        stagedManifest.Version,
        stagePath,
        packagePath,
        manifestPath,
        File.GetLastWriteTimeUtc(manifestPath),
        stagedManifest.ReleaseNotes);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or InvalidDataException)
    {
      return null;
    }
  }

  private string RequiredFullPath(string key)
  {
    string value = configuration[key] ?? throw new InvalidOperationException($"{key} is required.");
    return Path.GetFullPath(value);
  }

  private static async Task CopyBoundedAsync(
    Stream input,
    Stream output,
    long maximumBytes,
    CancellationToken cancellationToken)
  {
    var buffer = new byte[64 * 1024];
    long copied = 0;
    int read;
    while ((read = await input.ReadAsync(buffer, cancellationToken)) > 0)
    {
      copied += read;
      if (copied > maximumBytes) throw new InvalidDataException("The update input exceeds its size limit.");
      await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
    }
  }

  private UpdateTransactionJournal? ReadLatestJournal()
  {
    string? configuredRoot = configuration["Updates:PlanRoot"];
    if (string.IsNullOrWhiteSpace(configuredRoot)) return null;
    string root = Path.GetFullPath(configuredRoot);
    if (!Directory.Exists(root)) return null;
    try
    {
      string? latest = Directory.EnumerateFiles(root, "transaction-*.json", SearchOption.TopDirectoryOnly)
        .OrderByDescending(File.GetLastWriteTimeUtc)
        .FirstOrDefault();
      if (latest is null) return null;
      using FileStream input = File.Open(latest, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
      return JsonSerializer.Deserialize<UpdateTransactionJournal>(input, new JsonSerializerOptions(JsonSerializerDefaults.Web));
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
    {
      return null;
    }
  }

  private static string CurrentVersion() =>
    (typeof(UpdateManager).Assembly.GetName().Version ?? new Version(0, 0, 0)).ToString(3);
}

internal sealed record UpdateTransactionJournal(
  int SchemaVersion,
  string TransactionId,
  string Version,
  string State,
  DateTimeOffset OccurredAtUtc,
  string Reason);
