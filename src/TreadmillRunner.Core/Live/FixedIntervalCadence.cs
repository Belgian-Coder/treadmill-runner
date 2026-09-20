namespace TreadmillRunner.Core.Live;

public sealed class FixedIntervalCadence
{
  private readonly TimeSpan _interval;
  private DateTimeOffset _lastEmission;
  private DateTimeOffset _lastActualEmission;
  private DateTimeOffset _lastObservedNow;

  public FixedIntervalCadence(TimeSpan interval, DateTimeOffset startedAt)
  {
    if (interval <= TimeSpan.Zero)
    {
      throw new ArgumentOutOfRangeException(nameof(interval));
    }

    _interval = interval;
    _lastEmission = startedAt;
    _lastActualEmission = startedAt;
    _lastObservedNow = startedAt;
  }

  public bool TryAdvance(DateTimeOffset now)
  {
    if (now < _lastObservedNow)
    {
      _lastObservedNow = now;
      return false;
    }
    _lastObservedNow = now;

    if (now - _lastActualEmission < _interval || now - _lastEmission < _interval)
    {
      return false;
    }

    long elapsedTicks = (now - _lastEmission).Ticks;
    long elapsedIntervals = (elapsedTicks + (_interval.Ticks / 2)) / _interval.Ticks;
    _lastEmission = _lastEmission.AddTicks(elapsedIntervals * _interval.Ticks);
    _lastActualEmission = now;
    return true;
  }
}
