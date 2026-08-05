namespace TreadmillRunner.Core.Devices;

public enum DeviceRole
{
  Treadmill,
  HeartRate,
}

public enum TreadmillTelemetryMode
{
  Ftms,
  Vendor,
}

public sealed record DeviceEnrollment
{
  public DeviceEnrollment(
    Guid id,
    DeviceRole role,
    string deviceId,
    string protocolId,
    string identityFingerprint,
    string displayName,
    string? modelNumber,
    string? firmwareRevision,
    TreadmillTelemetryMode? telemetryMode,
    TreadmillCapabilities? capabilities,
    TreadmillCapabilityEvidence evidence,
    DateTimeOffset? lastVerifiedAtUtc,
    HeartRateDeviceKind? heartRateDeviceKind = null,
    HeartRateDeviceFamily? heartRateDeviceFamily = null)
  {
    if (id == Guid.Empty) throw new ArgumentException("Enrollment ID cannot be empty.", nameof(id));
    DeviceId = RequireText(deviceId, 256, nameof(deviceId));
    ProtocolId = RequireText(protocolId, 100, nameof(protocolId));
    IdentityFingerprint = RequireFingerprint(identityFingerprint);
    DisplayName = RequireText(displayName, 100, nameof(displayName));
    ModelNumber = OptionalText(modelNumber, 100, nameof(modelNumber));
    FirmwareRevision = OptionalText(firmwareRevision, 100, nameof(firmwareRevision));

    if (role == DeviceRole.Treadmill && (telemetryMode is null || capabilities is null))
    {
      throw new ArgumentException("Treadmill enrollment requires a telemetry mode and capabilities.");
    }

    if (role == DeviceRole.HeartRate && (telemetryMode is not null || capabilities is not null))
    {
      throw new ArgumentException("Heart-rate enrollment cannot contain treadmill telemetry settings.");
    }

    if (role == DeviceRole.Treadmill && (heartRateDeviceKind is not null || heartRateDeviceFamily is not null))
    {
      throw new ArgumentException("Treadmill enrollment cannot contain heart-rate sensor metadata.");
    }

    Id = id;
    Role = role;
    TelemetryMode = telemetryMode;
    Capabilities = capabilities;
    Evidence = evidence;
    LastVerifiedAtUtc = lastVerifiedAtUtc;
    HeartRateDeviceKind = role == DeviceRole.HeartRate
      ? heartRateDeviceKind ?? HeartRateDeviceClassifier.Classify(displayName)
      : null;
    HeartRateDeviceFamily = role == DeviceRole.HeartRate
      ? heartRateDeviceFamily ?? HeartRateDeviceClassifier.Family(displayName)
      : null;
  }

  public Guid Id { get; }
  public DeviceRole Role { get; }
  public string DeviceId { get; }
  public string ProtocolId { get; }
  public string IdentityFingerprint { get; }
  public string DisplayName { get; }
  public string? ModelNumber { get; }
  public string? FirmwareRevision { get; }
  public TreadmillTelemetryMode? TelemetryMode { get; }
  public TreadmillCapabilities? Capabilities { get; }
  public TreadmillCapabilityEvidence Evidence { get; }
  public DateTimeOffset? LastVerifiedAtUtc { get; }
  public HeartRateDeviceKind? HeartRateDeviceKind { get; }
  public HeartRateDeviceFamily? HeartRateDeviceFamily { get; }

  private static string RequireText(string value, int maximumLength, string parameterName)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(value, parameterName);
    string normalized = value.Trim();
    if (normalized.Length > maximumLength) throw new ArgumentOutOfRangeException(parameterName);
    return normalized;
  }

  private static string? OptionalText(string? value, int maximumLength, string parameterName)
  {
    if (string.IsNullOrWhiteSpace(value)) return null;
    string normalized = value.Trim();
    if (normalized.Length > maximumLength) throw new ArgumentOutOfRangeException(parameterName);
    return normalized;
  }

  private static string RequireFingerprint(string value)
  {
    string normalized = RequireText(value, 64, nameof(value));
    if (normalized.Length != 64 || !normalized.All(Uri.IsHexDigit))
    {
      throw new ArgumentException("Identity fingerprint must be 64 hexadecimal characters.", nameof(value));
    }

    return normalized.ToLowerInvariant();
  }
}
