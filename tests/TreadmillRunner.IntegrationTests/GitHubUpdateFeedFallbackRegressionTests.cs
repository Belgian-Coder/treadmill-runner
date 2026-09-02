using System.Net;
using TreadmillRunner.Core.Updates;
using TreadmillRunner.Infrastructure.Updates;

namespace TreadmillRunner.IntegrationTests;

public sealed class GitHubUpdateFeedFallbackRegressionTests
{
  [Fact]
  public async Task Oversized_GitHub_metadata_is_unavailable_and_uses_the_local_fallback()
  {
    using var client = new HttpClient(new OversizedMetadataHandler());
    var github = new GitHubReleaseUpdateFeed(client, "belgian-coder", "treadmill-runner");
    var localRelease = new StubRelease();
    var fallback = new FallbackUpdateFeed(github, new StubFeed(localRelease));

    IUpdateFeedRelease release = Assert.IsAssignableFrom<IUpdateFeedRelease>(
      await fallback.ReadLatestReleaseAsync("stable"));

    Assert.Same(localRelease, release);
  }

  [Fact]
  public async Task Failed_GitHub_metadata_stream_uses_the_local_fallback()
  {
    using var client = new HttpClient(new FailedMetadataStreamHandler());
    var github = new GitHubReleaseUpdateFeed(client, "belgian-coder", "treadmill-runner");
    var localRelease = new StubRelease();
    var fallback = new FallbackUpdateFeed(github, new StubFeed(localRelease));

    IUpdateFeedRelease release = Assert.IsAssignableFrom<IUpdateFeedRelease>(
      await fallback.ReadLatestReleaseAsync("stable"));

    Assert.Same(localRelease, release);
  }

  [Fact]
  public async Task Wrong_shaped_GitHub_metadata_uses_the_local_fallback()
  {
    using var client = new HttpClient(new WrongShapedMetadataHandler());
    var github = new GitHubReleaseUpdateFeed(client, "belgian-coder", "treadmill-runner");
    var localRelease = new StubRelease();
    var fallback = new FallbackUpdateFeed(github, new StubFeed(localRelease));

    IUpdateFeedRelease release = Assert.IsAssignableFrom<IUpdateFeedRelease>(
      await fallback.ReadLatestReleaseAsync("stable"));

    Assert.Same(localRelease, release);
  }

  private sealed class OversizedMetadataHandler : HttpMessageHandler
  {
    protected override Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken) =>
      Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
      {
        Content = new ByteArrayContent(
          new byte[GitHubReleaseUpdateFeed.MaximumReleaseMetadataBytes + 1]),
      });
  }

  private sealed class FailedMetadataStreamHandler : HttpMessageHandler
  {
    protected override Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken) =>
      Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
      {
        Content = new StreamContent(new FailedReadStream()),
      });
  }

  private sealed class WrongShapedMetadataHandler : HttpMessageHandler
  {
    protected override Task<HttpResponseMessage> SendAsync(
      HttpRequestMessage request,
      CancellationToken cancellationToken) =>
      Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
      {
        Content = new ByteArrayContent("[]"u8.ToArray()),
      });
  }

  private sealed class FailedReadStream : Stream
  {
    public override bool CanRead => true;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => throw new NotSupportedException();
    public override long Position
    {
      get => throw new NotSupportedException();
      set => throw new NotSupportedException();
    }

    public override void Flush() { }
    public override int Read(byte[] buffer, int offset, int count) => throw new IOException("metadata stream failed");
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default) =>
      ValueTask.FromException<int>(new IOException("metadata stream failed"));
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
  }

  private sealed class StubFeed(IUpdateFeedRelease release) : IUpdateFeed
  {
    public Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
      string channel,
      CancellationToken cancellationToken = default) =>
      Task.FromResult<IUpdateFeedRelease?>(release);
  }

  private sealed class StubRelease : IUpdateFeedRelease
  {
    public string Source => "Local folder";
    public ReadOnlyMemory<byte> ManifestContent => "{}"u8.ToArray();

    public Task<Stream> OpenPackageAsync(
      string packageFileName,
      CancellationToken cancellationToken = default) =>
      Task.FromResult<Stream>(new MemoryStream());
  }
}
