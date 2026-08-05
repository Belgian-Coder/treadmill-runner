namespace TreadmillRunner.Core.Sessions;

public sealed class SessionStateMachine
{
  public const double PhysicalStartThresholdKph = 0.3;
  public const int RequiredPhysicalStartSamples = 3;

  private readonly TimeProvider _timeProvider;
  private readonly List<SessionEvent> _events = [];
  private int _consecutiveMovingSamples;

  public SessionStateMachine(TimeProvider timeProvider)
  {
    _timeProvider = timeProvider ?? throw new ArgumentNullException(nameof(timeProvider));
  }

  public SessionState State { get; private set; } = SessionState.Idle;

  public long Version { get; private set; }

  public IReadOnlyList<SessionEvent> Events => _events.AsReadOnly();

  public event EventHandler<ManualSpeedOverrideEvent>? ManualSpeedOverrideRecorded;

  public void Arm()
  {
    RequireState(SessionState.Idle);
    TransitionTo(SessionState.ArmedWaitingForPhysicalStart);
  }

  public void ObserveTelemetry(double speedKph)
  {
    if (!double.IsFinite(speedKph) || speedKph < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(speedKph));
    }

    if (State is not (SessionState.ArmedWaitingForPhysicalStart or
        SessionState.PausedWaitingForPhysicalResume))
    {
      _consecutiveMovingSamples = 0;
      return;
    }

    _consecutiveMovingSamples = speedKph > PhysicalStartThresholdKph
        ? _consecutiveMovingSamples + 1
        : 0;

    if (_consecutiveMovingSamples >= RequiredPhysicalStartSamples)
    {
      TransitionTo(SessionState.Running);
    }
  }

  public void PauseWaitingForPhysicalResume()
  {
    RequireState(SessionState.Running);
    TransitionTo(SessionState.PausedWaitingForPhysicalResume);
  }

  public void RecordManualSpeedOverride(double expectedSpeedKph, double observedSpeedKph)
  {
    RequireState(SessionState.Running, SessionState.PausedWaitingForPhysicalResume);
    ValidateSpeed(expectedSpeedKph, nameof(expectedSpeedKph));
    ValidateSpeed(observedSpeedKph, nameof(observedSpeedKph));

    var @event = new ManualSpeedOverrideEvent(
        expectedSpeedKph,
        observedSpeedKph,
        _timeProvider.GetUtcNow());
    _events.Add(@event);
    Version++;
    ManualSpeedOverrideRecorded?.Invoke(this, @event);
  }

  public void MarkConfigurationChanged()
  {
    RequireState(SessionState.Running, SessionState.PausedWaitingForPhysicalResume);
    Version++;
  }

  public void Complete()
  {
    RequireState(SessionState.Running, SessionState.PausedWaitingForPhysicalResume);
    TransitionTo(SessionState.Completed);
  }

  public void Stop()
  {
    RequireActiveState();
    TransitionTo(SessionState.Stopped);
  }

  public void Interrupt()
  {
    RequireActiveState();
    TransitionTo(SessionState.Interrupted);
  }

  public void Fault()
  {
    RequireActiveState();
    TransitionTo(SessionState.Faulted);
  }

  public void Reset()
  {
    RequireState(
        SessionState.Completed,
        SessionState.Stopped,
        SessionState.Interrupted,
        SessionState.Faulted);
    _events.Clear();
    TransitionTo(SessionState.Idle);
  }

  private void RequireActiveState() => RequireState(
      SessionState.ArmedWaitingForPhysicalStart,
      SessionState.Running,
      SessionState.PausedWaitingForPhysicalResume);

  private void RequireState(params SessionState[] allowed)
  {
    if (!allowed.Contains(State))
    {
      throw new InvalidOperationException(
          $"Session state {State} does not allow this transition.");
    }
  }

  private void TransitionTo(SessionState state)
  {
    State = state;
    Version++;
    _consecutiveMovingSamples = 0;
  }

  private static void ValidateSpeed(double speedKph, string parameterName)
  {
    if (!double.IsFinite(speedKph) || speedKph < 0)
    {
      throw new ArgumentOutOfRangeException(parameterName);
    }
  }
}
