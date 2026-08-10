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

    await File.WriteAllTextAsync(Path.Combine(plans, "transaction-broken.json"), JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      transactionId = "broken",
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
