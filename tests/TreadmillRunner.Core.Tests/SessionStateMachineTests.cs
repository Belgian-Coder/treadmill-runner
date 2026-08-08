using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class SessionStateMachineTests
{
  [Fact]
  public void Restores_only_an_active_state_and_version_without_replaying_transitions()
  {
    SessionStateMachine restored = SessionStateMachine.Restore(
      new TestTimeProvider(), SessionState.Running, version: 7);

    Assert.Equal(SessionState.Running, restored.State);
    Assert.Equal(7, restored.Version);
    Assert.Empty(restored.Events);
  }
  [Fact]
  public void Arm_requires_three_consecutive_physical_start_samples()
  {
    var time = new TestTimeProvider();
    var session = new SessionStateMachine(time);

    session.Arm();
    session.ObserveTelemetry(0.31);
    session.ObserveTelemetry(0.30);
    session.ObserveTelemetry(0.50);
    session.ObserveTelemetry(0.60);

    Assert.Equal(SessionState.ArmedWaitingForPhysicalStart, session.State);

    session.ObserveTelemetry(0.70);

    Assert.Equal(SessionState.Running, session.State);
  }

  [Fact]
  public void Paused_session_requires_physical_resume_samples()
  {
    var session = RunningSession();

    session.PauseWaitingForPhysicalResume();
    session.ObserveTelemetry(0.4);
    session.ObserveTelemetry(0.5);

    Assert.Equal(SessionState.PausedWaitingForPhysicalResume, session.State);

    session.ObserveTelemetry(0.6);

    Assert.Equal(SessionState.Running, session.State);
  }

  [Fact]
  public void Confirmed_stop_leaves_an_active_session_waiting_for_explicit_resume()
  {
    var session = RunningSession();

    session.StopWaitingForPhysicalResume();

    Assert.Equal(SessionState.PausedWaitingForPhysicalResume, session.State);
    session.StopWaitingForPhysicalResume();
    Assert.Equal(SessionState.PausedWaitingForPhysicalResume, session.State);
  }

  [Fact]
  public void Records_manual_speed_override_as_domain_event()
  {
    var time = new TestTimeProvider(new DateTimeOffset(2026, 8, 2, 10, 0, 0, TimeSpan.Zero));
    var session = RunningSession(time);
    long versionBeforeOverride = session.Version;

    session.RecordManualSpeedOverride(6.0, 7.2);

    var @event = Assert.Single(session.Events);
    var speedOverride = Assert.IsType<ManualSpeedOverrideEvent>(@event);
    Assert.Equal(6.0, speedOverride.ExpectedSpeedKph);
    Assert.Equal(7.2, speedOverride.ObservedSpeedKph);
    Assert.Equal(time.GetUtcNow(), speedOverride.OccurredAt);
    Assert.Equal(versionBeforeOverride + 1, session.Version);
  }

  [Theory]
  [InlineData(SessionState.Completed)]
  [InlineData(SessionState.Stopped)]
  [InlineData(SessionState.Interrupted)]
  [InlineData(SessionState.Faulted)]
  public void Active_session_can_reach_terminal_state(SessionState terminalState)
  {
    var session = RunningSession();

    switch (terminalState)
    {
      case SessionState.Completed: session.Complete(); break;
      case SessionState.Stopped: session.Stop(); break;
      case SessionState.Interrupted: session.Interrupt(); break;
      case SessionState.Faulted: session.Fault(); break;
    }

    Assert.Equal(terminalState, session.State);
  }

  [Fact]
  public void Invalid_transition_is_rejected()
  {
    var session = new SessionStateMachine(new TestTimeProvider());

    Assert.Throws<InvalidOperationException>(session.Complete);
  }

  private static SessionStateMachine RunningSession(TestTimeProvider? time = null)
  {
    var session = new SessionStateMachine(time ?? new TestTimeProvider());
    session.Arm();
    session.ObserveTelemetry(0.4);
    session.ObserveTelemetry(0.4);
    session.ObserveTelemetry(0.4);
    return session;
  }
}
