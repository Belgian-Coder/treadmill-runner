using TreadmillRunner.Core.Updates;
using TreadmillRunner.Infrastructure.Updates;

namespace TreadmillRunner.Gateway.Updates;

public sealed class UpdateFeedFactory(IConfiguration configuration, IHttpClientFactory? httpClientFactory = null)
{
  public IUpdateFeed Create()
  {
    string provider = configuration["Updates:FeedProvider"] ?? "Local";
    return provider switch
    {
      "Local" => CreateLocal(),
      "GitHub" => CreateGitHub(),
      "GitHubThenLocal" => new FallbackUpdateFeed(CreateGitHub(), CreateLocal()),
      _ => throw new InvalidOperationException("Updates:FeedProvider must be Local, GitHub, or GitHubThenLocal."),
    };
  }

  private IUpdateFeed CreateLocal()
  {
    string path = configuration["Updates:FeedPath"]
      ?? throw new InvalidOperationException("Updates:FeedPath is required for the local update feed.");
    return new LocalFolderUpdateFeed(path);
  }

  private IUpdateFeed CreateGitHub()
  {
    if (httpClientFactory is null)
      throw new InvalidOperationException("The GitHub update feed HTTP client is unavailable.");
    string owner = configuration["Updates:GitHubOwner"]
      ?? throw new InvalidOperationException("Updates:GitHubOwner is required.");
    string repository = configuration["Updates:GitHubRepository"]
      ?? throw new InvalidOperationException("Updates:GitHubRepository is required.");
    return new GitHubReleaseUpdateFeed(
      httpClientFactory.CreateClient("TreadmillRunnerUpdates"),
      owner,
      repository);
  }
}
