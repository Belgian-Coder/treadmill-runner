using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;
using TreadmillRunner.Core.Updates;

namespace TreadmillRunner.Infrastructure.Updates;

public sealed partial class GitHubReleaseUpdateFeed : IUpdateFeed
{
  public const int MaximumReleaseMetadataBytes = 1024 * 1024;
  private const string ApiVersion = "2026-03-10";
  private readonly HttpClient _httpClient;
  private readonly string _owner;
  private readonly string _repository;
  private readonly Uri _latestReleaseUri;

  public GitHubReleaseUpdateFeed(HttpClient httpClient, string owner, string repository)
  {
    _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
    _owner = ValidateRepositoryPart(owner, nameof(owner));
    _repository = ValidateRepositoryPart(repository, nameof(repository));
    _latestReleaseUri = new Uri($"https://api.github.com/repos/{_owner}/{_repository}/releases/latest");
  }

  public async Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
    string channel,
    CancellationToken cancellationToken = default)
  {
    if (!ChannelPattern().IsMatch(channel))
      throw new ArgumentException("The update channel contains unsupported characters.", nameof(channel));

    using HttpResponseMessage response = await SendAsync(_latestReleaseUri, cancellationToken);
    if (response.StatusCode == HttpStatusCode.NotFound) return null;
    if (!response.IsSuccessStatusCode)
      throw new UpdateFeedUnavailableException($"GitHub Releases returned HTTP {(int)response.StatusCode}.");

    byte[] metadata = await ReadBoundedAsync(
      response.Content,
      MaximumReleaseMetadataBytes,
      "The GitHub release metadata is too large.",
      cancellationToken);
    try
    {
      using JsonDocument document = JsonDocument.Parse(metadata);
      JsonElement assets = document.RootElement.GetProperty("assets");
      if (assets.ValueKind != JsonValueKind.Array || assets.GetArrayLength() > 1000)
        throw new InvalidDataException("The GitHub release asset list is invalid or too large.");

      var releaseAssets = new Dictionary<string, Uri>(StringComparer.Ordinal);
      foreach (JsonElement asset in assets.EnumerateArray())
      {
        string? name = asset.TryGetProperty("name", out JsonElement nameElement)
          ? nameElement.GetString()
          : null;
        string? download = asset.TryGetProperty("browser_download_url", out JsonElement urlElement)
          ? urlElement.GetString()
          : null;
        if (string.IsNullOrWhiteSpace(name) || string.IsNullOrWhiteSpace(download) ||
            !string.Equals(Path.GetFileName(name), name, StringComparison.Ordinal) ||
            !Uri.TryCreate(download, UriKind.Absolute, out Uri? uri) ||
            !IsAllowedAssetUri(uri) ||
            !releaseAssets.TryAdd(name, uri))
        {
          throw new InvalidDataException("The GitHub release contains an invalid or duplicate asset.");
        }
      }

      string manifestName = $"{channel}.manifest.json";
      if (!releaseAssets.TryGetValue(manifestName, out Uri? manifestUri)) return null;
      byte[] manifest = await DownloadBytesAsync(
        manifestUri,
        LocalFolderUpdateFeed.MaximumManifestBytes,
        "The GitHub update manifest is too large.",
        cancellationToken);
      return new GitHubUpdateFeedRelease(
        _httpClient,
        $"GitHub Releases ({_owner}/{_repository})",
        manifest,
        releaseAssets,
        _owner,
        _repository);
    }
    catch (Exception exception) when (exception is JsonException or KeyNotFoundException or InvalidDataException)
    {
      throw new UpdateFeedUnavailableException("The GitHub release metadata is invalid.", exception);
    }
  }

  private async Task<HttpResponseMessage> SendAsync(Uri uri, CancellationToken cancellationToken)
  {
    try
    {
      using var request = new HttpRequestMessage(HttpMethod.Get, uri);
      request.Headers.Accept.ParseAdd("application/vnd.github+json");
      request.Headers.UserAgent.ParseAdd("TreadmillRunner-UpdateClient/1.0");
      request.Headers.TryAddWithoutValidation("X-GitHub-Api-Version", ApiVersion);
      return await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
    {
      throw new UpdateFeedUnavailableException("GitHub Releases is unavailable.", exception);
    }
  }

  private async Task<byte[]> DownloadBytesAsync(
    Uri uri,
    int maximumBytes,
    string tooLargeMessage,
    CancellationToken cancellationToken)
  {
    using HttpResponseMessage response = await SendAsync(uri, cancellationToken);
    if (!response.IsSuccessStatusCode)
      throw new UpdateFeedUnavailableException($"A GitHub release asset returned HTTP {(int)response.StatusCode}.");
    return await ReadBoundedAsync(response.Content, maximumBytes, tooLargeMessage, cancellationToken);
  }

  private static async Task<byte[]> ReadBoundedAsync(
    HttpContent content,
    int maximumBytes,
    string tooLargeMessage,
    CancellationToken cancellationToken)
  {
    if (content.Headers.ContentLength is > 0 and var length && length > maximumBytes)
      throw new InvalidDataException(tooLargeMessage);
    await using Stream input = await content.ReadAsStreamAsync(cancellationToken);
    using var output = new MemoryStream(Math.Min(maximumBytes, 64 * 1024));
    var buffer = new byte[32 * 1024];
    int read;
    while ((read = await input.ReadAsync(buffer, cancellationToken)) > 0)
    {
      if (output.Length + read > maximumBytes) throw new InvalidDataException(tooLargeMessage);
      await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
    }
    return output.ToArray();
  }

  private bool IsAllowedAssetUri(Uri uri)
  {
    if (!string.Equals(uri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase) ||
        !string.Equals(uri.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
        !string.IsNullOrEmpty(uri.UserInfo))
      return false;
    string prefix = $"/{_owner}/{_repository}/releases/download/";
    return uri.AbsolutePath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
  }

  private static string ValidateRepositoryPart(string value, string parameterName) =>
    !string.IsNullOrWhiteSpace(value) && RepositoryPartPattern().IsMatch(value)
      ? value
      : throw new ArgumentException("The GitHub owner or repository name is invalid.", parameterName);

  [GeneratedRegex("^[A-Za-z0-9_.-]{1,100}$", RegexOptions.CultureInvariant)]
  private static partial Regex RepositoryPartPattern();

  [GeneratedRegex("^[a-z0-9][a-z0-9._-]{0,31}$", RegexOptions.CultureInvariant)]
  private static partial Regex ChannelPattern();

  private sealed class GitHubUpdateFeedRelease(
    HttpClient httpClient,
    string source,
    ReadOnlyMemory<byte> manifestContent,
    IReadOnlyDictionary<string, Uri> assets,
    string owner,
    string repository) : IUpdateFeedRelease
  {
    public string Source { get; } = source;
    public ReadOnlyMemory<byte> ManifestContent { get; } = manifestContent;

    public async Task<Stream> OpenPackageAsync(
      string packageFileName,
      CancellationToken cancellationToken = default)
    {
      if (string.IsNullOrWhiteSpace(packageFileName) ||
          !string.Equals(Path.GetFileName(packageFileName), packageFileName, StringComparison.Ordinal) ||
          !assets.TryGetValue(packageFileName, out Uri? assetUri))
        throw new UpdateFeedUnavailableException("The signed package is missing from the selected GitHub release.");

      string expectedPrefix = $"/{owner}/{repository}/releases/download/";
      if (!string.Equals(assetUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase) ||
          !string.Equals(assetUri.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
          !assetUri.AbsolutePath.StartsWith(expectedPrefix, StringComparison.OrdinalIgnoreCase))
        throw new UpdateFeedUnavailableException("The GitHub package asset URL is outside the configured repository.");

      using var request = new HttpRequestMessage(HttpMethod.Get, assetUri);
      request.Headers.Accept.ParseAdd("application/octet-stream");
      request.Headers.UserAgent.ParseAdd("TreadmillRunner-UpdateClient/1.0");
      HttpResponseMessage response;
      try
      {
        response = await httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
      catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
      {
        throw new UpdateFeedUnavailableException("The GitHub update package is unavailable.", exception);
      }
      using (response)
      {
        if (!response.IsSuccessStatusCode)
          throw new UpdateFeedUnavailableException($"The GitHub update package returned HTTP {(int)response.StatusCode}.");
        if (response.Content.Headers.ContentLength is > ReleaseVerifier.MaximumPackageBytes)
          throw new InvalidDataException("The GitHub update package is too large.");

        string tempRoot = Path.Combine(Path.GetTempPath(), "TreadmillRunner", "github-updates");
        Directory.CreateDirectory(tempRoot);
        string tempPath = Path.Combine(tempRoot, $"{Guid.NewGuid():N}.tmp");
        var output = new FileStream(
          tempPath,
          FileMode.CreateNew,
          FileAccess.ReadWrite,
          FileShare.Read,
          64 * 1024,
          FileOptions.Asynchronous | FileOptions.SequentialScan | FileOptions.DeleteOnClose);
        try
        {
          await using Stream input = await response.Content.ReadAsStreamAsync(cancellationToken);
          var buffer = new byte[64 * 1024];
          long copied = 0;
          int read;
          while ((read = await input.ReadAsync(buffer, cancellationToken)) > 0)
          {
            copied += read;
            if (copied > ReleaseVerifier.MaximumPackageBytes)
              throw new InvalidDataException("The GitHub update package is too large.");
            await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
          }
          await output.FlushAsync(cancellationToken);
          output.Position = 0;
          return output;
        }
        catch
        {
          await output.DisposeAsync();
          throw;
        }
      }
    }
  }
}
