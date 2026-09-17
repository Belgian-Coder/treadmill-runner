namespace TreadmillRunner.Core.Sessions;

/// <summary>Polar-only memory boundary. Every mutating operation names an exact enrollment and exercise.</summary>
public interface IPolarH10MemoryClient
{
  /// <summary>
  /// Resolves the exact current H10 locator and opens one operation-scoped PFTP session.
  /// Read-only reconnects refresh the locator without replaying mutations.
  /// The caller must dispose the session before releasing exclusive memory access.
  /// </summary>
  Task<IPolarH10MemorySession> OpenAsync(Guid? enrollmentId, CancellationToken cancellationToken = default);
}

public interface IPolarH10MemorySession : IAsyncDisposable
{
  Guid EnrollmentId { get; }
  string DeviceId { get; }
  string DisplayName { get; }

  Task<PolarH10DeviceRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default);
  /// <summary>
  /// Checks the current exact recording, starts only when idle, and confirms the requested recording
  /// without releasing the underlying PFTP connection between those steps.
  /// </summary>
  Task<PolarH10StartResult> StartAsync(string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default);
  Task StopAsync(CancellationToken cancellationToken = default);
  Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(CancellationToken cancellationToken = default);
  Task<PolarH10MemoryRecord> FetchAsync(string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default);
  Task DeleteAsync(string remotePath, CancellationToken cancellationToken = default);
}

public enum PolarH10SampleType { HeartRate, RrInterval }

public sealed record PolarH10DeviceRecordingStatus(
  Guid EnrollmentId,
  string DeviceId,
  string DisplayName,
  bool IsRecording,
  string? ExerciseId);

public sealed record PolarH10StartResult(
  PolarH10DeviceRecordingStatus Status,
  bool StartIssued,
  DateTimeOffset? StartIssuedAtUtc = null);

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
