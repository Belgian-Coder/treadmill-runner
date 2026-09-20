namespace TreadmillRunner.Core.Sessions;

public static class SessionSampleTimeline
{
  public static IReadOnlyList<SessionSample> Normalize(IReadOnlyList<SessionSample> samples)
  {
    ArgumentNullException.ThrowIfNull(samples);
    SessionSample[] ordered = samples
      .OrderBy(static item => item.Sequence)
      .ThenBy(static item => item.CapturedAt)
      .ToArray();
    var normalized = new List<SessionSample>(ordered.Length);
    for (var index = 0; index < ordered.Length; index++)
    {
      SessionSample candidate = ordered[index];
      SessionSample? previous = normalized.LastOrDefault();
      if (!Follows(previous, candidate)) continue;

      // Prefer the following point when the current row is an isolated
      // forward outlier. A purely greedy prefix would accept the outlier and
      // then discard the otherwise valid remainder of a legacy timeline.
      if (index + 1 < ordered.Length)
      {
        SessionSample next = ordered[index + 1];
        bool candidateLeapsPastNext =
          candidate.CapturedAt > next.CapturedAt || candidate.Elapsed > next.Elapsed;
        if (previous is not null && candidateLeapsPastNext && Follows(previous, next)) continue;
      }
      normalized.Add(candidate);
    }
    return normalized;
  }

  private static bool Follows(SessionSample? previous, SessionSample candidate) =>
    previous is null ||
    (candidate.Sequence > previous.Sequence &&
     candidate.CapturedAt >= previous.CapturedAt &&
     candidate.Elapsed >= previous.Elapsed);
}
