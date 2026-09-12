using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Polar;

namespace TreadmillRunner.Infrastructure.Bluetooth;

public sealed class PolarH10MemoryClient(
  IDeviceEnrollmentStore enrollments,
  IPolarPftpConnectionFactory connections,
  PolarH10ConnectionLocator connectionLocator) : IPolarH10MemoryClient
{
  private const int MaximumStatusAttempts = 4;

  public async Task<PolarH10DeviceRecordingStatus> GetStatusAsync(Guid? enrollmentId, CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    for (var attempt = 1; ; attempt++)
    {
      try
      {
        await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
        PolarRecordingStatus status = await new PolarPftpClient(connection).GetStatusAsync(cancellationToken).ConfigureAwait(false);
        return new(target.Enrollment.Id, target.Enrollment.DeviceId, target.Enrollment.DisplayName, status.IsRecording, status.EntryId);
      }
      catch (Exception exception) when (
        attempt < MaximumStatusAttempts &&
        !cancellationToken.IsCancellationRequested &&
        (exception is TimeoutException or WindowsBleException ||
          exception is IOException and not PolarPftpProtocolException))
      {
        await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken).ConfigureAwait(false);
      }
    }
  }

  public async Task StartAsync(Guid enrollmentId, string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).StartAsync(
      exerciseId,
      sampleType == PolarH10SampleType.RrInterval ? PolarRecordingSampleType.RrInterval : PolarRecordingSampleType.HeartRate,
      intervalSeconds,
      cancellationToken).ConfigureAwait(false);
  }

  public async Task StopAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).StopAsync(cancellationToken).ConfigureAwait(false);
  }

  public async Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(Guid enrollmentId, CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
    IReadOnlyList<PolarExerciseSummary> recordings = await new PolarPftpClient(connection).ListExercisesAsync(cancellationToken).ConfigureAwait(false);
    return recordings.Select(recording => new PolarH10RemoteRecording(recording.Identifier, recording.SizeBytes)).ToArray();
  }

  public async Task<PolarH10MemoryRecord> FetchAsync(Guid enrollmentId, string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default)
  {
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
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
    PolarH10Target target = await ResolveAsync(enrollmentId, cancellationToken).ConfigureAwait(false);
    await using IPolarPftpConnection connection = await ConnectAsync(target, cancellationToken).ConfigureAwait(false);
    await new PolarPftpClient(connection).RemoveExerciseAsync(remotePath, cancellationToken).ConfigureAwait(false);
  }

  private async ValueTask<IPolarPftpConnection> ConnectAsync(
    PolarH10Target target,
    CancellationToken cancellationToken)
  {
    string deviceId = await connectionLocator.ResolveAsync(
      target.Enrollment,
      target.ActiveHeartRateEnrollments,
      cancellationToken).ConfigureAwait(false);
    return await connections.ConnectAsync(
      deviceId,
      cancellationToken: cancellationToken).ConfigureAwait(false);
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
}
