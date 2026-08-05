using System.IO.Compression;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using TreadmillRunner.Core.Updates;

namespace TreadmillRunner.Infrastructure.Updates;

public sealed class ReleaseVerifier(X509Certificate2 signingCertificate) : IReleaseVerifier
{
  public const int MaximumArchiveEntries = 10_000;
  public const long MaximumPackageBytes = 1024L * 1024 * 1024;
  public const long MaximumExpandedBytes = 2L * 1024 * 1024 * 1024;
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
  {
    PropertyNameCaseInsensitive = false,
  };

  public ReleaseValidationResult VerifyManifest(
    ReadOnlySpan<byte> manifestJson,
    UpdateCheckContext context)
  {
    UpdateManifest? manifest;
    try
    {
      manifest = JsonSerializer.Deserialize<UpdateManifest>(manifestJson, JsonOptions);
    }
    catch (JsonException exception)
    {
      return Invalid(ReleaseValidationStatus.InvalidManifest, exception.Message);
    }

    if (manifest is null ||
        manifest.SchemaVersion != 1 ||
        string.IsNullOrWhiteSpace(manifest.Version) ||
        string.IsNullOrWhiteSpace(manifest.Channel) ||
        string.IsNullOrWhiteSpace(manifest.PackageFileName) ||
        manifest.PackageSha256.Length != 64 ||
        !manifest.PackageSha256.All(Uri.IsHexDigit) ||
        manifest.MinimumDatabaseSchemaVersion < 0 ||
        manifest.MaximumDatabaseSchemaVersion < manifest.MinimumDatabaseSchemaVersion ||
        !string.Equals(Path.GetFileName(manifest.PackageFileName), manifest.PackageFileName, StringComparison.Ordinal) ||
        !manifest.PackageFileName.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
    {
      return Invalid(ReleaseValidationStatus.InvalidManifest, "The release manifest is malformed.");
    }

    if (!string.Equals(manifest.Channel, context.Channel, StringComparison.Ordinal))
    {
      return Invalid(ReleaseValidationStatus.InvalidManifest, "The release channel does not match.");
    }

    if (!Version.TryParse(manifest.Version, out Version? releaseVersion) ||
        releaseVersion.Build < 0 ||
        releaseVersion.Revision >= 0 ||
        !string.Equals(releaseVersion.ToString(3), manifest.Version, StringComparison.Ordinal))
    {
      return Invalid(ReleaseValidationStatus.InvalidManifest, "The release version is invalid.");
    }

    if (releaseVersion <= context.CurrentVersion)
    {
      return new ReleaseValidationResult(ReleaseValidationStatus.NotNewer, "The release is not newer.", manifest);
    }

    if (context.CurrentDatabaseSchemaVersion < manifest.MinimumDatabaseSchemaVersion ||
        context.CurrentDatabaseSchemaVersion > manifest.MaximumDatabaseSchemaVersion)
    {
      return new ReleaseValidationResult(ReleaseValidationStatus.UnsupportedSchema, "The database schema is outside the supported range.", manifest);
    }

    try
    {
      byte[] signature = Convert.FromBase64String(manifest.Signature);
      using RSA? rsa = signingCertificate.GetRSAPublicKey();
      if (rsa is null || !rsa.VerifyData(
        UpdateManifestSigningPayload.Create(manifest),
        signature,
        HashAlgorithmName.SHA256,
        RSASignaturePadding.Pkcs1))
      {
        return new ReleaseValidationResult(ReleaseValidationStatus.InvalidSignature, "The manifest signature is invalid.", manifest);
      }
    }
    catch (FormatException)
    {
      return new ReleaseValidationResult(ReleaseValidationStatus.InvalidSignature, "The manifest signature is invalid.", manifest);
    }

    return new ReleaseValidationResult(ReleaseValidationStatus.Valid, "The release manifest is valid.", manifest);
  }

  public async Task<ReleaseValidationResult> VerifyPackageAsync(
    UpdateManifest manifest,
    Stream package,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(manifest);
    ArgumentNullException.ThrowIfNull(package);
    FileStream? bufferedPackage = null;
    Stream archiveStream = package;
    if (!package.CanSeek)
    {
      string bufferRoot = Path.Combine(Path.GetTempPath(), "TreadmillRunner", "update-validation");
      Directory.CreateDirectory(bufferRoot);
      bufferedPackage = new FileStream(
        Path.Combine(bufferRoot, $"{Guid.NewGuid():N}.tmp"),
        FileMode.CreateNew,
        FileAccess.ReadWrite,
        FileShare.Read,
        64 * 1024,
        FileOptions.Asynchronous | FileOptions.SequentialScan | FileOptions.DeleteOnClose);
      var buffer = new byte[64 * 1024];
      long copied = 0;
      int read;
      while ((read = await package.ReadAsync(buffer, cancellationToken)) > 0)
      {
        copied += read;
        if (copied > MaximumPackageBytes)
        {
          bufferedPackage.Dispose();
          return Unsafe("The update package is too large.", manifest);
        }

        await bufferedPackage.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
      }

      bufferedPackage.Position = 0;
      archiveStream = bufferedPackage;
    }
    else if (package.Length - package.Position > MaximumPackageBytes)
    {
      return Unsafe("The update package is too large.", manifest);
    }

    long packageStart = archiveStream.Position;
    byte[] hash = await SHA256.HashDataAsync(archiveStream, cancellationToken);
    archiveStream.Position = packageStart;
    string actualHash = Convert.ToHexString(hash);
    if (!string.Equals(actualHash, manifest.PackageSha256, StringComparison.OrdinalIgnoreCase))
    {
      bufferedPackage?.Dispose();
      return new ReleaseValidationResult(ReleaseValidationStatus.HashMismatch, "The package hash does not match.", manifest);
    }

    try
    {
      using var archive = new ZipArchive(archiveStream, ZipArchiveMode.Read, leaveOpen: true);
      if (archive.Entries.Count > MaximumArchiveEntries)
      {
        return Unsafe("The archive contains too many entries.", manifest);
      }

      var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
      long expandedBytes = 0;
      foreach (ZipArchiveEntry entry in archive.Entries)
      {
        string path = entry.FullName.Replace('\\', '/');
        string[] segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (string.IsNullOrWhiteSpace(path) || path.StartsWith('/') || path.Contains(':') ||
            segments.Any(static segment => segment is "." or "..") || !paths.Add(path))
        {
          return Unsafe($"The archive entry '{entry.FullName}' is unsafe.", manifest);
        }

        expandedBytes = checked(expandedBytes + entry.Length);
        if (expandedBytes > MaximumExpandedBytes)
        {
          return Unsafe("The expanded archive is too large.", manifest);
        }
      }

      foreach (string requiredPath in new[]
      {
        "TreadmillRunner.Gateway.exe",
        "TreadmillRunner.Migrations.exe",
        "Updates/update-helper.ps1",
      })
      {
        if (!paths.Contains(requiredPath))
          return Unsafe($"The archive is missing required entry '{requiredPath}'.", manifest);
      }
    }
    catch (Exception exception) when (exception is InvalidDataException or OverflowException)
    {
      return Unsafe(exception.Message, manifest);
    }
    finally
    {
      bufferedPackage?.Dispose();
    }

    return new ReleaseValidationResult(ReleaseValidationStatus.Valid, "The release package is valid.", manifest);
  }

  private static ReleaseValidationResult Invalid(ReleaseValidationStatus status, string message) =>
    new(status, message);

  private static ReleaseValidationResult Unsafe(string message, UpdateManifest manifest) =>
    new(ReleaseValidationStatus.UnsafeArchive, message, manifest);
}
