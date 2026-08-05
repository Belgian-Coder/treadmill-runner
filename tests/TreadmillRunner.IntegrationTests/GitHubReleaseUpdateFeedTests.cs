using System.Net;
using System.Text;
using TreadmillRunner.Core.Updates;
using TreadmillRunner.Infrastructure.Updates;

namespace TreadmillRunner.IntegrationTests;

public sealed class GitHubReleaseUpdateFeedTests
{
  [Fact]
  public async Task GitHub_release_binds_manifest_and_package_to_the_same_configured_repository()
  {
    byte[] manifest = "{\"version\":\"2.0.0\"}"u8.ToArray();
    byte[] package = "signed-package"u8.ToArray();
    var handler = new StubHandler(request => request.RequestUri!.AbsolutePath switch
    {
      "/repos/belgian-coder/treadmill-runner/releases/latest" => Json("""
        {"assets":[
          {"name":"stable.manifest.json","browser_download_url":"https://github.com/belgian-coder/treadmill-runner/releases/download/v2.0.0/stable.manifest.json"},
          {"name":"treadmillrunner-2.0.0-win-x64.zip","browser_download_url":"https://github.com/belgian-coder/treadmill-runner/releases/download/v2.0.0/treadmillrunner-2.0.0-win-x64.zip"}
        ]}
        """),
      "/belgian-coder/treadmill-runner/releases/download/v2.0.0/stable.manifest.json" => Bytes(manifest),
      "/belgian-coder/treadmill-runner/releases/download/v2.0.0/treadmillrunner-2.0.0-win-x64.zip" => Bytes(package),
      _ => new HttpResponseMessage(HttpStatusCode.NotFound),
    });
    var feed = new GitHubReleaseUpdateFeed(new HttpClient(handler), "belgian-coder", "treadmill-runner");

    IUpdateFeedRelease release = Assert.IsAssignableFrom<IUpdateFeedRelease>(
      await feed.ReadLatestReleaseAsync("stable"));
    Assert.Equal(manifest, release.ManifestContent.ToArray());
    Assert.Contains("belgian-coder/treadmill-runner", release.Source, StringComparison.Ordinal);
    await using Stream downloaded = await release.OpenPackageAsync("treadmillrunner-2.0.0-win-x64.zip");
    using var output = new MemoryStream();
    await downloaded.CopyToAsync(output);
    Assert.Equal(package, output.ToArray());
    Assert.All(handler.Requests, request => Assert.False(request.Headers.Contains("Authorization")));
  }

  [Fact]
  public async Task GitHub_release_rejects_assets_outside_the_configured_repository_and_treats_404_as_empty()
  {
    var unsafeFeed = new GitHubReleaseUpdateFeed(new HttpClient(new StubHandler(_ => Json("""
      {"assets":[{"name":"stable.manifest.json","browser_download_url":"https://example.com/stable.manifest.json"}]}
      """))), "belgian-coder", "treadmill-runner");
    await Assert.ThrowsAsync<UpdateFeedUnavailableException>(() => unsafeFeed.ReadLatestReleaseAsync("stable"));

    var emptyFeed = new GitHubReleaseUpdateFeed(new HttpClient(new StubHandler(_ =>
      new HttpResponseMessage(HttpStatusCode.NotFound))), "belgian-coder", "treadmill-runner");
    Assert.Null(await emptyFeed.ReadLatestReleaseAsync("stable"));
  }

  [Fact]
  public async Task Fallback_uses_local_only_when_primary_is_unavailable_or_has_no_release()
  {
    var localRelease = new StubRelease("Local folder", "{}"u8.ToArray());
    var fallback = new FallbackUpdateFeed(
      new StubFeed(new UpdateFeedUnavailableException("offline")),
      new StubFeed(localRelease));
    Assert.Same(localRelease, await fallback.ReadLatestReleaseAsync("stable"));

    var present = new StubRelease("GitHub", "invalid-but-present"u8.ToArray());
    var primaryWins = new FallbackUpdateFeed(new StubFeed(present), new StubFeed(localRelease));
    Assert.Same(present, await primaryWins.ReadLatestReleaseAsync("stable"));
  }

  private static HttpResponseMessage Json(string value) => new(HttpStatusCode.OK)
  {
    Content = new StringContent(value, Encoding.UTF8, "application/json"),
  };

  private static HttpResponseMessage Bytes(byte[] value) => new(HttpStatusCode.OK)
  {
    Content = new ByteArrayContent(value),
  };

  private sealed class StubHandler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
  {
    public List<HttpRequestMessage> Requests { get; } = [];

    protected override Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken)
    {
      Requests.Add(request);
      return Task.FromResult(response(request));
    }
  }

  private sealed class StubFeed(object result) : IUpdateFeed
  {
    public Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
      string channel,
      CancellationToken cancellationToken = default) => result switch
      {
        Exception exception => Task.FromException<IUpdateFeedRelease?>(exception),
        IUpdateFeedRelease release => Task.FromResult<IUpdateFeedRelease?>(release),
        _ => Task.FromResult<IUpdateFeedRelease?>(null),
      };
  }

  private sealed class StubRelease(
    string source,
    ReadOnlyMemory<byte> manifestContent) : IUpdateFeedRelease
  {
    public string Source { get; } = source;
    public ReadOnlyMemory<byte> ManifestContent { get; } = manifestContent;
    public Task<Stream> OpenPackageAsync(string packageFileName, CancellationToken cancellationToken = default) =>
      Task.FromResult<Stream>(new MemoryStream());
  }
}
