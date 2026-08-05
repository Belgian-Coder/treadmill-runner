using TreadmillRunner.Core.Updates;

namespace TreadmillRunner.Infrastructure.Updates;

public sealed class FallbackUpdateFeed(params IUpdateFeed[] feeds) : IUpdateFeed
{
  private readonly IReadOnlyList<IUpdateFeed> _feeds = feeds.Length > 0
    ? feeds
    : throw new ArgumentException("At least one update feed is required.", nameof(feeds));

  public async Task<IUpdateFeedRelease?> ReadLatestReleaseAsync(
    string channel,
    CancellationToken cancellationToken = default)
  {
    UpdateFeedUnavailableException? lastUnavailable = null;
    foreach (IUpdateFeed feed in _feeds)
    {
      try
      {
        IUpdateFeedRelease? release = await feed.ReadLatestReleaseAsync(channel, cancellationToken);
        if (release is not null) return release;
      }
      catch (UpdateFeedUnavailableException exception)
      {
        lastUnavailable = exception;
      }
    }

    if (lastUnavailable is not null) throw lastUnavailable;
    return null;
  }
}
