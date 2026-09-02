namespace TreadmillRunner.Core.Control;

public enum WorkoutCompletionAction
{
  Continue,
  Finalize,
  RequestStop,
  AwaitPhysicalStop,
}

public sealed record WorkoutCompletionStopContext(
  bool ProgressionComplete,
  bool HardwareMode,
  bool TelemetryFresh,
  bool IsMoving,
  double MeasuredSpeedKph,
  bool CanStopRemotely,
  bool TreadmillReady,
  bool ConnectionGenerationCurrent,
  bool StopAttempted);

public static class WorkoutCompletionStopPolicy
{
  public static WorkoutCompletionAction Evaluate(WorkoutCompletionStopContext context)
  {
    ArgumentNullException.ThrowIfNull(context);
    if (!context.ProgressionComplete) return WorkoutCompletionAction.Continue;
    if (!context.HardwareMode) return WorkoutCompletionAction.Finalize;
    bool speedValid = double.IsFinite(context.MeasuredSpeedKph) && context.MeasuredSpeedKph >= 0;
    if (context.TelemetryFresh && speedValid && !context.IsMoving && context.MeasuredSpeedKph <= 0.05)
      return WorkoutCompletionAction.Finalize;
    if (context.StopAttempted) return WorkoutCompletionAction.AwaitPhysicalStop;
    return context.TelemetryFresh &&
      speedValid &&
      context.IsMoving &&
      context.MeasuredSpeedKph > 0.05 &&
      context.CanStopRemotely &&
      context.TreadmillReady &&
      context.ConnectionGenerationCurrent
        ? WorkoutCompletionAction.RequestStop
        : WorkoutCompletionAction.AwaitPhysicalStop;
  }
}
