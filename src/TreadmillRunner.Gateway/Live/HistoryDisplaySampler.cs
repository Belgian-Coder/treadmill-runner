using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Gateway.Live;

internal static class HistoryDisplaySampler
{
  internal const int MaximumSamples = SessionDisplayLimits.MaximumSamples;

  internal static IReadOnlyList<SessionSample> Select(IReadOnlyList<SessionSample> storedSamples)
  {
    ArgumentNullException.ThrowIfNull(storedSamples);
    if (storedSamples.Count <= MaximumSamples)
    {
      return storedSamples;
    }

    var selected = new SessionSample[MaximumSamples];
    long lastIndex = storedSamples.Count - 1L;
    long lastSlot = MaximumSamples - 1L;
    for (var slot = 0; slot < MaximumSamples; slot++)
    {
      int sourceIndex = checked((int)((slot * lastIndex) / lastSlot));
      selected[slot] = storedSamples[sourceIndex];
    }

    return selected;
  }
}
