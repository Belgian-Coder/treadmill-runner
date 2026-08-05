namespace TreadmillRunner.Core.Sessions;

public abstract record SessionEvent(DateTimeOffset OccurredAt)
{
  public abstract string EventType { get; }
}

public sealed record ManualSpeedOverrideEvent(
    double ExpectedSpeedKph,
    double ObservedSpeedKph,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "manual-speed-override";
}

public sealed record ManualInclineOverrideEvent(
    double PreviousInclinePercent,
    double RequestedInclinePercent,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "manual-incline-override";
}

public sealed record WorkoutStepTransitionEvent(
    int CompletedStepIndex,
    int? CurrentStepIndex,
    string? Cue,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "workout-step-transition";
}

public enum SessionPauseReason
{
  WebControl,
  PhysicalConsole,
  TreadmillStopped,
}

public sealed record SessionPausedEvent(
    SessionPauseReason Reason,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-paused";
}

public sealed record SessionResumedEvent(DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-resumed";
}

public enum SessionDeviceRole
{
  Treadmill,
  HeartRate,
}

public sealed record DeviceDisconnectedEvent(
    SessionDeviceRole DeviceRole,
    string? Reason,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "device-disconnected";
}

public sealed record DeviceReconnectedEvent(
    SessionDeviceRole DeviceRole,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "device-reconnected";
}

public sealed record SessionWarningEvent(
    string Code,
    string Message,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-warning";
}

public enum ControlLeaseEventKind
{
  Acquired,
  Renewed,
  Released,
  Expired,
  Reclaimed,
}

public sealed record ControlLeaseEvent(
    ControlLeaseEventKind Kind,
    Guid LeaseId,
    string HolderId,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "control-lease";
}

public sealed record SessionCompletedEvent(DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-completed";
}

public sealed record SessionStoppedEvent(DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-stopped";
}

public sealed record SessionInterruptedEvent(
    string? Reason,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-interrupted";
}

public sealed record SessionFaultedEvent(
    string Code,
    string Message,
    DateTimeOffset OccurredAt) : SessionEvent(OccurredAt)
{
  public override string EventType => "session-faulted";
}
