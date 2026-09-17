using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class PolarH10MemoryClient(
  IDeviceEnrollmentStore enrollments,
  IPolarPftpConnectionFactory connections,
  PolarH10ConnectionLocator connectionLocator,
  TimeProvider timeProvider) : IPolarH10MemoryClient
{
  public async Task<IPolarH10MemorySession> OpenAsync(
    Guid? enrollmentId,
    CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    string deviceId = await connectionLocator.ResolveAsync(
      target.Enrollment,
      target.ActiveHeartRateEnrollments,
      cancellationToken).ConfigureAwait(false);
    return new Session(
      target.Enrollment,
      target.ActiveHeartRateEnrollments,
      deviceId,
      connections,
      connectionLocator,
      timeProvider);
  }

  private async Task<PolarH10Target> ResolveAsync(Guid? enrollmentId, CancellationToken cancellationToken)
  {
    DeviceEnrollment[] activeHeartRateEnrollments = (await enrollments
      .ListActiveAsync(cancellationToken)
      .ConfigureAwait(false))
      .Select(item => item.Enrollment)
      .Where(item => item.Role == DeviceRole.HeartRate)
      .ToArray();
    DeviceEnrollment[] candidates = activeHeartRateEnrollments
      .Where(item => item.HeartRateDeviceFamily == HeartRateDeviceFamily.Polar && IsH10(item))
      .Where(item => enrollmentId is null || item.Id == enrollmentId)
      .ToArray();
    return candidates.Length switch
    {
      1 => new PolarH10Target(candidates[0], activeHeartRateEnrollments),
      0 => throw new InvalidOperationException("The exact enrolled Polar H10 is not available."),
      _ => throw new InvalidOperationException("More than one Polar H10 is enrolled; choose the exact device before using memory operations."),
    };
  }

  private static bool IsH10(DeviceEnrollment enrollment) =>
    enrollment.DisplayName.Contains("polar h10", StringComparison.OrdinalIgnoreCase) ||
    string.Equals(enrollment.ModelNumber?.Trim(), "H10", StringComparison.OrdinalIgnoreCase);

  private sealed record PolarH10Target(
    DeviceEnrollment Enrollment,
    IReadOnlyCollection<DeviceEnrollment> ActiveHeartRateEnrollments);

  private sealed class Session(
    DeviceEnrollment enrollment,
    IReadOnlyCollection<DeviceEnrollment> activeHeartRateEnrollments,
    string currentDeviceId,
    IPolarPftpConnectionFactory connections,
    PolarH10ConnectionLocator connectionLocator,
    TimeProvider timeProvider) : IPolarH10MemorySession
  {
    private const int MaximumReadAttempts = 2;
    private static readonly TimeSpan ReadRetryDelay = TimeSpan.FromMilliseconds(500);
    private IPolarPftpConnection? _connection;
    private string _currentDeviceId = currentDeviceId;
    private bool _refreshLocator;
    private int _disposed;

    public Guid EnrollmentId { get; } = enrollment.Id;
    public string DeviceId { get; } = enrollment.DeviceId;
    public string DisplayName { get; } = enrollment.DisplayName;

    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default) =>
      ExecuteReadAsync(async (client, token) => MapStatus(await client.GetStatusAsync(token).ConfigureAwait(false)), cancellationToken);

    public async Task<PolarH10StartResult> StartAsync(
      string exerciseId,
      PolarH10SampleType sampleType,
      int intervalSeconds,
      CancellationToken cancellationToken = default)
    {
      PolarH10DeviceRecordingStatus current = await GetStatusAsync(cancellationToken).ConfigureAwait(false);
      if (current.IsRecording) return new(current, false);

      PolarPftpClient client = await GetClientAsync(cancellationToken).ConfigureAwait(false);
      // Mark the mutation as dispatched before awaiting it. Any failure from this point has an
      // uncertain physical outcome and must be reconciled by a later status read, never replayed here.
      DateTimeOffset startIssuedAtUtc = timeProvider.GetUtcNow();
      await client.StartAsync(
        exerciseId,
        sampleType == PolarH10SampleType.RrInterval ? PolarRecordingSampleType.RrInterval : PolarRecordingSampleType.HeartRate,
        intervalSeconds,
        cancellationToken).ConfigureAwait(false);
      PolarRecordingStatus confirmed = await client.GetStatusAsync(cancellationToken).ConfigureAwait(false);
      return new(MapStatus(confirmed), true, startIssuedAtUtc);
    }

    public async Task StopAsync(CancellationToken cancellationToken = default)
    {
      PolarPftpClient client = await GetClientAsync(cancellationToken).ConfigureAwait(false);
      await client.StopAsync(cancellationToken).ConfigureAwait(false);
    }

    public async Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(CancellationToken cancellationToken = default)
    {
      IReadOnlyList<PolarExerciseSummary> recordings = await ExecuteReadAsync<IReadOnlyList<PolarExerciseSummary>>(
        async (client, token) => await client.ListExercisesAsync(token).ConfigureAwait(false), cancellationToken).ConfigureAwait(false);
      return recordings.Select(recording => new PolarH10RemoteRecording(recording.Identifier, recording.SizeBytes)).ToArray();
    }

    public async Task<PolarH10MemoryRecord> FetchAsync(
      string remotePath,
      DateTimeOffset startedAtUtc,
      CancellationToken cancellationToken = default)
    {
      PolarExerciseSamples recording = await ExecuteReadAsync<PolarExerciseSamples>(
        async (client, token) => await client.FetchExerciseAsync(remotePath, token).ConfigureAwait(false), cancellationToken).ConfigureAwait(false);
      PolarH10SampleType sampleType = recording.SampleType == PolarRecordingSampleType.RrInterval
        ? PolarH10SampleType.RrInterval
        : PolarH10SampleType.HeartRate;
      PolarH10HeartRateSample[] samples = recording.HeartRateSamples
        .Select((heartRate, index) => new PolarH10HeartRateSample(startedAtUtc.AddSeconds((long)index * recording.IntervalSeconds), heartRate))
        .ToArray();
      DateTimeOffset endedAt = sampleType == PolarH10SampleType.RrInterval
        ? startedAtUtc.AddMilliseconds(recording.RrIntervalsMilliseconds.Aggregate<uint, long>(0, (total, value) => checked(total + value)))
        : startedAtUtc.AddSeconds((long)recording.HeartRateSamples.Count * recording.IntervalSeconds);
      string recordingId = remotePath.Split('/', StringSplitOptions.RemoveEmptyEntries).First();
      return new(recordingId, remotePath, startedAtUtc, endedAt, sampleType, recording.IntervalSeconds,
        recording.Payload, samples, recording.RrIntervalsMilliseconds);
    }

    public async Task DeleteAsync(string remotePath, CancellationToken cancellationToken = default)
    {
      PolarPftpClient client = await GetClientAsync(cancellationToken).ConfigureAwait(false);
      await client.RemoveExerciseAsync(remotePath, cancellationToken).ConfigureAwait(false);
    }

    public async ValueTask DisposeAsync()
    {
      if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
      IPolarPftpConnection? connection = Interlocked.Exchange(ref _connection, null);
      if (connection is not null) await connection.DisposeAsync().ConfigureAwait(false);
    }

    private async Task<T> ExecuteReadAsync<T>(
      Func<PolarPftpClient, CancellationToken, Task<T>> operation,
      CancellationToken cancellationToken)
    {
      for (var attempt = 1; ; attempt++)
      {
        try
        {
          PolarPftpClient client = await GetClientAsync(cancellationToken).ConfigureAwait(false);
          return await operation(client, cancellationToken).ConfigureAwait(false);
        }
        catch (Exception exception) when (
          attempt < MaximumReadAttempts &&
          !cancellationToken.IsCancellationRequested &&
          IsTransientReadFailure(exception))
        {
          await ResetConnectionAsync().ConfigureAwait(false);
          await Task.Delay(ReadRetryDelay, cancellationToken).ConfigureAwait(false);
        }
      }
    }

    private async Task<PolarPftpClient> GetClientAsync(CancellationToken cancellationToken)
    {
      ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
      if (_connection is null)
      {
        if (_refreshLocator)
        {
          _currentDeviceId = await connectionLocator.ResolveAsync(
            enrollment,
            activeHeartRateEnrollments,
            cancellationToken).ConfigureAwait(false);
          _refreshLocator = false;
        }
        _connection = await connections.ConnectAsync(
          _currentDeviceId,
          cancellationToken: cancellationToken).ConfigureAwait(false);
      }
      return new PolarPftpClient(_connection);
    }

    private async Task ResetConnectionAsync()
    {
      IPolarPftpConnection? connection = Interlocked.Exchange(ref _connection, null);
      if (connection is not null) await connection.DisposeAsync().ConfigureAwait(false);
      _refreshLocator = true;
    }

    private PolarH10DeviceRecordingStatus MapStatus(PolarRecordingStatus status) =>
      new(EnrollmentId, DeviceId, DisplayName, status.IsRecording, status.EntryId);

    private static bool IsTransientReadFailure(Exception exception) =>
      exception is TimeoutException or WindowsBleException ||
      exception is IOException and not PolarPftpProtocolException;
  }
}
