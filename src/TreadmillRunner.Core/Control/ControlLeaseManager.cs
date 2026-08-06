namespace TreadmillRunner.Core.Control;

public sealed class ControlLeaseManager
{
  public static readonly TimeSpan LeaseTimeToLive = TimeSpan.FromSeconds(15);

  private readonly object _gate = new();
  private readonly TimeProvider _timeProvider;
  private readonly Func<Guid> _leaseIdFactory;
  private ControlLease? _current;
  private long _lastRenewedTimestamp;

  public ControlLeaseManager(TimeProvider timeProvider, Func<Guid>? leaseIdFactory = null)
  {
    _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
    _leaseIdFactory = leaseIdFactory ?? Guid.NewGuid;
  }

  public ControlLease? Current
  {
    get
    {
      lock (_gate)
      {
        RemoveExpiredLease();
        return _current;
      }
    }
  }

  public ControlLease? TryAcquire(string holderId)
  {
    ValidateHolder(holderId);

    lock (_gate)
    {
      RemoveExpiredLease();
      if (_current is not null)
      {
        if (string.Equals(_current.HolderId, holderId, StringComparison.Ordinal))
        {
          _current = _current with
          {
            ExpiresAt = _timeProvider.GetUtcNow() + LeaseTimeToLive,
          };
          _lastRenewedTimestamp = _timeProvider.GetTimestamp();
          return _current;
        }

        return null;
      }

      var now = _timeProvider.GetUtcNow();
      _lastRenewedTimestamp = _timeProvider.GetTimestamp();
      _current = new ControlLease(_leaseIdFactory(), holderId, now, now + LeaseTimeToLive);
      return _current;
    }
  }

  public ControlLease? Heartbeat(Guid leaseId, string holderId)
  {
    ValidateHolder(holderId);

    lock (_gate)
    {
      RemoveExpiredLease();
      if (_current is null || _current.Id != leaseId ||
          !string.Equals(_current.HolderId, holderId, StringComparison.Ordinal))
      {
        return null;
      }

      _current = _current with
      {
        ExpiresAt = _timeProvider.GetUtcNow() + LeaseTimeToLive,
      };
      _lastRenewedTimestamp = _timeProvider.GetTimestamp();
      return _current;
    }
  }

  public bool IsValid(Guid leaseId, string holderId)
  {
    ValidateHolder(holderId);

    lock (_gate)
    {
      RemoveExpiredLease();
      return _current is not null &&
          _current.Id == leaseId &&
          string.Equals(_current.HolderId, holderId, StringComparison.Ordinal);
    }
  }

  public bool Release(Guid leaseId, string holderId)
  {
    ValidateHolder(holderId);

    lock (_gate)
    {
      RemoveExpiredLease();
      if (_current is null || _current.Id != leaseId ||
          !string.Equals(_current.HolderId, holderId, StringComparison.Ordinal))
      {
        return false;
      }

      _current = null;
      return true;
    }
  }

  public void RevokeCurrent()
  {
    lock (_gate)
    {
      _current = null;
    }
  }

  private void RemoveExpiredLease()
  {
    if (_current is not null &&
        _timeProvider.GetElapsedTime(_lastRenewedTimestamp) >= LeaseTimeToLive)
    {
      _current = null;
    }
  }

  private static void ValidateHolder(string holderId)
  {
    if (string.IsNullOrWhiteSpace(holderId))
    {
      throw new ArgumentException("A control lease holder is required.", nameof(holderId));
    }
  }
}
