namespace TreadmillRunner.Core.Live;

public sealed class FixedIntervalCadence
{
  private readonly TimeSpan _interval;
  private DateTimeOffset _lastEmission;

  public FixedIntervalCadence(TimeSpan interval, DateTimeOffset startedAt)
  {
    if (interval <= TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(interval));
    }

    _interval = interval;
    _lastEmission = startedAt;
  }

  public bool TryAdvance(DateTimeOffset now)
  {
    if (now < _lastEmission)
    {
      throw new ArgumentOutOfRangeException(nameof(now), "Cadence time cannot move backwards.");
    }

    if (now - _lastEmission < _interval)
    {
      return false;
    }

    _lastEmission = now;
    return true;
  }
}
