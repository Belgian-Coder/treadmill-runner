using System.Globalization;
using System.Text;

namespace TreadmillRunner.Core.Updates;

public sealed record UpdateManifest(
  int SchemaVersion,
  string Version,
  string Channel,
  string PackageFileName,
  string PackageSha256,
  int MinimumDatabaseSchemaVersion,
  int MaximumDatabaseSchemaVersion,
  string ReleaseNotes,
  string Signature);

public sealed record UpdateCheckContext(
  Version CurrentVersion,
  string Channel,
  int CurrentDatabaseSchemaVersion);

public enum ReleaseValidationStatus
{
  Valid,
  InvalidManifest,
  InvalidSignature,
  HashMismatch,
  UnsupportedSchema,
  NotNewer,
  UnsafeArchive,
}

public sealed record ReleaseValidationResult(
  ReleaseValidationStatus Status,
  string Message,
  UpdateManifest? Manifest = null)
{
  public bool IsValid => Status == ReleaseValidationStatus.Valid;
}

public interface IUpdateFeedRelease
{
  string Source { get; }
  ReadOnlyMemory<byte> ManifestContent { get; }

  Task<Stream> OpenPackageAsync(
    string packageFileName,
    CancellationToken cancellationToken = default);
}

public interface IUpdateFeed
{
  Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
    string channel,
    CancellationToken cancellationToken = default);
}

public interface IReleaseVerifier
{
  ReleaseValidationResult VerifyManifest(
    ReadOnlySpan<byte> manifestJson,
    UpdateCheckContext context);

  Task<ReleaseValidationResult> VerifyPackageAsync(
    UpdateManifest manifest,
    Stream package,
    CancellationToken cancellationToken = default);
}

public static class UpdateManifestSigningPayload
{
  public static byte[] Create(UpdateManifest manifest)
  {
    ArgumentNullException.ThrowIfNull(manifest);
    string payload = string.Join('\n',
      manifest.SchemaVersion.ToString(CultureInfo.InvariantCulture),
      manifest.Version,
      manifest.Channel,
      manifest.PackageFileName,
      manifest.PackageSha256.ToUpperInvariant(),
      manifest.MinimumDatabaseSchemaVersion.ToString(CultureInfo.InvariantCulture),
      manifest.MaximumDatabaseSchemaVersion.ToString(CultureInfo.InvariantCulture),
      manifest.ReleaseNotes
        .Replace("\r\n", "\n", StringComparison.Ordinal)
        .Replace('\r', '\n'));
    return Encoding.UTF8.GetBytes(payload);
  }
}

public sealed class UpdateFeedUnavailableException(string message, Exception? innerException = null) :
  IOException(message, innerException);
