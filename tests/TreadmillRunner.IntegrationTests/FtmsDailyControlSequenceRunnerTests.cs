using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class FtmsDailyControlSequenceRunnerTests
{
  [Fact]
  public async Task Sends_the_exact_confirmed_sequence_then_observes_speed_and_incline_at_zero()
  {
    var stages = new RecordingStages();
    var delay = new RecordingDelay();
    var runner = new FtmsDailyControlSequenceRunner(
      TimeProvider.System,
      stages,
      delay,
      new FixedDevices(ReadySnapshot(speed: 0, incline: 0)));

    FtmsDailyControlSequenceOutcome outcome = await runner.RunAsync(
      new FtmsDailyControlSequenceRequest(Guid.NewGuid(), "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None);

    Assert.Equal(
      new[]
      {
        (TreadmillCommandKind.Start, (double?)null),
        (TreadmillCommandKind.SetSpeed, 1.2),
        (TreadmillCommandKind.SetSpeed, 1.5),
        (TreadmillCommandKind.SetIncline, 1.0),
        (TreadmillCommandKind.SetSpeed, 1.0),
        (TreadmillCommandKind.SetIncline, 0.5),
        (TreadmillCommandKind.Stop, (double?)null),
      },
      stages.Requests.Select(static request => (request.Kind, request.RequestedValue)));
    Assert.Equal(7, stages.Requests.Select(static request => request.OperationId).Distinct().Count());
    Assert.True(outcome.SafetyStopSent);
    Assert.True(outcome.SpeedAndInclineReturnedToZero);
    Assert.Equal(0, outcome.FinalSpeedKph);
    Assert.Equal(0, outcome.FinalInclinePercent);
    Assert.Equal(6, delay.Durations.Count(static duration => duration == TimeSpan.FromSeconds(2)));
  }

  [Fact]
  public async Task Failure_after_start_skips_remaining_targets_but_still_sends_the_reserved_safety_stop()
  {
    var stages = new RecordingStages(failAtRequest: 2);
    var runner = new FtmsDailyControlSequenceRunner(
      TimeProvider.System,
      stages,
      new ImmediateDelay(),
      new FixedDevices(ReadySnapshot(speed: 0, incline: 0)));

    FtmsDailyControlSequenceOutcome outcome = await runner.RunAsync(
      new FtmsDailyControlSequenceRequest(Guid.NewGuid(), "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None);

    Assert.Equal(
      new[] { TreadmillCommandKind.Start, TreadmillCommandKind.SetSpeed, TreadmillCommandKind.SetSpeed, TreadmillCommandKind.Stop },
      stages.Requests.Select(static request => request.Kind));
    Assert.True(outcome.SafetyStopSent);
    Assert.Equal(TreadmillCommandDisposition.Rejected, outcome.Steps[2].Outcome.Disposition);
    Assert.Equal(TreadmillCommandDisposition.Confirmed, outcome.Steps[^1].Outcome.Disposition);
  }

  [Fact]
  public async Task Rejected_start_sends_no_later_target_or_stop()
  {
    var stages = new RecordingStages(failAtRequest: 0);
    var runner = new FtmsDailyControlSequenceRunner(
      TimeProvider.System,
      stages,
      new ImmediateDelay(),
      new FixedDevices(ReadySnapshot(speed: 0, incline: 0)));

    FtmsDailyControlSequenceOutcome outcome = await runner.RunAsync(
      new FtmsDailyControlSequenceRequest(Guid.NewGuid(), "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None);

    Assert.Single(stages.Requests);
    Assert.False(outcome.SafetyStopSent);
  }

  private sealed class RecordingStages(int failAtRequest = -1) : IFtmsCommandCommissioningRunner
  {
    public List<FtmsCommandCommissioningRequest> Requests { get; } = [];

    public Task<FtmsCommandCommissioningOutcome> RunAsync(
      FtmsCommandCommissioningRequest request,
      CancellationToken cancellationToken)
    {
      int index = Requests.Count;
      Requests.Add(request);
      bool rejected = index == failAtRequest && request.Kind != TreadmillCommandKind.Stop;
      DateTimeOffset now = DateTimeOffset.UtcNow;
      double measured = request.Kind == TreadmillCommandKind.SetIncline
        ? request.RequestedValue ?? 0
        : request.Kind == TreadmillCommandKind.Stop
          ? 0
          : request.RequestedValue ?? 0.8;
      return Task.FromResult(new FtmsCommandCommissioningOutcome(
        request.OperationId,
        request.Kind,
        rejected ? TreadmillCommandDisposition.Rejected : TreadmillCommandDisposition.Confirmed,
        CapabilityPromoted: !rejected,
        rejected ? "Rejected" : "Confirmed",
        request.RequestedValue,
        request.RequestedValue,
        measured,
        ConnectionGeneration: 1,
        now,
        now));
    }
  }

  private sealed class ImmediateDelay : ICommissioningDelay
  {
    public Task DelayAsync(TimeSpan duration, CancellationToken cancellationToken) => Task.CompletedTask;
  }

  private sealed class RecordingDelay : ICommissioningDelay
  {
    public List<TimeSpan> Durations { get; } = [];

    public Task DelayAsync(TimeSpan duration, CancellationToken cancellationToken)
    {
      Durations.Add(duration);
      return Task.CompletedTask;
    }
  }

  private sealed class FixedDevices(DeviceTelemetrySnapshot snapshot) : IReadOnlyDeviceCoordinator
  {
    public DeviceTelemetrySnapshot Current => snapshot with { CapturedAt = DateTimeOffset.UtcNow };
  }

  private static DeviceTelemetrySnapshot ReadySnapshot(double speed, double incline)
  {
    DateTimeOffset observed = DateTimeOffset.UtcNow;
    return new DeviceTelemetrySnapshot(
      observed,
      new DeviceConnectionSnapshot(
        DeviceRole.Treadmill,
        DeviceConnectionState.Ready,
        1,
        "Horizon Omega Z",
        "horizon-omega-z",
        "Ftms",
        observed,
        null),
      new DeviceConnectionSnapshot(DeviceRole.HeartRate, DeviceConnectionState.Disconnected, 0, null, null, null, null, null),
      new TreadmillTelemetry(observed, speed, incline, observed, observed),
      null,
      null,
      null);
  }
}
