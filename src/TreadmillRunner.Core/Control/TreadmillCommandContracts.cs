using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Control;

public enum TreadmillCommandKind
{
  Start,
  SetSpeed,
  SetIncline,
  Pause,
  Stop,
}

public enum TreadmillCommandDisposition
{
  Rejected,
  Confirmed,
  Unknown,
}

public enum TreadmillCommandOrigin
{
  Manual,
  PlannedTransition,
  HeartRateAutomation,
  Commissioning,
}

public sealed record TreadmillCommandIntent
{
  public static readonly TimeSpan MaximumLifetime = TimeSpan.FromSeconds(5);

  public TreadmillCommandIntent(
    Guid operationId,
    Guid sessionId,
    TreadmillCommandKind kind,
    DateTimeOffset issuedAt,
    DateTimeOffset expiresAt,
    long expectedSessionVersion,
    SessionState expectedSessionState,
    Guid leaseId,
    string holderId,
    long connectionGeneration,
    double? requestedValue,
    TreadmillCommandOrigin origin = TreadmillCommandOrigin.Manual)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("Operation ID cannot be empty.", nameof(operationId));
    if (sessionId == Guid.Empty) throw new ArgumentException("Session ID cannot be empty.", nameof(sessionId));
    if (leaseId == Guid.Empty) throw new ArgumentException("Lease ID cannot be empty.", nameof(leaseId));
    if (expectedSessionVersion < 0) throw new ArgumentOutOfRangeException(nameof(expectedSessionVersion));
    if (connectionGeneration <= 0) throw new ArgumentOutOfRangeException(nameof(connectionGeneration));
    ArgumentException.ThrowIfNullOrWhiteSpace(holderId);
    string normalizedHolder = holderId.Trim();
    if (normalizedHolder.Length > 128) throw new ArgumentOutOfRangeException(nameof(holderId));

    TimeSpan lifetime = expiresAt - issuedAt;
    if (lifetime <= TimeSpan.Zero || lifetime > MaximumLifetime)
    {
      throw new ArgumentOutOfRangeException(nameof(expiresAt), "Command intent lifetime must be greater than zero and at most five seconds.");
    }

    if (kind is TreadmillCommandKind.Start or TreadmillCommandKind.SetSpeed or TreadmillCommandKind.SetIncline)
    {
      if (requestedValue is not { } target || !double.IsFinite(target) || target < 0)
      {
        throw new ArgumentOutOfRangeException(nameof(requestedValue), $"{kind} requires a finite non-negative target value.");
      }
    }
    else if (requestedValue is not null)
    {
      throw new ArgumentException($"{kind} does not accept a requested value.", nameof(requestedValue));
    }

    OperationId = operationId;
    SessionId = sessionId;
    Kind = kind;
    IssuedAt = issuedAt;
    ExpiresAt = expiresAt;
    ExpectedSessionVersion = expectedSessionVersion;
    ExpectedSessionState = expectedSessionState;
    LeaseId = leaseId;
    HolderId = normalizedHolder;
    ConnectionGeneration = connectionGeneration;
    RequestedValue = requestedValue;
    Origin = origin;
  }

  public Guid OperationId { get; }
  public Guid SessionId { get; }
  public TreadmillCommandKind Kind { get; }
  public DateTimeOffset IssuedAt { get; }
  public DateTimeOffset ExpiresAt { get; }
  public long ExpectedSessionVersion { get; }
  public SessionState ExpectedSessionState { get; }
  public Guid LeaseId { get; }
  public string HolderId { get; }
  public long ConnectionGeneration { get; }
  public double? RequestedValue { get; }
  public TreadmillCommandOrigin Origin { get; }
}

public sealed record TreadmillCommandResult
{
  public TreadmillCommandResult(
    Guid operationId,
    TreadmillCommandKind kind,
    TreadmillCommandDisposition disposition,
    double? requestedValue,
    double? acceptedValue,
    double? measuredValue,
    string reason,
    long connectionGeneration,
    DateTimeOffset issuedAt,
    DateTimeOffset completedAt)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("Operation ID cannot be empty.", nameof(operationId));
    if (connectionGeneration <= 0) throw new ArgumentOutOfRangeException(nameof(connectionGeneration));
    if (completedAt < issuedAt) throw new ArgumentOutOfRangeException(nameof(completedAt));
    ArgumentException.ThrowIfNullOrWhiteSpace(reason);
    ValidateOptionalValue(requestedValue, nameof(requestedValue));
    ValidateOptionalValue(acceptedValue, nameof(acceptedValue));
    ValidateOptionalValue(measuredValue, nameof(measuredValue));

    OperationId = operationId;
    Kind = kind;
    Disposition = disposition;
    RequestedValue = requestedValue;
    AcceptedValue = acceptedValue;
    MeasuredValue = measuredValue;
    Reason = reason.Trim();
    ConnectionGeneration = connectionGeneration;
    IssuedAt = issuedAt;
    CompletedAt = completedAt;
  }

  public Guid OperationId { get; }
  public TreadmillCommandKind Kind { get; }
  public TreadmillCommandDisposition Disposition { get; }
  public double? RequestedValue { get; }
  public double? AcceptedValue { get; }
  public double? MeasuredValue { get; }
  public string Reason { get; }
  public long ConnectionGeneration { get; }
  public DateTimeOffset IssuedAt { get; }
  public DateTimeOffset CompletedAt { get; }

  private static void ValidateOptionalValue(double? value, string parameterName)
  {
    if (value is { } present && (!double.IsFinite(present) || present < 0))
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }
}
