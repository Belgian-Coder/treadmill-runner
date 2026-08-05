namespace TreadmillRunner.Core.Sessions;

public enum SessionState
{
  Idle,
  ArmedWaitingForPhysicalStart,
  Running,
  PausedWaitingForPhysicalResume,
  Completed,
  Stopped,
  Interrupted,
  Faulted,
}
