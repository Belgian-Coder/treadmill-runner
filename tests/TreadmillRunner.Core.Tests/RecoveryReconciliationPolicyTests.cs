using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class RecoveryReconciliationPolicyTests
{
  [Fact]
  public void Same_moving_session_with_stable_values_resumes_automatically()
  {
    RecoveryReconciliationDecision decision = RecoveryReconciliationPolicy.Evaluate(Input());

    Assert.Equal(RecoveryReconciliationAction.ResumeAutomatically, decision.Action);
  }

  [Theory]
  [InlineData(false, true, false, TreadmillCommandDisposition.Confirmed)]
  [InlineData(true, false, false, TreadmillCommandDisposition.Confirmed)]
  [InlineData(true, true, false, TreadmillCommandDisposition.Unknown)]
  public void Identity_freshness_and_unknown_outcomes_block_recovery(
    bool sameTreadmill,
    bool fresh,
    bool restart,
    TreadmillCommandDisposition disposition)
  {
    RecoveryReconciliationDecision decision = RecoveryReconciliationPolicy.Evaluate(
      Input() with
      {
        SameEnrolledTreadmill = sameTreadmill,
        FreshStableTelemetry = fresh,
        RecoveredAfterGatewayRestart = restart,
        LastCommandDisposition = disposition,
      });

    Assert.Equal(RecoveryReconciliationAction.Blocked, decision.Action);
  }

  [Fact]
  public void Stopped_belt_is_blocked_and_never_becomes_a_start_request()
  {
    RecoveryReconciliationDecision decision = RecoveryReconciliationPolicy.Evaluate(
      Input() with { MeasuredSpeedKph = 0 });

    Assert.Equal(RecoveryReconciliationAction.Blocked, decision.Action);
    Assert.Contains("never issue Start", decision.Reason, StringComparison.Ordinal);
  }

  [Fact]
  public void Non_finite_telemetry_never_resumes_planned_controls()
  {
    RecoveryReconciliationDecision decision = RecoveryReconciliationPolicy.Evaluate(
      Input() with { MeasuredSpeedKph = double.NaN });

    Assert.Equal(RecoveryReconciliationAction.Blocked, decision.Action);
    Assert.Contains("Finite treadmill telemetry", decision.Reason, StringComparison.Ordinal);
  }

  [Fact]
  public void Console_change_and_gateway_restart_require_explicit_resume()
  {
    RecoveryReconciliationDecision console = RecoveryReconciliationPolicy.Evaluate(
      Input() with { MeasuredSpeedKph = 6.3 });
    RecoveryReconciliationDecision restart = RecoveryReconciliationPolicy.Evaluate(
      Input() with { RecoveredAfterGatewayRestart = true });

    Assert.Equal(RecoveryReconciliationAction.RequireExplicitResume, console.Action);
    Assert.True(console.PossibleConsoleIntervention);
    Assert.Equal(RecoveryReconciliationAction.RequireExplicitResume, restart.Action);
  }

  private static RecoveryReconciliationInput Input() => new(
    SessionState.Running,
    SameEnrolledTreadmill: true,
    FreshStableTelemetry: true,
    RecoveredAfterGatewayRestart: false,
    MeasuredSpeedKph: 6,
    MeasuredInclinePercent: 1,
    PreGapSpeedKph: 6,
    PreGapInclinePercent: 1,
    SpeedIncrementKph: 0.1,
    InclineIncrementPercent: 0.5,
    LastCommandDisposition: TreadmillCommandDisposition.Confirmed);
}
