using System.Text.RegularExpressions;
using TreadmillRunner.Core.Updates;

namespace TreadmillRunner.Infrastructure.Updates;

public sealed partial class LocalFolderUpdateFeed(string rootPath) : IUpdateFeed
{
  public const int MaximumManifestBytes = 256 * 1024;
  private readonly string _rootPath = Path.GetFullPath(
    string.IsNullOrWhiteSpace(rootPath) ? throw new ArgumentException("An update-feed path is required.", nameof(rootPath)) : rootPath);

  public async Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
    string channel,
    CancellationToken cancellationToken = default)
  {
    if (!ChannelPattern().IsMatch(channel))
    {
      throw new ArgumentException("The update channel contains unsupported characters.", nameof(channel));
    }

    if (!Directory.Exists(_rootPath))
    {
      throw new UpdateFeedUnavailableException($"The update feed '{_rootPath}' is unavailable.");
    }

    string path = Path.Combine(_rootPath, $"{channel}.manifest.json");
    try
    {
      if (!File.Exists(path)) return null;
      var information = new FileInfo(path);
      if (information.Length > MaximumManifestBytes)
      {
        throw new InvalidDataException($"The update manifest exceeds {MaximumManifestBytes} bytes.");
      }

      byte[] content = await File.ReadAllBytesAsync(path, cancellationToken);
      return new LocalFolderUpdateFeedRelease(_rootPath, path, content);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
    {
      throw new UpdateFeedUnavailableException($"The update feed '{_rootPath}' is unavailable.", exception);
    }
  }

  private sealed class LocalFolderUpdateFeedRelease(
    string rootPath,
    string source,
    ReadOnlyMemory<byte> manifestContent) : IUpdateFeedRelease
  {
    public string Source { get; } = source;
    public ReadOnlyMemory<byte> ManifestContent { get; } = manifestContent;

    public Task<Stream> OpenPackageAsync(
      string packageFileName,
      CancellationToken cancellationToken = default)
    {
      cancellationToken.ThrowIfCancellationRequested();
      if (string.IsNullOrWhiteSpace(packageFileName) ||
          !string.Equals(Path.GetFileName(packageFileName), packageFileName, StringComparison.Ordinal) ||
          packageFileName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
      {
        throw new ArgumentException("The package name must be a safe file name without a path.", nameof(packageFileName));
      }

      string path = Path.Combine(rootPath, packageFileName);
      try
      {
        return Task.FromResult<Stream>(new FileStream(
          path,
          FileMode.Open,
          FileAccess.Read,
          FileShare.Read,
          bufferSize: 64 * 1024,
          FileOptions.Asynchronous | FileOptions.SequentialScan));
      }
      catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
      {
        throw new UpdateFeedUnavailableException($"The update package '{packageFileName}' is unavailable.", exception);
      }
    }
  }

  [GeneratedRegex("^[a-z0-9][a-z0-9._-]{0,31}$", RegexOptions.CultureInvariant)]
  private static partial Regex ChannelPattern();
}
