namespace TreadmillRunner.Web.Live;

public sealed class BoundedLiveSeries<T>
{
  private readonly T[] _items;
  private int _start;
  private T[]? _snapshot;

  public BoundedLiveSeries(int capacity)
  {
    if (capacity < 1)
    {
      throw new ArgumentOutOfRangeException(nameof(capacity));
    }

    _items = new T[capacity];
  }

  public int Capacity => _items.Length;

  public int Count { get; private set; }

  public long Version { get; private set; }

  public void AppendOrReplace(T item, Func<T, T, bool> samePosition)
  {
    ArgumentNullException.ThrowIfNull(samePosition);
    if (Count > 0)
    {
      int lastIndex = (_start + Count - 1) % Capacity;
      if (samePosition(_items[lastIndex], item))
      {
        if (EqualityComparer<T>.Default.Equals(_items[lastIndex], item))
        {
          return;
        }
        _items[lastIndex] = item;
        InvalidateSnapshot();
        return;
      }
    }

    if (Count < Capacity)
    {
      _items[(_start + Count) % Capacity] = item;
      Count++;
      InvalidateSnapshot();
      return;
    }

    _items[_start] = item;
    _start = (_start + 1) % Capacity;
    InvalidateSnapshot();
  }

  public IReadOnlyList<T> Snapshot()
  {
    if (_snapshot is not null)
    {
      return _snapshot;
    }

    var snapshot = new T[Count];
    for (var index = 0; index < Count; index++)
    {
      snapshot[index] = _items[(_start + index) % Capacity];
    }

    _snapshot = snapshot;
    return _snapshot;
  }

  private void InvalidateSnapshot()
  {
    Version++;
    _snapshot = null;
  }
}
