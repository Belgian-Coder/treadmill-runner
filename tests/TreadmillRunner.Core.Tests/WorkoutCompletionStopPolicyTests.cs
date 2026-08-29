using TreadmillRunner.Core.Control;

namespace TreadmillRunner.Core.Tests;

public sealed class WorkoutCompletionStopPolicyTests
{
  [Fact]
  public void Moving_hardware_requests_one_verified_stop_before_completion()
  {
    WorkoutCompletionStopContext context = HardwareContext();

    Assert.Equal(WorkoutCompletionAction.RequestStop, WorkoutCompletionStopPolicy.Evaluate(context));
    Assert.Equal(
      WorkoutCompletionAction.AwaitPhysicalStop,
      WorkoutCompletionStopPolicy.Evaluate(context with { StopAttempted = true }));
  }

  [Theory]
  [InlineData(false, true, true, true)]
  [InlineData(true, false, true, true)]
  [InlineData(true, true, false, true)]
  [InlineData(true, true, true, false)]
  public void Unsafe_or_unverified_hardware_never_finalizes_or_sends_stop(
    bool telemetryFresh,
    bool canStop,
    bool treadmillReady,
    bool generationCurrent)
  {
    WorkoutCompletionStopContext context = HardwareContext() with
    {
      TelemetryFresh = telemetryFresh,
      CanStopRemotely = canStop,
      TreadmillReady = treadmillReady,
      ConnectionGenerationCurrent = generationCurrent,
    };

    Assert.Equal(WorkoutCompletionAction.AwaitPhysicalStop, WorkoutCompletionStopPolicy.Evaluate(context));
  }

  [Fact]
  public void Fresh_stopped_telemetry_allows_hardware_completion_after_any_stop_outcome()
  {
    WorkoutCompletionStopContext context = HardwareContext() with
    {
      IsMoving = false,
      MeasuredSpeedKph = 0,
      StopAttempted = true,
    };

    Assert.Equal(WorkoutCompletionAction.Finalize, WorkoutCompletionStopPolicy.Evaluate(context));
  }

  [Theory]
  [InlineData(double.NegativeInfinity)]
  [InlineData(-0.1)]
  public void Invalid_speed_is_not_accepted_as_physical_stop_evidence(double measuredSpeedKph)
  {
    WorkoutCompletionStopContext context = HardwareContext() with
    {
      IsMoving = false,
      MeasuredSpeedKph = measuredSpeedKph,
      StopAttempted = true,
    };

    Assert.Equal(
      WorkoutCompletionAction.AwaitPhysicalStop,
      WorkoutCompletionStopPolicy.Evaluate(context));
  }

  [Fact]
  public void Non_finite_moving_speed_does_not_trigger_a_stop_request()
  {
    WorkoutCompletionStopContext context = HardwareContext() with
    {
      MeasuredSpeedKph = double.PositiveInfinity,
    };

    Assert.Equal(
      WorkoutCompletionAction.AwaitPhysicalStop,
      WorkoutCompletionStopPolicy.Evaluate(context));
  }

  [Fact]
  public void Simulator_completion_remains_immediate()
  {
    WorkoutCompletionStopContext context = HardwareContext() with
    {
      HardwareMode = false,
      TelemetryFresh = false,
      CanStopRemotely = false,
      TreadmillReady = false,
    };

    Assert.Equal(WorkoutCompletionAction.Finalize, WorkoutCompletionStopPolicy.Evaluate(context));
  }

  private static WorkoutCompletionStopContext HardwareContext() => new(
    ProgressionComplete: true,
    HardwareMode: true,
    TelemetryFresh: true,
    IsMoving: true,
    MeasuredSpeedKph: 4.5,
    CanStopRemotely: true,
    TreadmillReady: true,
    ConnectionGenerationCurrent: true,
    StopAttempted: false);
}
