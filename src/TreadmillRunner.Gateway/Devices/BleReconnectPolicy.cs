namespace TreadmillRunner.Gateway.Devices;

public sealed class BleReconnectPolicy
{
  public static readonly TimeSpan StableConnectionThreshold = TimeSpan.FromSeconds(30);

  public TimeSpan GetDelay(Guid enrollmentId, int consecutiveFailureCount, bool active = true)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    if (consecutiveFailureCount < 1) throw new ArgumentOutOfRangeException(nameof(consecutiveFailureCount));

    int exponent = active
      ? Math.Min(consecutiveFailureCount - 1, 4)
      : Math.Min(consecutiveFailureCount - 1, 9);
    double baseSeconds = active
      ? Math.Min(10, Math.Pow(2, exponent))
      : Math.Min(300, Math.Pow(2, exponent));
    Span<byte> bytes = stackalloc byte[16];
    enrollmentId.TryWriteBytes(bytes);
    int jitterLimit = active
      ? 500
      : Math.Min(500, Math.Max(0, (int)(300 - baseSeconds) * 1000));
    int bucket = jitterLimit == 0
      ? 0
      : (bytes[0] << 8 | bytes[1]) % (jitterLimit + 1);
    return TimeSpan.FromSeconds(baseSeconds) + TimeSpan.FromMilliseconds(bucket);
  }
}
