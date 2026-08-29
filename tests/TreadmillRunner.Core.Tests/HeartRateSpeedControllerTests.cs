using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Core.Tests;

public sealed class HeartRateSpeedControllerTests
{
  private static readonly DateTimeOffset Start = new(2026, 8, 4, 20, 0, 0, TimeSpan.Zero);

  [Fact]
  public void Requires_twenty_seconds_below_target_and_aligns_increase_down()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    Assert.Equal(HeartRateSpeedDecisionKind.None, controller.Evaluate(Input(Start, 120, HeartRateAutomationMode.Full)).Kind);

    HeartRateSpeedDecision decision = controller.Evaluate(Input(
      Start.AddSeconds(20), 120, HeartRateAutomationMode.Full, speed: 1.05));

    Assert.Equal(HeartRateSpeedDecisionKind.Increase, decision.Kind);
    Assert.True(decision.ShouldExecute);
    Assert.Equal(1.2, decision.TargetSpeedKph);
  }

  [Fact]
  public void Requires_ten_seconds_above_target_and_aligns_decrease_up()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    controller.Evaluate(Input(Start, 160, HeartRateAutomationMode.DecreaseOnly, speed: 2.05));

    HeartRateSpeedDecision decision = controller.Evaluate(Input(
      Start.AddSeconds(10), 160, HeartRateAutomationMode.DecreaseOnly, speed: 2.05));

    Assert.Equal(HeartRateSpeedDecisionKind.Decrease, decision.Kind);
    Assert.True(decision.ShouldExecute);
    Assert.Equal(1.6, decision.TargetSpeedKph);
  }

  [Fact]
  public void Shadow_mode_reports_but_never_executes_and_stale_data_resets_dwell()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    controller.Evaluate(Input(Start, 120, HeartRateAutomationMode.Shadow));
    HeartRateSpeedDecision shadow = controller.Evaluate(Input(Start.AddSeconds(20), 120, HeartRateAutomationMode.Shadow));
    Assert.Equal(HeartRateSpeedDecisionKind.Increase, shadow.Kind);
    Assert.False(shadow.ShouldExecute);

    HeartRateSpeedControllerInput stale = Input(Start.AddSeconds(21), 120, HeartRateAutomationMode.Full) with
    {
      HeartRateAge = TimeSpan.FromSeconds(6),
    };
    Assert.Equal(HeartRateSpeedDecisionKind.None, controller.Evaluate(stale).Kind);
    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start.AddSeconds(40), 120, HeartRateAutomationMode.Full)).Kind);
  }

  [Fact]
  public void Realistic_run_adjusts_only_after_dwell_and_cooldown_and_recovers_safely()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    double speed = 4.5;

    HeartRateSpeedDecision firstIncrease = RunSecondBySecond(
      controller, Start, 20, 120, HeartRateAutomationMode.Full, speed);
    Assert.Equal(HeartRateSpeedDecisionKind.Increase, firstIncrease.Kind);
    Assert.True(firstIncrease.ShouldExecute);
    speed = Assert.IsType<double>(firstIncrease.TargetSpeedKph);
    Assert.Equal(4.7, speed);

    HeartRateSpeedDecision cooldown = controller.Evaluate(Input(
      Start.AddSeconds(40), 120, HeartRateAutomationMode.Full, speed));
    Assert.Equal(HeartRateSpeedDecisionKind.None, cooldown.Kind);
    Assert.Contains("cooldown", cooldown.Reason, StringComparison.OrdinalIgnoreCase);

    HeartRateSpeedDecision secondIncrease = controller.Evaluate(Input(
      Start.AddSeconds(50), 120, HeartRateAutomationMode.Full, speed));
    Assert.Equal(HeartRateSpeedDecisionKind.Increase, secondIncrease.Kind);
    speed = Assert.IsType<double>(secondIncrease.TargetSpeedKph);
    Assert.Equal(4.9, speed);

    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start.AddSeconds(51), 140, HeartRateAutomationMode.Full, speed)).Kind);

    HeartRateSpeedDecision decrease = RunSecondBySecond(
      controller, Start.AddSeconds(52), 10, 160, HeartRateAutomationMode.Full, speed);
    Assert.Equal(HeartRateSpeedDecisionKind.Decrease, decrease.Kind);
    Assert.True(decrease.ShouldExecute);
    speed = Assert.IsType<double>(decrease.TargetSpeedKph);
    Assert.Equal(4.4, speed);

    controller.ResetDwell();
    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start.AddSeconds(63), 120, HeartRateAutomationMode.Full, speed)).Kind);
    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start.AddSeconds(82), 120, HeartRateAutomationMode.Full, speed)).Kind);
    HeartRateSpeedDecision afterResume = controller.Evaluate(Input(
      Start.AddSeconds(83), 120, HeartRateAutomationMode.Full, speed));
    Assert.Equal(HeartRateSpeedDecisionKind.Increase, afterResume.Kind);
  }

  [Fact]
  public void Dwell_reset_preserves_cooldown_but_requires_a_fresh_dwell()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    RunSecondBySecond(controller, Start, 20, 120, HeartRateAutomationMode.Full, 4.5);

    controller.ResetDwell();

    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start.AddSeconds(21), 120, HeartRateAutomationMode.Full, 4.7)).Kind);
    HeartRateSpeedDecision stillCoolingDown = controller.Evaluate(Input(
      Start.AddSeconds(41), 120, HeartRateAutomationMode.Full, 4.7));
    Assert.Equal(HeartRateSpeedDecisionKind.None, stillCoolingDown.Kind);
    Assert.Contains("cooldown", stillCoolingDown.Reason, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Invalid_heart_rate_never_authorizes_a_speed_increase()
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);

    Assert.Equal(HeartRateSpeedDecisionKind.None,
      controller.Evaluate(Input(Start, 0, HeartRateAutomationMode.Full)).Kind);
    HeartRateSpeedDecision decision = controller.Evaluate(
      Input(Start.AddSeconds(20), 0, HeartRateAutomationMode.Full));

    Assert.Equal(HeartRateSpeedDecisionKind.None, decision.Kind);
    Assert.False(decision.ShouldExecute);
  }

  [Theory]
  [InlineData(true)]
  [InlineData(false)]
  public void Negative_telemetry_age_never_authorizes_automation(bool heartRateAgeIsNegative)
  {
    var controller = new HeartRateSpeedController(HeartRateControllerSettings.Default);
    HeartRateSpeedControllerInput first = Input(Start, 120, HeartRateAutomationMode.Full) with
    {
      HeartRateAge = heartRateAgeIsNegative ? TimeSpan.FromSeconds(-1) : TimeSpan.Zero,
      TreadmillAge = heartRateAgeIsNegative ? TimeSpan.Zero : TimeSpan.FromSeconds(-1),
    };
    HeartRateSpeedControllerInput afterDwell = first with { Now = Start.AddSeconds(20) };

    Assert.Equal(HeartRateSpeedDecisionKind.None, controller.Evaluate(first).Kind);
    HeartRateSpeedDecision decision = controller.Evaluate(afterDwell);

    Assert.Equal(HeartRateSpeedDecisionKind.None, decision.Kind);
    Assert.False(decision.ShouldExecute);
  }

  [Theory]
  [InlineData(0.09, 30, 0.5, 15)]
  [InlineData(0.2, 14, 0.5, 15)]
  [InlineData(0.2, 30, 1.01, 15)]
  [InlineData(0.2, 30, 0.5, 121)]
  public void Rejects_out_of_range_profile_settings(double increase, int increaseCooldown, double decrease, int decreaseCooldown)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() =>
      new HeartRateControllerSettings(increase, increaseCooldown, decrease, decreaseCooldown));
  }

  private static HeartRateSpeedControllerInput Input(
    DateTimeOffset now,
    ushort heartRate,
    HeartRateAutomationMode mode,
    double speed = 1.0) => new(
      now,
      mode,
      heartRate,
      130,
      150,
      TimeSpan.Zero,
      TimeSpan.Zero,
      speed,
      0.8,
      10,
      0.1,
      true);

  private static HeartRateSpeedDecision RunSecondBySecond(
    HeartRateSpeedController controller,
    DateTimeOffset start,
    int seconds,
    ushort heartRate,
    HeartRateAutomationMode mode,
    double speed)
  {
    HeartRateSpeedDecision decision = HeartRateSpeedDecision.None("Not evaluated.");
    for (int second = 0; second <= seconds; second++)
      decision = controller.Evaluate(Input(start.AddSeconds(second), heartRate, mode, speed));
    return decision;
  }
}
