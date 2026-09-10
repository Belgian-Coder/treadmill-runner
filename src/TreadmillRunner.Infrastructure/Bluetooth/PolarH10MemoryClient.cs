using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class PolarH10MemoryClient(
  IDeviceEnrollmentStore enrollments,
  IPolarPftpConnectionFactory connections) : IPolarH10MemoryClient
{
  public async Task<PolarH10DeviceRecordingStatus> GetStatusAsync(Guid? enrollmentId, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    PolarRecordingStatus status = await new PolarPftpClient(connection).GetStatusAsync(cancellationToken).ConfigureAwait(false);
    return new(enrollment.Id, enrollment.DeviceId, enrollment.DisplayName, status.IsRecording, status.EntryId);
  }

  public async Task StartAsync(Guid enrollmentId, string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).StartAsync(
      exerciseId,
      sampleType == PolarH10SampleType.RrInterval ? PolarRecordingSampleType.RrInterval : PolarRecordingSampleType.HeartRate,
      intervalSeconds,
      cancellationToken).ConfigureAwait(false);
  }

  public async Task StopAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).StopAsync(cancellationToken).ConfigureAwait(false);
  }

  public async Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    IReadOnlyList<PolarExerciseSummary> recordings = await new PolarPftpClient(connection).ListExercisesAsync(cancellationToken).ConfigureAwait(false);
    return recordings.Select(recording => new PolarH10RemoteRecording(recording.Identifier, recording.SizeBytes)).ToArray();
  }

  public async Task<PolarH10MemoryRecord> FetchAsync(Guid enrollmentId, string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    PolarExerciseSamples recording = await new PolarPftpClient(connection).FetchExerciseAsync(remotePath, cancellationToken).ConfigureAwait(false);
    PolarH10SampleType sampleType = recording.SampleType == PolarRecordingSampleType.RrInterval ? PolarH10SampleType.RrInterval : PolarH10SampleType.HeartRate;
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

  public async Task DeleteAsync(Guid enrollmentId, string remotePath, CancellationToken cancellationToken = default)
  {
    DeviceEnrollment enrollment = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await connections.ConnectAsync(enrollment.DeviceId, cancellationToken: cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).RemoveExerciseAsync(remotePath, cancellationToken).ConfigureAwait(false);
  }

  private async Task<DeviceEnrollment> ResolveAsync(Guid? enrollmentId, CancellationToken cancellationToken)
  {
    IReadOnlyList<DeviceEnrollment> candidates = (await enrollments.ListActiveAsync(cancellationToken).ConfigureAwait(false))
      .Select(item => item.Enrollment)
      .Where(item => item.Role == DeviceRole.HeartRate && item.HeartRateDeviceFamily == HeartRateDeviceFamily.Polar && IsH10(item))
      .Where(item => enrollmentId is null || item.Id == enrollmentId)
      .ToArray();
    return candidates.Count switch
    {
      1 => candidates[0],
      0 => throw new InvalidOperationException("The exact enrolled Polar H10 is not available."),
      _ => throw new InvalidOperationException("More than one Polar H10 is enrolled; choose the exact device before using memory operations."),
    };
  }

  private static bool IsH10(DeviceEnrollment enrollment) =>
    enrollment.DisplayName.Contains("polar h10", StringComparison.OrdinalIgnoreCase) ||
    string.Equals(enrollment.ModelNumber?.Trim(), "H10", StringComparison.OrdinalIgnoreCase);
}
