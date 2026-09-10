namespace TreadmillRunner.Protocols.Polar;

public interface IPolarPftpConnection : IAsyncDisposable
{
  string DeviceId { get; }

  ValueTask<ReadOnlyMemory<byte>> ExchangeAsync(
    ReadOnlyMemory<byte> request,
    TimeSpan responseTimeout,
    int maximumResponseBytes,
    CancellationToken cancellationToken = default);
}

public sealed record PolarRecordingStatus(bool IsRecording, string? EntryId = null);

public enum PolarRecordingSampleType : ulong
{
  HeartRate = 1,
  RrInterval = 16,
}

public sealed record PolarExerciseSummary(string Identifier, long SizeBytes);

public sealed record PolarExerciseSamples(
  string Identifier,
  ReadOnlyMemory<byte> Payload,
  PolarRecordingSampleType SampleType,
  int IntervalSeconds,
  IReadOnlyList<ushort> HeartRateSamples,
  IReadOnlyList<uint> RrIntervalsMilliseconds);

public static class PolarPftpQueries
{
  public const ushort Start = 14;
  public const ushort Stop = 15;
  public const ushort GetStatus = 16;
}
