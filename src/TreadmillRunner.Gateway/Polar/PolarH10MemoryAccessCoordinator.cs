using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

public interface IPolarH10MemoryAccessCoordinator
{
  Task<IPolarH10MemoryAccessLease> AcquireAsync(
    Guid? enrollmentId,
    CancellationToken cancellationToken = default);
}

public interface IPolarH10MemoryAccessLease : IAsyncDisposable
{
  Guid EnrollmentId { get; }
}

/// <summary>
/// Temporarily releases the live heart-rate connection before PFTP uses the exact same H10.
/// The onboard recording continues independently while the live connection is suspended.
/// </summary>
public sealed class PolarH10MemoryAccessCoordinator(
  IDeviceEnrollmentStore enrollments,
  IReadOnlyDeviceCoordinator deviceCoordinator) : IPolarH10MemoryAccessCoordinator
{
  public async Task<IPolarH10MemoryAccessLease> AcquireAsync(
    Guid? enrollmentId,
    CancellationToken cancellationToken = default)
  {
    DeviceEnrollment[] candidates = (await enrollments.ListActiveAsync(cancellationToken).ConfigureAwait(false))
      .Select(item => item.Enrollment)
      .Where(item => item.Role == DeviceRole.HeartRate)
      .Where(item => item.HeartRateDeviceFamily == HeartRateDeviceFamily.Polar && IsH10(item))
      .Where(item => enrollmentId is null || item.Id == enrollmentId)
      .ToArray();

    DeviceEnrollment enrollment = candidates.Length switch
    {
      1 => candidates[0],
      0 => throw new InvalidOperationException("The exact enrolled Polar H10 is not available."),
      _ => throw new InvalidOperationException("More than one Polar H10 is enrolled; choose the exact device before using memory operations."),
    };

    bool suspended = await deviceCoordinator
      .SuspendConnectionAsync(enrollment.Id, cancellationToken)
      .ConfigureAwait(false);
    if (!suspended)
      throw new InvalidOperationException("The exact H10 live connection could not be released for a memory operation.");

    return new PolarH10MemoryAccessLease(enrollment.Id, deviceCoordinator);
  }

  private static bool IsH10(DeviceEnrollment enrollment) =>
    enrollment.DisplayName.Contains("polar h10", StringComparison.OrdinalIgnoreCase) ||
    string.Equals(enrollment.ModelNumber?.Trim(), "H10", StringComparison.OrdinalIgnoreCase);
}

public sealed class PolarH10MemoryAccessLease(
  Guid enrollmentId,
  IReadOnlyDeviceCoordinator deviceCoordinator) : IPolarH10MemoryAccessLease
{
  private int _disposed;

  public Guid EnrollmentId { get; } = enrollmentId;

  public async ValueTask DisposeAsync()
  {
    if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
    await deviceCoordinator.ResumeConnectionAsync(EnrollmentId, CancellationToken.None).ConfigureAwait(false);
  }
}
