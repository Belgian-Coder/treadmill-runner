using TreadmillRunner.Web.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class ControlFocusStateTests
{
  [Fact]
  public void Focus_is_remembered_for_the_active_session()
  {
    var state = new ControlFocusState();
    Guid sessionId = Guid.NewGuid();

    state.Set(sessionId, ControlFocusMode.Chart);

    Assert.Equal(ControlFocusMode.Chart, state.ForSession(sessionId));
  }

  [Fact]
  public void A_new_session_resets_focus_to_balanced()
  {
    var state = new ControlFocusState();
    state.Set(Guid.NewGuid(), ControlFocusMode.Controls);

    Assert.Equal(ControlFocusMode.Balanced, state.ForSession(Guid.NewGuid()));
  }
}
