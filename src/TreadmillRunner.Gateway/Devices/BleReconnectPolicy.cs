namespace TreadmillRunner.Gateway.Devices;

public sealed class BleReconnectPolicy
{
  public static readonly TimeSpan StableConnectionThreshold = TimeSpan.FromSeconds(30);

  public TimeSpan GetDelay(Guid enrollmentId, int consecutiveFailureCount)
  {
    if (enrollmentId == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(enrollmentId));
    if (consecutiveFailureCount < 1) throw new ArgumentOutOfRangeException(nameof(consecutiveFailureCount));

    int exponent = Math.Min(consecutiveFailureCount - 1, 4);
    double baseSeconds = Math.Min(10, Math.Pow(2, exponent));
    Span<byte> bytes = stackalloc byte[16];
    enrollmentId.TryWriteBytes(bytes);
    int bucket = (bytes[0] << 8 | bytes[1]) % 501;
    return TimeSpan.FromSeconds(baseSeconds) + TimeSpan.FromMilliseconds(bucket);
  }
}
