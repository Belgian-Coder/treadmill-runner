using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using TreadmillRunner.Core.Updates;
using TreadmillRunner.Infrastructure.Updates;

namespace TreadmillRunner.IntegrationTests;

public sealed class ReleaseVerifierTests : IDisposable
{
  private readonly RSA _signingKey = RSA.Create(2048);
  private readonly X509Certificate2 _certificate;

  public ReleaseVerifierTests()
  {
    var request = new CertificateRequest(
      "CN=TreadmillRunner Update Test",
      _signingKey,
      HashAlgorithmName.SHA256,
      RSASignaturePadding.Pkcs1);
    _certificate = request.CreateSelfSigned(
      DateTimeOffset.UtcNow.AddMinutes(-1),
      DateTimeOffset.UtcNow.AddDays(1));
  }

  [Fact]
  public async Task Valid_manifest_and_package_pass_signature_hash_and_archive_validation()
  {
    byte[] package = ValidPackage();
    UpdateManifest manifest = SignedManifest(package);
    var verifier = new ReleaseVerifier(_certificate);

    ReleaseValidationResult manifestResult = verifier.VerifyManifest(
      JsonSerializer.SerializeToUtf8Bytes(manifest, JsonOptions),
      Context());
    ReleaseValidationResult packageResult = await verifier.VerifyPackageAsync(
      manifest,
      new MemoryStream(package));

    Assert.Equal(ReleaseValidationStatus.Valid, manifestResult.Status);
    Assert.Equal(ReleaseValidationStatus.Valid, packageResult.Status);
  }

  [Fact]
  public void Manifest_rejects_bad_signature_schema_range_and_non_newer_version()
  {
    byte[] package = Package(("app/file.txt", "data"));
    UpdateManifest valid = SignedManifest(package);
    var verifier = new ReleaseVerifier(_certificate);

    UpdateManifest tampered = valid with { ReleaseNotes = "tampered after signing" };
    Assert.Equal(
      ReleaseValidationStatus.InvalidSignature,
      verifier.VerifyManifest(JsonSerializer.SerializeToUtf8Bytes(tampered, JsonOptions), Context()).Status);
    Assert.Equal(
      ReleaseValidationStatus.UnsupportedSchema,
      verifier.VerifyManifest(
        JsonSerializer.SerializeToUtf8Bytes(SignedManifest(package, minimumSchema: 6), JsonOptions),
        Context()).Status);
    Assert.Equal(
      ReleaseValidationStatus.NotNewer,
      verifier.VerifyManifest(
        JsonSerializer.SerializeToUtf8Bytes(SignedManifest(package, version: "1.0.0"), JsonOptions),
        Context()).Status);
  }

  [Fact]
  public void Manifest_requires_exact_three_part_version_and_zip_package_name()
  {
    byte[] package = Package(("app/file.txt", "data"));
    var verifier = new ReleaseVerifier(_certificate);

    Assert.Equal(
      ReleaseValidationStatus.InvalidManifest,
      verifier.VerifyManifest(
        JsonSerializer.SerializeToUtf8Bytes(SignedManifest(package, version: "2.0"), JsonOptions),
        Context()).Status);
    UpdateManifest wrongSuffix = SignedManifest(package) with { PackageFileName = "release.bin" };
    Assert.Equal(
      ReleaseValidationStatus.InvalidManifest,
      verifier.VerifyManifest(JsonSerializer.SerializeToUtf8Bytes(wrongSuffix, JsonOptions), Context()).Status);
  }

  [Fact]
  public async Task Package_rejects_hash_mismatch_and_path_traversal()
  {
    byte[] safePackage = Package(("app/file.txt", "data"));
    var verifier = new ReleaseVerifier(_certificate);
    UpdateManifest safeManifest = SignedManifest(safePackage);
    byte[] changedPackage = Package(("app/file.txt", "changed"));

    Assert.Equal(
      ReleaseValidationStatus.HashMismatch,
      (await verifier.VerifyPackageAsync(safeManifest, new MemoryStream(changedPackage))).Status);

    byte[] unsafePackage = Package(("../outside.txt", "escape"));
    UpdateManifest unsafeManifest = SignedManifest(unsafePackage);
    Assert.Equal(
      ReleaseValidationStatus.UnsafeArchive,
      (await verifier.VerifyPackageAsync(unsafeManifest, new MemoryStream(unsafePackage))).Status);
  }

  [Fact]
  public async Task Package_rejects_missing_activation_assets()
  {
    byte[] package = Package(("TreadmillRunner.Gateway.exe", "binary"));
    UpdateManifest manifest = SignedManifest(package);

    ReleaseValidationResult result = await new ReleaseVerifier(_certificate)
      .VerifyPackageAsync(manifest, new MemoryStream(package));

    Assert.Equal(ReleaseValidationStatus.UnsafeArchive, result.Status);
    Assert.Contains("TreadmillRunner.Migrations.exe", result.Message, StringComparison.Ordinal);

    byte[] missingGuardian = Package(
      ("TreadmillRunner.Gateway.exe", "binary"),
      ("TreadmillRunner.Migrations.exe", "migrations"),
      ("Updates/update-helper.ps1", "helper"));
    ReleaseValidationResult guardianResult = await new ReleaseVerifier(_certificate)
      .VerifyPackageAsync(SignedManifest(missingGuardian), new MemoryStream(missingGuardian));
    Assert.Equal(ReleaseValidationStatus.UnsafeArchive, guardianResult.Status);
    Assert.Contains("Updates/service-guardian.ps1", guardianResult.Message, StringComparison.Ordinal);
  }

  [Fact]
  public async Task Local_feed_is_bounded_and_treats_missing_release_as_no_update()
  {
    string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.UpdateTests", Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    try
    {
      var feed = new LocalFolderUpdateFeed(directory);
      Assert.Null(await feed.ReadLatestReleaseAsync("stable"));

      byte[] content = "{}"u8.ToArray();
      await File.WriteAllBytesAsync(Path.Combine(directory, "stable.manifest.json"), content);
      IUpdateFeedRelease found = Assert.IsAssignableFrom<IUpdateFeedRelease>(await feed.ReadLatestReleaseAsync("stable"));
      Assert.Equal(content, found.ManifestContent.ToArray());
      await Assert.ThrowsAsync<ArgumentException>(() => feed.ReadLatestReleaseAsync("../escape"));
      await Assert.ThrowsAsync<ArgumentException>(() => found.OpenPackageAsync("../escape.zip"));
      var unavailable = new LocalFolderUpdateFeed(Path.Combine(directory, "missing"));
      await Assert.ThrowsAsync<UpdateFeedUnavailableException>(() =>
        unavailable.ReadLatestReleaseAsync("stable"));
    }
    finally
    {
      Directory.Delete(directory, recursive: true);
    }
  }

  public void Dispose()
  {
    _certificate.Dispose();
    _signingKey.Dispose();
  }

  private UpdateManifest SignedManifest(
    byte[] package,
    string version = "2.0.0",
    int minimumSchema = 1)
  {
    var unsigned = new UpdateManifest(
      1,
      version,
      "stable",
      "treadmillrunner-2.0.0.zip",
      Convert.ToHexString(SHA256.HashData(package)),
      minimumSchema,
      10,
      "Test release",
      string.Empty);
    byte[] signature = _signingKey.SignData(
      UpdateManifestSigningPayload.Create(unsigned),
      HashAlgorithmName.SHA256,
      RSASignaturePadding.Pkcs1);
    return unsigned with { Signature = Convert.ToBase64String(signature) };
  }

  private static UpdateCheckContext Context() => new(new Version(1, 0, 0), "stable", 5);

  private static byte[] Package(params (string Path, string Content)[] entries)
  {
    using var stream = new MemoryStream();
    using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, leaveOpen: true))
    {
      foreach ((string path, string content) in entries)
      {
        ZipArchiveEntry entry = archive.CreateEntry(path);
        using StreamWriter writer = new(entry.Open());
        writer.Write(content);
      }
    }

    return stream.ToArray();
  }

  private static byte[] ValidPackage() => Package(
    ("TreadmillRunner.Gateway.exe", "gateway"),
    ("TreadmillRunner.Migrations.exe", "migrations"),
    ("Updates/update-helper.ps1", "helper"),
    ("Updates/service-guardian.ps1", "guardian"));

  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
}
