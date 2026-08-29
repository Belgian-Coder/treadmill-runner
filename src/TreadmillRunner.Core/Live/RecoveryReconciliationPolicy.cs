using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Live;

public enum RecoveryReconciliationAction
{
  ResumeAutomatically,
  RequireExplicitResume,
  Blocked,
}

public sealed record RecoveryReconciliationInput(
  SessionState SessionState,
  bool SameEnrolledTreadmill,
  bool FreshStableTelemetry,
  bool RecoveredAfterGatewayRestart,
  double MeasuredSpeedKph,
  double MeasuredInclinePercent,
  double PreGapSpeedKph,
  double PreGapInclinePercent,
  double SpeedIncrementKph,
  double InclineIncrementPercent,
  TreadmillCommandDisposition? LastCommandDisposition);

public sealed record RecoveryReconciliationDecision(
  RecoveryReconciliationAction Action,
  bool PossibleConsoleIntervention,
  string Reason);

public static class RecoveryReconciliationPolicy
{
  public static RecoveryReconciliationDecision Evaluate(RecoveryReconciliationInput input)
  {
    ArgumentNullException.ThrowIfNull(input);
    if (!input.SameEnrolledTreadmill)
      return Blocked("The enrolled treadmill identity does not match the active session.");
    if (!input.FreshStableTelemetry)
      return Blocked("Fresh stable treadmill telemetry is required before recovery.");
    if (input.LastCommandDisposition == TreadmillCommandDisposition.Unknown)
      return Blocked("An unknown command outcome blocks automatic recovery.");
    if (input.SessionState != SessionState.Running)
      return Blocked("Only a running session can reconcile planned controls.");
    if (!double.IsFinite(input.MeasuredSpeedKph) ||
        !double.IsFinite(input.MeasuredInclinePercent) ||
        !double.IsFinite(input.PreGapSpeedKph) ||
        !double.IsFinite(input.PreGapInclinePercent) ||
        !double.IsFinite(input.SpeedIncrementKph) ||
        !double.IsFinite(input.InclineIncrementPercent))
      return Blocked("Finite treadmill telemetry and operating increments are required before recovery.");
    if (input.MeasuredSpeedKph <= SessionStateMachine.PhysicalStartThresholdKph)
      return Blocked("The belt is not moving; recovery will never issue Start.");

    double speedTolerance = Math.Max(0.01, input.SpeedIncrementKph) + 0.01;
    double inclineTolerance = Math.Max(0.01, input.InclineIncrementPercent) + 0.01;
    bool consoleIntervention = Math.Abs(input.MeasuredSpeedKph - input.PreGapSpeedKph) > speedTolerance ||
      Math.Abs(input.MeasuredInclinePercent - input.PreGapInclinePercent) > inclineTolerance;
    if (consoleIntervention)
      return new RecoveryReconciliationDecision(
        RecoveryReconciliationAction.RequireExplicitResume,
        true,
        "The treadmill changed while disconnected; planned controls will not override a possible console action.");
    if (input.RecoveredAfterGatewayRestart)
      return new RecoveryReconciliationDecision(
        RecoveryReconciliationAction.RequireExplicitResume,
        false,
        "Gateway restart recovery confirmed movement; planned controls require one explicit resume.");

    return new RecoveryReconciliationDecision(
      RecoveryReconciliationAction.ResumeAutomatically,
      false,
      "Fresh telemetry from the same moving treadmill was confirmed.");
  }

  private static RecoveryReconciliationDecision Blocked(string reason) =>
    new(RecoveryReconciliationAction.Blocked, false, reason);
}
