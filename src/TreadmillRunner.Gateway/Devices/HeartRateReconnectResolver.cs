using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Gateway.Devices;

internal enum HeartRateReconnectMatch
{
  ExactDeviceId,
  ExactDisplayName,
  UniqueFamilyAndKind,
}

internal sealed record HeartRateReconnectResolution(
  string DeviceId,
  HeartRateReconnectMatch Match);

/// <summary>
/// Aggregates split BLE advertisement packets and resolves only a unique,
/// recognizable replacement locator for an enrolled heart-rate source.
/// </summary>
internal sealed class HeartRateReconnectResolver
{
  private readonly Dictionary<string, Candidate> _candidates =
    new(StringComparer.OrdinalIgnoreCase);

  public void Observe(BleAdvertisement advertisement)
  {
    if (string.IsNullOrWhiteSpace(advertisement.DeviceId)) return;

    if (!_candidates.TryGetValue(advertisement.DeviceId, out Candidate? candidate))
    {
      candidate = new Candidate(advertisement.DeviceId);
      _candidates.Add(advertisement.DeviceId, candidate);
    }

    candidate.Observe(advertisement);
  }

  public bool ContainsDeviceId(string deviceId) =>
    _candidates.ContainsKey(deviceId);

  public HeartRateReconnectResolution? Resolve(
    DeviceEnrollment enrollment,
    string currentDeviceId,
    bool allowDisplayNameFallback,
    bool allowFamilyFallback,
    string? excludedDeviceId = null)
  {
    ArgumentNullException.ThrowIfNull(enrollment);
    ArgumentException.ThrowIfNullOrWhiteSpace(currentDeviceId);

    Candidate[] eligible = _candidates.Values
      .Where(candidate => !string.Equals(
        candidate.DeviceId,
        excludedDeviceId,
        StringComparison.OrdinalIgnoreCase))
      .ToArray();

    Candidate? exactDevice = eligible.SingleOrDefault(candidate => string.Equals(
      candidate.DeviceId,
      currentDeviceId,
      StringComparison.OrdinalIgnoreCase));
    if (exactDevice is not null)
    {
      return new HeartRateReconnectResolution(
        exactDevice.DeviceId,
        HeartRateReconnectMatch.ExactDeviceId);
    }

    if (allowDisplayNameFallback)
    {
      Candidate[] exactName = eligible
        .Where(candidate =>
          candidate.HasAdvertisedHeartRateService &&
          string.Equals(
            candidate.Name,
            enrollment.DisplayName,
            StringComparison.OrdinalIgnoreCase))
        .ToArray();
      if (exactName.Length == 1)
      {
        return new HeartRateReconnectResolution(
          exactName[0].DeviceId,
          HeartRateReconnectMatch.ExactDisplayName);
      }
    }

    HeartRateDeviceFamily expectedFamily = EffectiveFamily(enrollment);
    if (!allowFamilyFallback || expectedFamily == HeartRateDeviceFamily.Other)
    {
      return null;
    }

    HeartRateDeviceKind expectedKind = EffectiveKind(enrollment);
    Candidate[] familyAndKind = eligible
      .Where(candidate =>
        candidate.HasAdvertisedHeartRateService &&
        HeartRateDeviceClassifier.Family(candidate.Name, candidate.ServiceUuids) == expectedFamily &&
        HeartRateDeviceClassifier.Classify(candidate.Name, candidate.ServiceUuids) == expectedKind)
      .ToArray();
    return familyAndKind.Length == 1
      ? new HeartRateReconnectResolution(
        familyAndKind[0].DeviceId,
        HeartRateReconnectMatch.UniqueFamilyAndKind)
      : null;
  }

  internal static HeartRateDeviceFamily EffectiveFamily(DeviceEnrollment enrollment)
  {
    HeartRateDeviceFamily stored = enrollment.HeartRateDeviceFamily ?? HeartRateDeviceFamily.Other;
    return stored != HeartRateDeviceFamily.Other
      ? stored
      : HeartRateDeviceClassifier.Family(enrollment.DisplayName);
  }

  internal static HeartRateDeviceKind EffectiveKind(DeviceEnrollment enrollment)
  {
    HeartRateDeviceKind stored = enrollment.HeartRateDeviceKind ?? HeartRateDeviceKind.Sensor;
    return stored != HeartRateDeviceKind.Sensor
      ? stored
      : HeartRateDeviceClassifier.Classify(enrollment.DisplayName);
  }

  private sealed class Candidate(string deviceId)
  {
    private readonly HashSet<Guid> _serviceUuids = [];

    public string DeviceId { get; } = deviceId;

    public string? Name { get; private set; }

    public IReadOnlyCollection<Guid> ServiceUuids => _serviceUuids;

    public bool HasAdvertisedHeartRateService =>
      _serviceUuids.Contains(HeartRateDeviceClassifier.HeartRateService) ||
      _serviceUuids.Contains(HeartRateDeviceClassifier.PolarService);

    public void Observe(BleAdvertisement advertisement)
    {
      if (!string.IsNullOrWhiteSpace(advertisement.Name))
      {
        Name = advertisement.Name.Trim();
      }

      _serviceUuids.UnionWith(advertisement.ServiceUuids);
    }
  }
}
