namespace TreadmillRunner.Web.Live;

public enum ControlFocusMode
{
  Balanced,
  Chart,
  Controls,
}

public sealed class ControlFocusState
{
  private Guid? sessionId;
  private ControlFocusMode mode = ControlFocusMode.Balanced;

  public ControlFocusMode ForSession(Guid activeSessionId)
  {
    if (sessionId == activeSessionId) return mode;
    sessionId = activeSessionId;
    mode = ControlFocusMode.Balanced;
    return mode;
  }

  public void Set(Guid activeSessionId, ControlFocusMode selected)
  {
    if (sessionId != activeSessionId)
    {
      sessionId = activeSessionId;
      mode = ControlFocusMode.Balanced;
    }
    mode = selected;
  }
}
