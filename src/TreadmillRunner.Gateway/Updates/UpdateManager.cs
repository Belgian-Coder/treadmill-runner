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

internal sealed record UpdateArtifactRetentionResult(
  int TerminalJournalCount,
  int RetainedTerminalJournalCount,
  int DeletedJournalCount,
  int DeletedStageCount,
  int DeletedBackupCount,
  bool SkippedAmbiguous);

public sealed class UpdateManager(
  IConfiguration configuration,
  TimeProvider timeProvider,
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
  SqliteOnlineBackupService databaseBackup,
  UpdateFeedFactory? updateFeedFactory = null)
{
  public const long MaximumUploadedBundleBytes = ReleaseVerifier.MaximumPackageBytes + (2L * 1024 * 1024);
  internal const int MaximumRetainedTerminalTransactions = 5;
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
      UpdateTransactionJournal? releaseJournal = null;
      bool releaseRejected = false;
      if (feedRelease is null)
      {
        result = new ReleaseValidationResult(ReleaseValidationStatus.NotNewer, "No release manifest is available.");
      }
      else
      {
        UpdateCheckContext context = await ContextAsync(channel, cancellationToken);
        result = verifier.VerifyManifest(feedRelease.ManifestContent.Span, context);
        IReadOnlyList<UpdateTransactionJournal> transactionHistory = ReadTransactionJournalsForActivation();
        releaseJournal = result.Manifest is { } checkedManifest
          ? transactionHistory.FirstOrDefault(journal =>
            string.Equals(journal.Version, checkedManifest.Version, StringComparison.Ordinal) &&
            journal.State is "RolledBack" or "RollbackFailed")
          : null;
        releaseRejected = releaseJournal?.State is "RolledBack" or "RollbackFailed";
        if (releaseRejected)
        {
          Volatile.Write(ref _staged, null);
        }
        else if (result is { IsValid: true, Manifest: not null } && Staged is null)
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
      UpdateTransactionJournal? journal = releaseRejected ? releaseJournal : ReadLatestJournal();
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
      PruneTerminalArtifacts();
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
      EnsureReleaseWasNotRejected(manifest.Version);

      await using Stream package = await feedRelease.OpenPackageAsync(manifest.PackageFileName, cancellationToken);
      ReleaseValidationResult packageResult = await verifier.VerifyPackageAsync(manifest, package, cancellationToken);
      if (!packageResult.IsValid) throw new InvalidOperationException(packageResult.Message);
      if (package.CanSeek) package.Position = 0;

      string stagingRoot = RequiredFullPath("Updates:StagingRoot");
      string finalPath = Path.Combine(stagingRoot, manifest.Version);
      string temporaryPath = Path.Combine(stagingRoot, $".{manifest.Version}-{Guid.NewGuid():N}.tmp");
      // Mutex ownership is thread-affine. Keep each lease strictly around
      // synchronous filesystem decisions; no await may run while it is held.
      using (MaintenanceMutexLease feedPublicationLease = AcquireMaintenanceMutex())
      {
        Directory.CreateDirectory(stagingRoot);
      }
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
        using MaintenanceMutexLease feedPublicationLease = AcquireMaintenanceMutex();
        // The local feed publisher replaces package and manifest as one
        // mutex-protected transaction. Re-read the manifest while holding the
        // same mutex before adopting the package we just verified. If a feed
        // publication overlapped the earlier reads, retry from a coherent
        // manifest/package pair instead of staging a stale or mixed release.
        IUpdateFeedRelease currentFeedRelease = feed
          .ReadLatestReleaseAsync(channel, cancellationToken)
          .GetAwaiter()
          .GetResult()
          ?? throw new InvalidOperationException("The update feed changed while the release was being staged.");
        if (!currentFeedRelease.ManifestContent.Span.SequenceEqual(feedRelease.ManifestContent.Span))
          throw new InvalidOperationException("The update feed changed while the release was being staged. Check again before retrying.");
        if (Directory.Exists(finalPath))
          throw new InvalidOperationException("Another verified release became staged while this package was being written.");
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
        EnsureReleaseWasNotRejected(manifest.Version);

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
      EnsureReleaseWasNotRejected(staged.Version);
      string backupRoot = RequiredFullPath("Updates:BackupRoot");
      string planRoot = RequiredFullPath("Updates:PlanRoot");
      string taskName = configuration["Updates:ScheduledTaskName"]
        ?? throw new InvalidOperationException("Updates:ScheduledTaskName is required.");
      if (!string.Equals(taskName, "TreadmillRunnerUpdate", StringComparison.Ordinal))
        throw new InvalidOperationException("Updates:ScheduledTaskName must be TreadmillRunnerUpdate because the protected helper contract is fixed.");
      // Mutex is thread-affine. Keep the complete publication and task-launch
      // transaction on one worker thread so ownership survives every blocking
      // wait and is released only after schtasks has exited.
      return await Task.Run(
        () => ActivateUnderMaintenanceMutex(staged, backupRoot, planRoot, taskName, cancellationToken),
        CancellationToken.None);
    }
    finally
    {
      _gate.Release();
    }
  }

  private string ActivateUnderMaintenanceMutex(
    StagedUpdate staged,
    string backupRoot,
    string planRoot,
    string taskName,
    CancellationToken cancellationToken)
  {
    using MaintenanceMutexLease maintenanceLease = AcquireMaintenanceMutex();
    EnsureReleaseWasNotRejected(staged.Version);
    string? configuredDataRoot = configuration["Updates:DataRoot"];
    if (!string.IsNullOrWhiteSpace(configuredDataRoot) &&
        File.Exists(Path.Combine(Path.GetFullPath(configuredDataRoot), "updates", "service-maintenance.lock")))
      throw new InvalidOperationException("Service maintenance is already in progress; activation was not started.");
    Directory.CreateDirectory(backupRoot);
    Directory.CreateDirectory(planRoot);
    string transactionId = Guid.NewGuid().ToString("N");
    string backupPath = Path.Combine(backupRoot, $"pre-update-{transactionId}.db");
    string planPath = Path.Combine(planRoot, "pending-activation.json");
    if (File.Exists(planPath))
      throw new InvalidOperationException("A pending activation plan already exists.");
    bool planOwned = false;
    bool taskStarted = false;
    try
    {
      cancellationToken.ThrowIfCancellationRequested();
      databaseBackup.BackupAsync(backupPath, cancellationToken).GetAwaiter().GetResult();
      using (var planFile = new FileStream(
        planPath,
        FileMode.CreateNew,
        FileAccess.Write,
        FileShare.None,
        bufferSize: 16 * 1024,
        FileOptions.WriteThrough))
      {
        planOwned = true;
        JsonSerializer.SerializeAsync(planFile, new
        {
          TransactionId = transactionId,
          Version = staged.Version,
        }, cancellationToken: CancellationToken.None).GetAwaiter().GetResult();
        planFile.Flush(true);
      }

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
      taskStarted = true;
      // Once Process.Start succeeds, the task may already be queued even if
      // this wait is canceled or schtasks later reports an error. Keep the
      // plan and backup until the privileged helper records a terminal result.
      process.WaitForExitAsync(cancellationToken).GetAwaiter().GetResult();
      if (process.ExitCode != 0)
      {
        // Process.Start means Task Scheduler may already have accepted and
        // queued the protected helper. Preserve the maintenance gate by
        // reporting an indeterminate dispatch as Activating instead of
        // surfacing an error that lets the endpoint admit a new live session.
        Volatile.Write(ref _staged, null);
        Volatile.Write(ref _status, Status with
        {
          State = UpdateLifecycleState.Activating,
          StagedVersion = null,
          Message = "The signed update task was queued, but its launcher result was indeterminate; activation or rollback continues under maintenance.",
        });
        return transactionId;
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
    catch (Exception) when (taskStarted)
    {
      // Cancellation or a process-inspection failure after Process.Start does
      // not prove that the task was rejected. Keep the durable plan, backup,
      // and live-session maintenance gate until terminal recovery evidence.
      Volatile.Write(ref _staged, null);
      Volatile.Write(ref _status, Status with
      {
        State = UpdateLifecycleState.Activating,
        StagedVersion = null,
        Message = "The signed update task was queued; activation continues while the service reconnects after promotion or rollback.",
      });
      return transactionId;
    }
    catch
    {
      if (planOwned) File.Delete(planPath);
      if (File.Exists(backupPath)) File.Delete(backupPath);
      throw;
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

  internal UpdateArtifactRetentionResult PruneTerminalArtifacts()
  {
    string? configuredPlanRoot = configuration["Updates:PlanRoot"];
    string? configuredStagingRoot = configuration["Updates:StagingRoot"];
    string? configuredBackupRoot = configuration["Updates:BackupRoot"];
    if (string.IsNullOrWhiteSpace(configuredPlanRoot) ||
        string.IsNullOrWhiteSpace(configuredStagingRoot) ||
        string.IsNullOrWhiteSpace(configuredBackupRoot))
    {
      return new(0, 0, 0, 0, 0, true);
    }

    try
    {
      string planRoot = Path.GetFullPath(configuredPlanRoot);
      string stagingRoot = Path.GetFullPath(configuredStagingRoot);
      string backupRoot = Path.GetFullPath(configuredBackupRoot);
      if (!Directory.Exists(planRoot) || !Directory.Exists(stagingRoot) || !Directory.Exists(backupRoot) ||
          ContainsReparsePoint(planRoot) || ContainsReparsePoint(stagingRoot) || ContainsReparsePoint(backupRoot))
      {
        return new(0, 0, 0, 0, 0, true);
      }

      var referencedVersions = new HashSet<string>(StringComparer.Ordinal);
      var referencedTransactions = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
      var versionReferenceCounts = new Dictionary<string, int>(StringComparer.Ordinal);
      var transactionReferenceCounts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
      var terminal = new List<TerminalArtifact>();
      bool ambiguousJournal = false;

      foreach (string journalPath in Directory.EnumerateFiles(planRoot, "transaction-*.json", SearchOption.TopDirectoryOnly))
      {
        string fileName = Path.GetFileNameWithoutExtension(journalPath);
        string transactionId = fileName.StartsWith("transaction-", StringComparison.Ordinal)
          ? fileName["transaction-".Length..]
          : string.Empty;
        if (!IsTransactionId(transactionId))
        {
          ambiguousJournal = true;
          continue;
        }

        try
        {
          UpdateTransactionJournal? journal = JsonSerializer.Deserialize<UpdateTransactionJournal>(
            File.ReadAllText(journalPath), new JsonSerializerOptions(JsonSerializerDefaults.Web));
          if (journal is null || journal.SchemaVersion != 1 ||
              !IsTransactionId(journal.TransactionId) ||
              !string.Equals(journal.TransactionId, transactionId, StringComparison.OrdinalIgnoreCase) ||
              !IsVersion(journal.Version) ||
              journal.State is not ("Activating" or "Activated" or "RolledBack" or "RollbackFailed") ||
              journal.OccurredAtUtc == default ||
              journal.Reason is null)
          {
            ambiguousJournal = true;
            continue;
          }

          versionReferenceCounts[journal.Version] = versionReferenceCounts.GetValueOrDefault(journal.Version) + 1;
          transactionReferenceCounts[transactionId] = transactionReferenceCounts.GetValueOrDefault(transactionId) + 1;
          if (journal.State is "Activated" or "RolledBack")
          {
            terminal.Add(new TerminalArtifact(
              journalPath,
              transactionId,
              journal.Version,
              journal.OccurredAtUtc,
              File.GetLastWriteTimeUtc(journalPath)));
          }
          else
          {
            referencedTransactions.Add(transactionId);
            referencedVersions.Add(journal.Version);
          }
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or FormatException)
        {
          // A malformed or unreadable journal could share a version directory
          // or backup with a parseable journal. Protect the entire retention pass.
          ambiguousJournal = true;
        }
      }

      if (ambiguousJournal)
        return new(terminal.Count, terminal.Count, 0, 0, 0, true);

      string pendingPath = Path.Combine(planRoot, "pending-activation.json");
      if (File.Exists(pendingPath))
      {
        try
        {
          using JsonDocument pending = JsonDocument.Parse(File.ReadAllText(pendingPath));
          if (pending.RootElement.ValueKind != JsonValueKind.Object ||
              !pending.RootElement.TryGetProperty("TransactionId", out JsonElement transaction) ||
              transaction.ValueKind != JsonValueKind.String ||
              !pending.RootElement.TryGetProperty("Version", out JsonElement version) ||
              version.ValueKind != JsonValueKind.String)
            return new(terminal.Count, terminal.Count, 0, 0, 0, true);
          string? transactionIdText = transaction.GetString();
          string? versionText = version.GetString();
          if (transactionIdText is null || versionText is null || !IsTransactionId(transactionIdText) || !IsVersion(versionText))
            return new(terminal.Count, terminal.Count, 0, 0, 0, true);
          string transactionId = transactionIdText;
          referencedTransactions.Add(transactionId);
          transactionReferenceCounts[transactionId] = transactionReferenceCounts.GetValueOrDefault(transactionId) + 1;
          referencedVersions.Add(versionText);
          versionReferenceCounts[versionText] = versionReferenceCounts.GetValueOrDefault(versionText) + 1;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException)
        {
          // An unreadable pending plan protects all possibly related artifacts.
          return new(terminal.Count, terminal.Count, 0, 0, 0, true);
        }
      }

      if (IsVersion(CurrentVersion())) referencedVersions.Add(CurrentVersion());
      if (Staged is { } staged && IsVersion(staged.Version)) referencedVersions.Add(staged.Version);

      IReadOnlyList<TerminalArtifact> ordered = terminal
        .OrderByDescending(static artifact => artifact.OccurredAtUtc)
        .ThenByDescending(static artifact => artifact.LastWriteTimeUtc)
        .ToArray();
      var keep = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
      foreach (TerminalArtifact artifact in ordered.Take(MaximumRetainedTerminalTransactions)) keep.Add(artifact.JournalPath);
      foreach (TerminalArtifact artifact in ordered.Where(artifact => referencedVersions.Contains(artifact.Version) || referencedTransactions.Contains(artifact.TransactionId)))
        keep.Add(artifact.JournalPath);

      int deletedJournals = 0;
      int deletedStages = 0;
      int deletedBackups = 0;
      foreach (TerminalArtifact artifact in ordered)
      {
        if (keep.Contains(artifact.JournalPath)) continue;

        // A version or transaction mentioned by any journal remains protected;
        // only an unambiguous, terminal journal may release its three artifacts.
        if (versionReferenceCounts.GetValueOrDefault(artifact.Version) > 1 ||
            transactionReferenceCounts.GetValueOrDefault(artifact.TransactionId) > 1)
          continue;

        string stagePath = Path.Combine(stagingRoot, artifact.Version);
        string backupPath = Path.Combine(backupRoot, $"pre-update-{artifact.TransactionId}.db");
        if (!IsSafeArtifactPath(stagePath, stagingRoot) || !IsSafeArtifactPath(backupPath, backupRoot) ||
            !IsSafeArtifactPath(artifact.JournalPath, planRoot))
          continue;

        try
        {
          if (Directory.Exists(stagePath))
          {
            Directory.Delete(stagePath, recursive: true);
            deletedStages++;
          }
          if (File.Exists(backupPath))
          {
            File.Delete(backupPath);
            deletedBackups++;
          }
          File.Delete(artifact.JournalPath);
          deletedJournals++;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
          // Leave the journal and any remaining artifact for a later safe pass.
        }
      }

      return new(
        terminal.Count,
        terminal.Count - deletedJournals,
        deletedJournals,
        deletedStages,
        deletedBackups,
        false);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException)
    {
      return new(0, 0, 0, 0, 0, true);
    }
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

  private void EnsureReleaseWasNotRejected(string version)
  {
    UpdateTransactionJournal? journal = ReadTransactionJournalsForActivation()
      .FirstOrDefault(candidate =>
        string.Equals(candidate.Version, version, StringComparison.Ordinal) &&
        candidate.State is "RolledBack" or "RollbackFailed");
    if (journal is not null)
    {
      Volatile.Write(ref _staged, null);
      throw new InvalidOperationException("This release was already rejected by activation health and cannot be activated again.");
    }
  }

  private IReadOnlyList<UpdateTransactionJournal> ReadTransactionJournalsForActivation()
  {
    string? configuredRoot = configuration["Updates:PlanRoot"];
    if (string.IsNullOrWhiteSpace(configuredRoot)) return [];
    string root = Path.GetFullPath(configuredRoot);
    if (!Directory.Exists(root)) return [];
    try
    {
      var journals = new List<(DateTime LastWriteTimeUtc, UpdateTransactionJournal Journal)>();
      foreach (string path in Directory.EnumerateFiles(root, "transaction-*.json", SearchOption.TopDirectoryOnly))
      {
        using FileStream input = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        UpdateTransactionJournal journal = JsonSerializer.Deserialize<UpdateTransactionJournal>(
          input,
          new JsonSerializerOptions(JsonSerializerDefaults.Web))
          ?? throw new InvalidDataException("An update transaction journal is empty.");
        string fileTransactionId = Path.GetFileNameWithoutExtension(path)["transaction-".Length..];
        if (journal.SchemaVersion != 1 ||
            !IsTransactionId(journal.TransactionId) ||
            !string.Equals(fileTransactionId, journal.TransactionId, StringComparison.OrdinalIgnoreCase) ||
            !IsVersion(journal.Version) ||
            journal.State is not ("Activating" or "Activated" or "RolledBack" or "RollbackFailed") ||
            journal.OccurredAtUtc == default ||
            journal.Reason is null)
          throw new InvalidDataException("An update transaction journal is invalid.");
        journals.Add((File.GetLastWriteTimeUtc(path), journal));
      }
      return journals
        .OrderByDescending(static item => item.LastWriteTimeUtc)
        .Select(static item => item.Journal)
        .ToArray();
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or ArgumentException)
    {
      throw new InvalidOperationException("Update transaction history is unreadable or ambiguous; activation was not started.", exception);
    }
  }

  private string RequiredFullPath(string key)
  {
    string value = configuration[key] ?? throw new InvalidOperationException($"{key} is required.");
    return Path.GetFullPath(value);
  }

  private sealed class MaintenanceMutexLease(Mutex mutex) : IDisposable
  {
    private bool held = true;

    public void Dispose()
    {
      if (held)
      {
        mutex.ReleaseMutex();
        held = false;
      }
      mutex.Dispose();
    }
  }

  private static MaintenanceMutexLease AcquireMaintenanceMutex()
  {
    Mutex mutex = new(false, "Global\\TreadmillRunnerGateway.Maintenance");
    try
    {
      bool acquired;
      try
      {
        acquired = mutex.WaitOne(30_000);
      }
      catch (AbandonedMutexException)
      {
        acquired = true;
      }
      if (!acquired) throw new InvalidOperationException("The update maintenance lock could not be acquired.");
      return new MaintenanceMutexLease(mutex);
    }
    catch
    {
      mutex.Dispose();
      throw;
    }
  }

  private static bool IsTransactionId(string? value) =>
    value is { Length: 32 } && value.All(static character => Uri.IsHexDigit(character));

  private static bool IsVersion(string? value) =>
    value is not null && Version.TryParse(value, out Version? parsed) &&
    parsed.ToString(3) == value;

  private static bool IsSafeArtifactPath(string path, string root)
  {
    try
    {
      string resolvedPath = Path.GetFullPath(path);
      string resolvedRoot = Path.GetFullPath(root);
      string prefix = resolvedRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
      if (!resolvedPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) return false;
      string? cursor = resolvedPath;
      while (cursor is not null && cursor.StartsWith(resolvedRoot, StringComparison.OrdinalIgnoreCase))
      {
        if (File.Exists(cursor) || Directory.Exists(cursor))
        {
          if ((File.GetAttributes(cursor) & FileAttributes.ReparsePoint) != 0) return false;
        }
        if (string.Equals(cursor, resolvedRoot, StringComparison.OrdinalIgnoreCase)) break;
        cursor = Path.GetDirectoryName(cursor);
      }
      return !Directory.Exists(resolvedPath) || !ContainsReparsePoint(resolvedPath);
    }
    catch (IOException) { return false; }
    catch (UnauthorizedAccessException) { return false; }
    catch (ArgumentException) { return false; }
  }

  private static bool ContainsReparsePoint(string root)
  {
    try
    {
      var pending = new Stack<DirectoryInfo>();
      DirectoryInfo rootInfo = new(root);
      if ((rootInfo.Attributes & FileAttributes.ReparsePoint) != 0) return true;
      pending.Push(rootInfo);
      while (pending.Count > 0)
      {
        foreach (FileSystemInfo item in pending.Pop().EnumerateFileSystemInfos("*", SearchOption.TopDirectoryOnly))
        {
          if ((item.Attributes & FileAttributes.ReparsePoint) != 0) return true;
          if (item is DirectoryInfo directory) pending.Push(directory);
        }
      }
      return false;
    }
    catch (IOException) { return true; }
    catch (UnauthorizedAccessException) { return true; }
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

internal sealed record TerminalArtifact(
  string JournalPath,
  string TransactionId,
  string Version,
  DateTimeOffset OccurredAtUtc,
  DateTime LastWriteTimeUtc);
