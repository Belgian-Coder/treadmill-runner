namespace TreadmillRunner.Core.Sessions;

/// <summary>Polar-only memory boundary. Every mutating operation names an exact enrollment and exercise.</summary>
public interface IPolarH10MemoryClient
{
  Task<PolarH10DeviceRecordingStatus> GetStatusAsync(Guid? enrollmentId, CancellationToken cancellationToken = default);
  Task StartAsync(Guid enrollmentId, string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default);
  Task StopAsync(Guid enrollmentId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(Guid enrollmentId, CancellationToken cancellationToken = default);
  Task<PolarH10MemoryRecord> FetchAsync(Guid enrollmentId, string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default);
  Task DeleteAsync(Guid enrollmentId, string remotePath, CancellationToken cancellationToken = default);
}

public enum PolarH10SampleType { HeartRate, RrInterval }

public sealed record PolarH10DeviceRecordingStatus(
  Guid EnrollmentId,
  string DeviceId,
  string DisplayName,
  bool IsRecording,
  string? ExerciseId);

public sealed record PolarH10RemoteRecording(string RemotePath, long SizeBytes);

public sealed record PolarH10MemoryRecord(
  string RecordingId,
  string RemotePath,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset EndedAtUtc,
  PolarH10SampleType SampleType,
  int IntervalSeconds,
  ReadOnlyMemory<byte> Payload,
  IReadOnlyList<PolarH10HeartRateSample> Samples,
  IReadOnlyList<uint> RrIntervalsMilliseconds);

public sealed record PolarH10HeartRateSample(DateTimeOffset CapturedAtUtc, ushort? BeatsPerMinute);

public enum PolarH10RecordingOutcome
{
  StartPending, Recording, StopPending, AwaitingDevice, Downloading, Downloaded,
  Merging, Merged, RemovalPending, Completed, Retained, Skipped, NotStarted,
  DiscardCleanupPending, ReviewRequired, Retryable,
}
