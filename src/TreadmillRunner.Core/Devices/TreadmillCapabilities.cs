namespace TreadmillRunner.Core.Devices;

public enum TreadmillCapabilityEvidence
{
  Unknown = 0,
  ProtocolReported = 1,
  PassivelyObserved = 2,
  HardwareVerified = 3,
}

public sealed record TreadmillOperatingRange
{
  public TreadmillOperatingRange(
    decimal minimum,
    decimal maximum,
    decimal increment,
    TreadmillCapabilityEvidence evidence)
  {
    if (maximum < minimum)
    {
      throw new ArgumentOutOfRangeException(
        nameof(maximum),
        maximum,
        "Maximum must be greater than or equal to minimum.");
    }

    if (increment <= 0m)
    {
      throw new ArgumentOutOfRangeException(
        nameof(increment),
        increment,
        "Increment must be greater than zero.");
    }

    Minimum = minimum;
    Maximum = maximum;
    Increment = increment;
    Evidence = evidence;
  }

  public decimal Minimum { get; }

  public decimal Maximum { get; }

  public decimal Increment { get; }

  public TreadmillCapabilityEvidence Evidence { get; }

  public bool Contains(decimal value) => value >= Minimum && value <= Maximum;

  public decimal Clamp(decimal value) => Math.Clamp(value, Minimum, Maximum);
}

public sealed record TreadmillCapabilities(
  bool CanSetSpeedRemotely = false,
  bool CanSetInclineRemotely = false,
  bool CanPauseRemotely = false,
  bool CanStopRemotely = false,
  bool CanStartRemotely = false,
  bool ReportsSpeedTargetSupport = false,
  bool ReportsInclineTargetSupport = false,
  bool ReportsStandardStartResume = false,
  TreadmillOperatingRange? SpeedRange = null,
  TreadmillOperatingRange? InclineRange = null);

public sealed record TreadmillAdvertisementIdentity(
  string? Name,
  IReadOnlyCollection<Guid> ServiceUuids);

public interface ITreadmillProtocol
{
  string ProtocolId { get; }

  string DisplayName { get; }

  int MatchPriority { get; }

  TreadmillCapabilities Capabilities { get; }

  bool CanHandle(TreadmillAdvertisementIdentity identity);
}

public sealed class TreadmillProtocolRegistry
{
  private readonly IReadOnlyList<ITreadmillProtocol> _protocols;

  public TreadmillProtocolRegistry(IEnumerable<ITreadmillProtocol> protocols)
  {
    ArgumentNullException.ThrowIfNull(protocols);

    _protocols = protocols
      .OrderByDescending(static protocol => protocol.MatchPriority)
      .ThenBy(static protocol => protocol.ProtocolId, StringComparer.OrdinalIgnoreCase)
      .ToArray();

    var duplicate = _protocols
      .GroupBy(static protocol => protocol.ProtocolId, StringComparer.OrdinalIgnoreCase)
      .FirstOrDefault(static group => group.Count() > 1);

    if (duplicate is not null)
    {
      throw new ArgumentException(
        $"Treadmill protocol id '{duplicate.Key}' is registered more than once.",
        nameof(protocols));
    }
  }

  public ITreadmillProtocol? Resolve(TreadmillAdvertisementIdentity identity)
  {
    ArgumentNullException.ThrowIfNull(identity);

    return _protocols.FirstOrDefault(protocol => protocol.CanHandle(identity));
  }
}
