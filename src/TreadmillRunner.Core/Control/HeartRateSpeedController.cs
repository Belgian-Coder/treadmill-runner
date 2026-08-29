using TreadmillRunner.Core.Profiles;

namespace TreadmillRunner.Core.Control;

public enum HeartRateAutomationMode
{
  Disabled,
  Shadow,
  DecreaseOnly,
  Full,
  SuspendedManualOverride,
  SuspendedSafety,
}

public enum HeartRateSpeedDecisionKind
{
  None,
  Increase,
  Decrease,
}

public sealed record HeartRateSpeedControllerInput(
  DateTimeOffset Now,
  HeartRateAutomationMode Mode,
  ushort? HeartRateBpm,
  ushort? TargetMinimumBpm,
  ushort? TargetMaximumBpm,
  TimeSpan? HeartRateAge,
  TimeSpan? TreadmillAge,
  double CurrentSpeedKph,
  double MinimumSpeedKph,
  double MaximumSpeedKph,
  double SpeedIncrementKph,
  bool SafetyReady);

public sealed record HeartRateSpeedDecision(
  HeartRateSpeedDecisionKind Kind,
  double? TargetSpeedKph,
  bool ShouldExecute,
  string Reason)
{
  public static HeartRateSpeedDecision None(string reason) =>
    new(HeartRateSpeedDecisionKind.None, null, false, reason);
}

public sealed class HeartRateSpeedController(HeartRateControllerSettings settings)
{
  private static readonly TimeSpan FreshnessLimit = TimeSpan.FromSeconds(5);
  private static readonly TimeSpan BelowTargetDwell = TimeSpan.FromSeconds(20);
  private static readonly TimeSpan AboveTargetDwell = TimeSpan.FromSeconds(10);
  private DateTimeOffset? _belowSince;
  private DateTimeOffset? _aboveSince;
  private DateTimeOffset? _lastIncreaseAt;
  private DateTimeOffset? _lastDecreaseAt;

  public HeartRateSpeedDecision Evaluate(HeartRateSpeedControllerInput input)
  {
    ArgumentNullException.ThrowIfNull(input);
    Validate(input);
    if (input.Mode is HeartRateAutomationMode.Disabled or
        HeartRateAutomationMode.SuspendedManualOverride or
        HeartRateAutomationMode.SuspendedSafety)
    {
      ResetDwell();
      return HeartRateSpeedDecision.None($"Automation is {input.Mode}.");
    }

    if (!input.SafetyReady ||
        input.HeartRateBpm is not (>= 30 and <= 250) ||
        input.TargetMinimumBpm is null ||
        input.TargetMaximumBpm is null ||
        input.HeartRateAge is null || input.HeartRateAge < TimeSpan.Zero || input.HeartRateAge > FreshnessLimit ||
        input.TreadmillAge is null || input.TreadmillAge < TimeSpan.Zero || input.TreadmillAge > FreshnessLimit)
    {
      ResetDwell();
      return HeartRateSpeedDecision.None("Fresh heart-rate and treadmill telemetry plus a safe command context are required.");
    }

    if (input.HeartRateBpm < input.TargetMinimumBpm)
    {
      _aboveSince = null;
      _belowSince ??= input.Now;
      if (input.Now - _belowSince < BelowTargetDwell)
        return HeartRateSpeedDecision.None("Heart rate has not remained below target for 20 seconds.");
      if (_lastIncreaseAt is { } last && input.Now - last < TimeSpan.FromSeconds(settings.IncreaseCooldownSeconds))
        return HeartRateSpeedDecision.None("Increase cooldown is active.");
      if (input.Mode == HeartRateAutomationMode.DecreaseOnly)
        return HeartRateSpeedDecision.None("Increases are disabled in decrease-only mode.");

      double target = AlignWithoutAggression(
        input.CurrentSpeedKph + settings.IncreaseStepKph,
        input.CurrentSpeedKph,
        input.MinimumSpeedKph,
        input.MaximumSpeedKph,
        input.SpeedIncrementKph);
      if (target <= input.CurrentSpeedKph + 0.0001)
        return HeartRateSpeedDecision.None("The maximum permitted speed has been reached.");
      _lastIncreaseAt = input.Now;
      _belowSince = input.Now;
      return new HeartRateSpeedDecision(
        HeartRateSpeedDecisionKind.Increase,
        target,
        input.Mode == HeartRateAutomationMode.Full,
        input.Mode == HeartRateAutomationMode.Shadow
          ? "Shadow-mode increase recorded without a write."
          : "Below-target dwell and increase cooldown passed.");
    }

    if (input.HeartRateBpm > input.TargetMaximumBpm)
    {
      _belowSince = null;
      _aboveSince ??= input.Now;
      if (input.Now - _aboveSince < AboveTargetDwell)
        return HeartRateSpeedDecision.None("Heart rate has not remained above target for 10 seconds.");
      if (_lastDecreaseAt is { } last && input.Now - last < TimeSpan.FromSeconds(settings.DecreaseCooldownSeconds))
        return HeartRateSpeedDecision.None("Decrease cooldown is active.");

      double target = AlignWithoutAggression(
        input.CurrentSpeedKph - settings.DecreaseStepKph,
        input.CurrentSpeedKph,
        input.MinimumSpeedKph,
        input.MaximumSpeedKph,
        input.SpeedIncrementKph);
      if (target >= input.CurrentSpeedKph - 0.0001)
        return HeartRateSpeedDecision.None("The minimum permitted speed has been reached.");
      _lastDecreaseAt = input.Now;
      _aboveSince = input.Now;
      return new HeartRateSpeedDecision(
        HeartRateSpeedDecisionKind.Decrease,
        target,
        input.Mode is HeartRateAutomationMode.Full or HeartRateAutomationMode.DecreaseOnly,
        input.Mode == HeartRateAutomationMode.Shadow
          ? "Shadow-mode decrease recorded without a write."
          : "Above-target dwell and decrease cooldown passed.");
    }

    ResetDwell();
    return HeartRateSpeedDecision.None("Heart rate is within target.");
  }

  public void Reset()
  {
    ResetDwell();
    _lastIncreaseAt = null;
    _lastDecreaseAt = null;
  }

  public void ResetDwell()
  {
    _belowSince = null;
    _aboveSince = null;
  }

  private static double AlignWithoutAggression(
    double requested,
    double current,
    double minimum,
    double maximum,
    double increment)
  {
    decimal bounded = Math.Clamp((decimal)requested, (decimal)minimum, (decimal)maximum);
    decimal rawSteps = (bounded - (decimal)minimum) / (decimal)increment;
    decimal steps = requested >= current ? decimal.Floor(rawSteps) : decimal.Ceiling(rawSteps);
    return (double)((decimal)minimum + (steps * (decimal)increment));
  }

  private static void Validate(HeartRateSpeedControllerInput input)
  {
    if (!double.IsFinite(input.CurrentSpeedKph) ||
        !double.IsFinite(input.MinimumSpeedKph) ||
        !double.IsFinite(input.MaximumSpeedKph) ||
        !double.IsFinite(input.SpeedIncrementKph) ||
        input.MinimumSpeedKph < 0 ||
        input.MaximumSpeedKph < input.MinimumSpeedKph ||
        input.SpeedIncrementKph <= 0)
      throw new ArgumentOutOfRangeException(nameof(input));
    if (input.TargetMinimumBpm > input.TargetMaximumBpm)
      throw new ArgumentOutOfRangeException(nameof(input));
  }

}
