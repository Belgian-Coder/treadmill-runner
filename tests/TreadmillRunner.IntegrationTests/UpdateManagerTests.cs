using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Data.Sqlite;
using TreadmillRunner.Core.Updates;
using TreadmillRunner.Gateway.Updates;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class UpdateManagerTests : IDisposable
{
  private readonly string root = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.UpdateManagerTests",
    Guid.NewGuid().ToString("N"));
  private readonly RSA signingKey = RSA.Create(2048);
  private readonly X509Certificate2 certificate;

  public UpdateManagerTests()
  {
    var request = new CertificateRequest(
      "CN=TreadmillRunner Update Manager Test",
      signingKey,
      HashAlgorithmName.SHA256,
      RSASignaturePadding.Pkcs1);
    certificate = request.CreateSelfSigned(
      DateTimeOffset.UtcNow.AddMinutes(-1),
      DateTimeOffset.UtcNow.AddDays(1));
    Directory.CreateDirectory(root);
  }

  [Fact]
  public async Task Signed_release_checks_and_stages_to_verified_version_directory()
  {
    string feed = Path.Combine(root, "feed");
    string staging = Path.Combine(root, "staging");
    string plans = Path.Combine(root, "plans");
    string database = Path.Combine(root, "live.db");
    string certificatePath = Path.Combine(root, "signing.cer");
    Directory.CreateDirectory(feed);
    await File.WriteAllBytesAsync(certificatePath, certificate.Export(X509ContentType.Cert));

    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
      await context.Database.MigrateAsync();

    byte[] package = CreateValidPackage("99.0.0");
    var unsigned = new UpdateManifest(
      1,
      "99.0.0",
      "stable",
      "treadmillrunner-99.0.0.zip",
      Convert.ToHexString(SHA256.HashData(package)),
      0,
      100,
      "Signed staging fixture",
      string.Empty);
    byte[] signature = signingKey.SignData(
      UpdateManifestSigningPayload.Create(unsigned),
      HashAlgorithmName.SHA256,
      RSASignaturePadding.Pkcs1);
    UpdateManifest manifest = unsigned with { Signature = Convert.ToBase64String(signature) };
    await File.WriteAllBytesAsync(Path.Combine(feed, manifest.PackageFileName), package);
    await File.WriteAllBytesAsync(
      Path.Combine(feed, "stable.manifest.json"),
      JsonSerializer.SerializeToUtf8Bytes(manifest, new JsonSerializerOptions(JsonSerializerDefaults.Web)));

    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:FeedPath"] = feed,
        ["Updates:StagingRoot"] = staging,
        ["Updates:PlanRoot"] = plans,
        ["Updates:SigningCertificatePath"] = certificatePath,
        ["Updates:Channel"] = "stable",
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));

    ReleaseValidationResult check = await manager.CheckAsync(CancellationToken.None);
    Assert.Equal(ReleaseValidationStatus.Valid, check.Status);
    Assert.Equal(UpdateLifecycleState.Available, manager.Status.State);
    Assert.Equal("99.0.0", manager.Status.AvailableVersion);
    Assert.NotNull(manager.Status.LastCheckedAtUtc);
    await Assert.ThrowsAsync<InvalidOperationException>(() =>
      manager.StageAsync("98.0.0", CancellationToken.None));
    StagedUpdate staged = await manager.StageAsync("99.0.0", CancellationToken.None);

    Assert.Equal("99.0.0", staged.Version);
    Assert.Equal(Path.Combine(staging, "99.0.0"), staged.StagePath);
    Assert.True(File.Exists(staged.PackagePath));
    using (var stagedArchive = ZipFile.OpenRead(staged.PackagePath))
    {
      ZipArchiveEntry entry = Assert.Single(stagedArchive.Entries, entry => entry.FullName == "app/version.txt");
      using var reader = new StreamReader(entry.Open());
      Assert.Equal("99.0.0", await reader.ReadToEndAsync());
    }
    Assert.True(File.Exists(staged.ManifestPath));
    Assert.Equal(UpdateLifecycleState.Staged, manager.Status.State);
    Assert.Equal(staged.StagePath, (await manager.StageAsync("99.0.0", CancellationToken.None)).StagePath);

    var restartedManager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));
    Assert.Equal(ReleaseValidationStatus.Valid, (await restartedManager.CheckAsync(CancellationToken.None)).Status);
    Assert.Equal(UpdateLifecycleState.Staged, restartedManager.Status.State);
    Assert.Equal(staged.PackagePath, restartedManager.Staged?.PackagePath);

    Directory.CreateDirectory(plans);
    string malformedJournal = Path.Combine(plans, "transaction-b0000000000000000000000000000000.json");
    await File.WriteAllTextAsync(malformedJournal, "{");
    InvalidOperationException ambiguity = await Assert.ThrowsAsync<InvalidOperationException>(() =>
      restartedManager.ActivateAsync("99.0.0", CancellationToken.None));
    Assert.Contains("unreadable or ambiguous", ambiguity.Message, StringComparison.Ordinal);
    Assert.NotNull(restartedManager.Staged);
    File.Delete(malformedJournal);

    await File.WriteAllTextAsync(Path.Combine(plans, "transaction-a0000000000000000000000000000000.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = "a0000000000000000000000000000000",
      version = "99.0.0",
      state = "RolledBack",
      occurredAtUtc = DateTimeOffset.UtcNow,
      reason = "Activation health failed.",
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
    await File.WriteAllTextAsync(Path.Combine(plans, "transaction-c0000000000000000000000000000000.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = "c0000000000000000000000000000000",
      version = "100.0.0",
      state = "Activated",
      occurredAtUtc = DateTimeOffset.UtcNow.AddSeconds(1),
      reason = "A different release activated later.",
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
    await File.WriteAllTextAsync(Path.Combine(plans, "transaction-d0000000000000000000000000000000.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = "d0000000000000000000000000000000",
      version = "99.0.0",
      state = "Activated",
      occurredAtUtc = DateTimeOffset.UtcNow.AddSeconds(2),
      reason = "A conflicting later record must not hide the rejection.",
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));

    InvalidOperationException rejection = await Assert.ThrowsAsync<InvalidOperationException>(() =>
      restartedManager.ActivateAsync("99.0.0", CancellationToken.None));
    Assert.Contains("already rejected", rejection.Message, StringComparison.Ordinal);
    Assert.Null(restartedManager.Staged);

    var secondRestart = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));
    Assert.Equal(ReleaseValidationStatus.Valid, (await secondRestart.CheckAsync(CancellationToken.None)).Status);
    Assert.Equal(UpdateLifecycleState.RolledBack, secondRestart.Status.State);
    Assert.Null(secondRestart.Staged);
  }

  [Fact]
  public async Task Uploaded_signed_bundle_stages_without_changing_the_feed_or_activating()
  {
    string staging = Path.Combine(root, "uploaded-staging");
    string database = Path.Combine(root, "uploaded.db");
    string certificatePath = Path.Combine(root, "uploaded-signing.cer");
    await File.WriteAllBytesAsync(certificatePath, certificate.Export(X509ContentType.Cert));
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
      await context.Database.MigrateAsync();
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:StagingRoot"] = staging,
        ["Updates:SigningCertificatePath"] = certificatePath,
        ["Updates:Channel"] = "stable",
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));
    byte[] bundle = CreateSignedBundle("99.0.0");

    StagedUpdate staged = await manager.StageUploadedBundleAsync(
      new MemoryStream(bundle),
      bundle.Length,
      CancellationToken.None);

    Assert.Equal("99.0.0", staged.Version);
    Assert.Equal(UpdateLifecycleState.Staged, manager.Status.State);
    Assert.Equal("Manual signed bundle", manager.Status.FeedSource);
    Assert.True(File.Exists(staged.PackagePath));
    Assert.Null(Directory.EnumerateDirectories(staging, ".upload-*.tmp").FirstOrDefault());
  }

  [Fact]
  public async Task Activation_waits_for_the_global_maintenance_mutex_before_publishing_a_plan()
  {
    string staging = Path.Combine(root, "activation-staging");
    string plans = Path.Combine(root, "activation-plans");
    string backups = Path.Combine(root, "activation-backups");
    string database = Path.Combine(root, "activation.db");
    string certificatePath = Path.Combine(root, "activation-signing.cer");
    await File.WriteAllBytesAsync(certificatePath, certificate.Export(X509ContentType.Cert));
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
      await context.Database.MigrateAsync();
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:StagingRoot"] = staging,
        ["Updates:PlanRoot"] = plans,
        ["Updates:BackupRoot"] = backups,
        ["Updates:SigningCertificatePath"] = certificatePath,
        ["Updates:ScheduledTaskName"] = "TreadmillRunnerUpdate",
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));
    byte[] bundle = CreateSignedBundle("99.0.0");
    await manager.StageUploadedBundleAsync(new MemoryStream(bundle), bundle.Length, CancellationToken.None);

    var mutexAcquired = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    var releaseMutex = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
    Task mutexOwner = Task.Run(() =>
    {
      using var maintenanceMutex = new Mutex(false, "Global\\TreadmillRunnerGateway.Maintenance");
      bool acquired = false;
      try
      {
        acquired = maintenanceMutex.WaitOne(TimeSpan.FromSeconds(5));
        mutexAcquired.SetResult(acquired);
        if (acquired) releaseMutex.Task.GetAwaiter().GetResult();
      }
      catch (Exception exception)
      {
        mutexAcquired.TrySetException(exception);
        throw;
      }
      finally
      {
        if (acquired) maintenanceMutex.ReleaseMutex();
      }
    });
    Assert.True(await mutexAcquired.Task);
    using var cancellation = new CancellationTokenSource();
    Task activation = manager.ActivateAsync("99.0.0", cancellation.Token);
    await Task.Delay(200);
    Assert.False(File.Exists(Path.Combine(plans, "pending-activation.json")));
    Assert.Empty(Directory.Exists(backups) ? Directory.EnumerateFiles(backups) : Enumerable.Empty<string>());
    cancellation.Cancel();
    releaseMutex.SetResult(true);
    await mutexOwner;

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() => activation);
  }

  [Fact]
  public async Task Automatic_check_runs_at_startup_and_feed_failure_does_not_stop_the_worker()
  {
    string database = Path.Combine(root, "worker.db");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));
    var worker = new UpdateCheckWorker(manager, TimeProvider.System, NullLogger<UpdateCheckWorker>.Instance);

    await worker.StartAsync(CancellationToken.None);
    for (int attempt = 0; attempt < 20 && manager.Status.State != UpdateLifecycleState.Unavailable; attempt++)
      await Task.Delay(25);

    Assert.Equal(UpdateLifecycleState.Unavailable, manager.Status.State);
    Assert.Equal(TimeSpan.FromHours(6), UpdateCheckWorker.CheckInterval);
    using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(2));
    await worker.StopAsync(stop.Token);
  }

  [Fact]
  public async Task Newer_valid_release_is_available_after_an_older_release_rolled_back()
  {
    string feed = Path.Combine(root, "recovery-feed");
    string staging = Path.Combine(root, "recovery-staging");
    string plans = Path.Combine(root, "recovery-plans");
    string database = Path.Combine(root, "recovery.db");
    string certificatePath = Path.Combine(root, "recovery-signing.cer");
    Directory.CreateDirectory(feed);
    Directory.CreateDirectory(plans);
    await File.WriteAllBytesAsync(certificatePath, certificate.Export(X509ContentType.Cert));

    await File.WriteAllTextAsync(Path.Combine(plans, "transaction-b0000000000000000000000000000000.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = "b0000000000000000000000000000000",
      version = "98.0.0",
      state = "RolledBack",
      occurredAtUtc = DateTimeOffset.UtcNow,
      reason = "The release executable is missing.",
    }, new JsonSerializerOptions(JsonSerializerDefaults.Web)));

    byte[] package = CreateValidPackage("valid");
    var unsigned = new UpdateManifest(
      1,
      "99.0.0",
      "stable",
      "treadmillrunner-99.0.0.zip",
      Convert.ToHexString(SHA256.HashData(package)),
      0,
      100,
      "Valid release after rollback",
      string.Empty);
    UpdateManifest manifest = unsigned with
    {
      Signature = Convert.ToBase64String(signingKey.SignData(
        UpdateManifestSigningPayload.Create(unsigned),
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pkcs1)),
    };
    await File.WriteAllBytesAsync(Path.Combine(feed, manifest.PackageFileName), package);
    await File.WriteAllBytesAsync(
      Path.Combine(feed, "stable.manifest.json"),
      JsonSerializer.SerializeToUtf8Bytes(manifest, new JsonSerializerOptions(JsonSerializerDefaults.Web)));

    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    await using (TreadmillRunnerDbContext context = await factory.CreateDbContextAsync())
      await context.Database.MigrateAsync();
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:FeedPath"] = feed,
        ["Updates:StagingRoot"] = staging,
        ["Updates:PlanRoot"] = plans,
        ["Updates:SigningCertificatePath"] = certificatePath,
        ["Updates:Channel"] = "stable",
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));

    ReleaseValidationResult result = await manager.CheckAsync(CancellationToken.None);

    Assert.Equal(ReleaseValidationStatus.Valid, result.Status);
    Assert.Equal(UpdateLifecycleState.Available, manager.Status.State);
    Assert.Equal("99.0.0", manager.Status.AvailableVersion);
    Assert.Equal("Valid release after rollback", manager.Status.ReleaseNotes);
    Assert.Equal("99.0.0", (await manager.StageAsync("99.0.0", CancellationToken.None)).Version);
  }

  [Fact]
  public void Terminal_artifact_retention_is_bounded_and_preserves_in_flight_or_ambiguous_state()
  {
    string plans = Path.Combine(root, "retention-plans");
    string staging = Path.Combine(root, "retention-staging");
    string backups = Path.Combine(root, "retention-backups");
    string database = Path.Combine(root, "retention.db");
    Directory.CreateDirectory(plans);
    Directory.CreateDirectory(staging);
    Directory.CreateDirectory(backups);
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:PlanRoot"] = plans,
        ["Updates:StagingRoot"] = staging,
        ["Updates:BackupRoot"] = backups,
      })
      .Build();
    var manager = new UpdateManager(
      configuration,
      TimeProvider.System,
      factory,
      new SqliteOnlineBackupService(factory));

    var terminalPaths = new List<(string Version, string TransactionId)>();
    for (int index = 0; index < 7; index++)
    {
      string version = $"91.0.{index}";
      string transactionId = index.ToString("x32");
      terminalPaths.Add((version, transactionId));
      Directory.CreateDirectory(Path.Combine(staging, version));
      File.WriteAllText(Path.Combine(staging, version, "verified-manifest.json"), "retained fixture");
      File.WriteAllText(Path.Combine(backups, $"pre-update-{transactionId}.db"), "backup");
      File.WriteAllText(
        Path.Combine(plans, $"transaction-{transactionId}.json"),
        JsonSerializer.Serialize(new
        {
          schemaVersion = 1,
          transactionId,
          version,
          state = "Activated",
          occurredAtUtc = DateTimeOffset.UtcNow.AddDays(-index),
          reason = "retention fixture",
        }));
    }

    string inFlightVersion = "92.0.0";
    string inFlightTransaction = "a".PadLeft(32, 'a');
    Directory.CreateDirectory(Path.Combine(staging, inFlightVersion));
    File.WriteAllText(Path.Combine(plans, $"transaction-{inFlightTransaction}.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = inFlightTransaction,
      version = inFlightVersion,
      state = "Activating",
      occurredAtUtc = DateTimeOffset.UtcNow,
      reason = "in flight",
    }));
    File.WriteAllText(Path.Combine(plans, "pending-activation.json"), JsonSerializer.Serialize(new
    {
      TransactionId = inFlightTransaction,
      Version = inFlightVersion,
    }));
    string failedVersion = "92.1.0";
    string failedTransaction = "d".PadLeft(32, 'd');
    Directory.CreateDirectory(Path.Combine(staging, failedVersion));
    File.WriteAllText(Path.Combine(backups, $"pre-update-{failedTransaction}.db"), "rollback evidence");
    File.WriteAllText(Path.Combine(plans, $"transaction-{failedTransaction}.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = failedTransaction,
      version = failedVersion,
      state = "RollbackFailed",
      occurredAtUtc = DateTimeOffset.UtcNow.AddDays(-1),
      reason = "rollback evidence",
    }));

    UpdateArtifactRetentionResult result = manager.PruneTerminalArtifacts();

    Assert.Equal(7, result.TerminalJournalCount);
    Assert.Equal(2, result.DeletedJournalCount);
    Assert.False(result.SkippedAmbiguous);
    foreach ((string version, string transactionId) in terminalPaths.Take(5))
    {
      Assert.True(File.Exists(Path.Combine(plans, $"transaction-{transactionId}.json")));
      Assert.True(Directory.Exists(Path.Combine(staging, version)));
      Assert.True(File.Exists(Path.Combine(backups, $"pre-update-{transactionId}.db")));
    }
    foreach ((string version, string transactionId) in terminalPaths.Skip(5))
    {
      Assert.False(File.Exists(Path.Combine(plans, $"transaction-{transactionId}.json")));
      Assert.False(Directory.Exists(Path.Combine(staging, version)));
      Assert.False(File.Exists(Path.Combine(backups, $"pre-update-{transactionId}.db")));
    }
    Assert.True(File.Exists(Path.Combine(plans, $"transaction-{inFlightTransaction}.json")));
    Assert.True(Directory.Exists(Path.Combine(staging, inFlightVersion)));
    Assert.True(File.Exists(Path.Combine(plans, $"transaction-{failedTransaction}.json")));
    Assert.True(Directory.Exists(Path.Combine(staging, failedVersion)));
    Assert.True(File.Exists(Path.Combine(backups, $"pre-update-{failedTransaction}.db")));
  }

  [Fact]
  public void Malformed_transaction_journal_fails_closed_for_all_terminal_artifacts()
  {
    string plans = Path.Combine(root, "malformed-plans");
    string staging = Path.Combine(root, "malformed-staging");
    string backups = Path.Combine(root, "malformed-backups");
    string database = Path.Combine(root, "malformed.db");
    Directory.CreateDirectory(plans);
    Directory.CreateDirectory(staging);
    Directory.CreateDirectory(backups);
    const string version = "91.0.0";
    const string transactionId = "c0000000000000000000000000000000";
    Directory.CreateDirectory(Path.Combine(staging, version));
    File.WriteAllText(Path.Combine(backups, $"pre-update-{transactionId}.db"), "backup");
    File.WriteAllText(Path.Combine(plans, $"transaction-{transactionId}.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId,
      version,
      state = "Activated",
      occurredAtUtc = DateTimeOffset.UtcNow.AddDays(-1),
      reason = "retention fixture",
    }));
    File.WriteAllText(Path.Combine(plans, "transaction-malformed.json"), "{");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
    {
      ["Updates:PlanRoot"] = plans,
      ["Updates:StagingRoot"] = staging,
      ["Updates:BackupRoot"] = backups,
    }).Build();
    var manager = new UpdateManager(configuration, TimeProvider.System, factory, new SqliteOnlineBackupService(factory));

    UpdateArtifactRetentionResult result = manager.PruneTerminalArtifacts();

    Assert.True(result.SkippedAmbiguous);
    Assert.Equal(0, result.DeletedJournalCount);
    Assert.True(File.Exists(Path.Combine(plans, $"transaction-{transactionId}.json")));
    Assert.True(Directory.Exists(Path.Combine(staging, version)));
    Assert.True(File.Exists(Path.Combine(backups, $"pre-update-{transactionId}.db")));
  }

  [Fact]
  public void Invalid_pending_plan_fails_closed_without_deleting_terminal_artifacts()
  {
    string plans = Path.Combine(root, "ambiguous-plans");
    string staging = Path.Combine(root, "ambiguous-staging");
    string backups = Path.Combine(root, "ambiguous-backups");
    string database = Path.Combine(root, "ambiguous.db");
    Directory.CreateDirectory(plans);
    Directory.CreateDirectory(staging);
    Directory.CreateDirectory(backups);
    const string version = "91.0.0";
    const string transactionId = "b0000000000000000000000000000000";
    string journalPath = Path.Combine(plans, $"transaction-{transactionId}.json");
    Directory.CreateDirectory(Path.Combine(staging, version));
    File.WriteAllText(Path.Combine(backups, $"pre-update-{transactionId}.db"), "backup");
    File.WriteAllText(journalPath, JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId,
      version,
      state = "Activated",
      occurredAtUtc = DateTimeOffset.UtcNow.AddDays(-100),
      reason = "retention fixture",
    }));
    File.WriteAllText(Path.Combine(plans, "pending-activation.json"), "{\"TransactionId\":42,\"Version\":\"91.0.0\"}");
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(database);
    IConfiguration configuration = new ConfigurationBuilder()
      .AddInMemoryCollection(new Dictionary<string, string?>
      {
        ["Persistence:DatabasePath"] = database,
        ["Updates:PlanRoot"] = plans,
        ["Updates:StagingRoot"] = staging,
        ["Updates:BackupRoot"] = backups,
      })
      .Build();
    var manager = new UpdateManager(configuration, TimeProvider.System, factory, new SqliteOnlineBackupService(factory));

    UpdateArtifactRetentionResult result = manager.PruneTerminalArtifacts();

    Assert.True(result.SkippedAmbiguous);
    Assert.True(File.Exists(journalPath));
    Assert.True(Directory.Exists(Path.Combine(staging, version)));
    Assert.True(File.Exists(Path.Combine(backups, $"pre-update-{transactionId}.db")));
  }

  public void Dispose()
  {
    certificate.Dispose();
    signingKey.Dispose();
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
  }

  private static byte[] CreatePackage(params (string Path, string Content)[] entries)
  {
    using var stream = new MemoryStream();
    using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
    {
      foreach ((string path, string content) in entries)
      {
        ZipArchiveEntry entry = archive.CreateEntry(path);
        using var writer = new StreamWriter(entry.Open());
        writer.Write(content);
      }
    }

    return stream.ToArray();
  }

  private static byte[] CreateValidPackage(string marker) => CreatePackage(
    ("TreadmillRunner.Gateway.exe", "gateway"),
    ("TreadmillRunner.Migrations.exe", "migrations"),
    ("Updates/update-helper.ps1", "helper"),
    ("Updates/service-guardian.ps1", "guardian"),
    ("app/version.txt", marker));

  private byte[] CreateSignedBundle(string version)
  {
    byte[] package = CreateValidPackage(version);
    string packageName = $"treadmillrunner-{version}-win-x64.zip";
    var unsigned = new UpdateManifest(
      1,
      version,
      "stable",
      packageName,
      Convert.ToHexString(SHA256.HashData(package)),
      0,
      100,
      "Uploaded signed fixture",
      string.Empty);
    UpdateManifest manifest = unsigned with
    {
      Signature = Convert.ToBase64String(signingKey.SignData(
        UpdateManifestSigningPayload.Create(unsigned),
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pkcs1)),
    };
    byte[] manifestBytes = JsonSerializer.SerializeToUtf8Bytes(
      manifest,
      new JsonSerializerOptions(JsonSerializerDefaults.Web));
    using var output = new MemoryStream();
    using (var archive = new ZipArchive(output, ZipArchiveMode.Create, leaveOpen: true))
    {
      ZipArchiveEntry manifestEntry = archive.CreateEntry("stable.manifest.json");
      using (Stream target = manifestEntry.Open()) target.Write(manifestBytes);
      ZipArchiveEntry packageEntry = archive.CreateEntry(packageName, CompressionLevel.NoCompression);
      using (Stream target = packageEntry.Open()) target.Write(package);
    }
    return output.ToArray();
  }
}
