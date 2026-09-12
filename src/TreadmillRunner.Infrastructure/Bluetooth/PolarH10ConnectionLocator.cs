using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Infrastructure.Bluetooth;

/// <summary>
/// Resolves a fresh, fail-closed BLE locator for an enrolled H10 before PFTP access.
/// Polar sensors can advertise with a rotating private address, so the persisted
/// enrollment locator is not assumed to remain a current Windows connection key.
/// </summary>
public sealed class PolarH10ConnectionLocator
{
  private static readonly TimeSpan DefaultScanDuration = TimeSpan.FromSeconds(5);
  private readonly IBleAdvertisementBroker _advertisements;
  private readonly TimeSpan _scanDuration;

  public PolarH10ConnectionLocator(IBleAdvertisementBroker advertisements)
    : this(advertisements, DefaultScanDuration)
  {
  }

  internal PolarH10ConnectionLocator(
    IBleAdvertisementBroker advertisements,
    TimeSpan scanDuration)
  {
    _advertisements = advertisements ?? throw new ArgumentNullException(nameof(advertisements));
    if (scanDuration <= TimeSpan.Zero || scanDuration > TimeSpan.FromSeconds(30))
      throw new ArgumentOutOfRangeException(nameof(scanDuration));
    _scanDuration = scanDuration;
  }

  public async Task<string> ResolveAsync(
    DeviceEnrollment enrollment,
    IReadOnlyCollection<DeviceEnrollment> activeHeartRateEnrollments,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(enrollment);
    ArgumentNullException.ThrowIfNull(activeHeartRateEnrollments);
    cancellationToken.ThrowIfCancellationRequested();

    var resolver = new HeartRateReconnectResolver();
    using var scan = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    scan.CancelAfter(_scanDuration);
    try
    {
      await foreach (BleAdvertisement advertisement in _advertisements
        .ScanAsync(scan.Token)
        .WithCancellation(scan.Token)
        .ConfigureAwait(false))
      {
        resolver.Observe(advertisement);
        if (resolver.ContainsDeviceId(enrollment.DeviceId)) return enrollment.DeviceId;
      }
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      throw;
    }
    catch (OperationCanceledException) when (scan.IsCancellationRequested)
    {
      // Resolve only the complete bounded candidate set collected before timeout.
    }

    DeviceEnrollment[] peers = activeHeartRateEnrollments
      .Where(candidate => candidate.Role == DeviceRole.HeartRate && candidate.Id != enrollment.Id)
      .ToArray();
    bool nameIsUnique = peers.All(candidate => !string.Equals(
      candidate.DisplayName,
      enrollment.DisplayName,
      StringComparison.OrdinalIgnoreCase));
    HeartRateDeviceFamily family = HeartRateReconnectResolver.EffectiveFamily(enrollment);
    HeartRateDeviceKind kind = HeartRateReconnectResolver.EffectiveKind(enrollment);
    bool familyAndKindAreUnique = family != HeartRateDeviceFamily.Other &&
      peers.All(candidate =>
        HeartRateReconnectResolver.EffectiveFamily(candidate) != family ||
        HeartRateReconnectResolver.EffectiveKind(candidate) != kind);

    HeartRateReconnectResolution? resolution = resolver.Resolve(
      enrollment,
      enrollment.DeviceId,
      nameIsUnique,
      familyAndKindAreUnique);
    return resolution?.DeviceId ?? enrollment.DeviceId;
  }
}
