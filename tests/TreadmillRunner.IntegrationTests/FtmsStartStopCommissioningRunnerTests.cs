using TreadmillRunner.Core.Control;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class FtmsStartStopCommissioningRunnerTests
{
  [Fact]
  public async Task Confirmed_start_waits_exactly_three_seconds_then_sends_stop_in_same_run()
  {
    Guid startId = Guid.NewGuid();
    Guid stopId = Guid.NewGuid();
    var stages = new FakeStages(
      Result(startId, TreadmillCommandKind.Start, TreadmillCommandDisposition.Confirmed, promoted: true, measured: 0.8),
      Result(stopId, TreadmillCommandKind.Stop, TreadmillCommandDisposition.Confirmed, promoted: true, measured: 0));
    var delay = new RecordingDelay();
    var runner = new FtmsStartStopCommissioningRunner(stages, delay);

    FtmsStartStopCommissioningOutcome outcome = await runner.RunAsync(
      new FtmsStartStopCommissioningRequest(startId, stopId, "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None);

    Assert.Equal(TimeSpan.FromSeconds(3), outcome.RequestedMovingDuration);
    Assert.Equal([TimeSpan.FromSeconds(3)], delay.Durations);
    Assert.Equal([startId, stopId], stages.Requests.Select(static request => request.OperationId));
    Assert.Equal([TreadmillCommandKind.Start, TreadmillCommandKind.Stop], stages.Requests.Select(static request => request.Kind));
    Assert.NotNull(outcome.Stop);
    Assert.Equal(TreadmillCommandDisposition.Confirmed, outcome.Stop.Disposition);
  }

  [Theory]
  [InlineData(TreadmillCommandDisposition.Rejected)]
  [InlineData(TreadmillCommandDisposition.Unknown)]
  public async Task Unconfirmed_start_never_delays_or_sends_stop(TreadmillCommandDisposition disposition)
  {
    Guid startId = Guid.NewGuid();
    var stages = new FakeStages(
      Result(startId, TreadmillCommandKind.Start, disposition, promoted: false, measured: 0));
    var delay = new RecordingDelay();
    var runner = new FtmsStartStopCommissioningRunner(stages, delay);

    FtmsStartStopCommissioningOutcome outcome = await runner.RunAsync(
      new FtmsStartStopCommissioningRequest(startId, Guid.NewGuid(), "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None);

    Assert.Null(outcome.Stop);
    Assert.Empty(delay.Durations);
    Assert.Single(stages.Requests);
  }

  [Fact]
  public async Task Start_and_stop_require_distinct_operation_ids_before_any_stage_runs()
  {
    Guid operationId = Guid.NewGuid();
    var stages = new FakeStages();
    var runner = new FtmsStartStopCommissioningRunner(stages, new RecordingDelay());

    await Assert.ThrowsAsync<ArgumentException>(() => runner.RunAsync(
      new FtmsStartStopCommissioningRequest(operationId, operationId, "OMEGA Z", "V10.23.17", "owner"),
      CancellationToken.None));

    Assert.Empty(stages.Requests);
  }

  private static FtmsCommandCommissioningOutcome Result(
    Guid operationId,
    TreadmillCommandKind kind,
    TreadmillCommandDisposition disposition,
    bool promoted,
    double measured) => new(
      operationId,
      kind,
      disposition,
      promoted,
      disposition.ToString(),
      kind == TreadmillCommandKind.Start ? 0.8 : null,
      kind == TreadmillCommandKind.Start ? 0.8 : null,
      measured,
      ConnectionGeneration: 1,
      DateTimeOffset.UtcNow,
      DateTimeOffset.UtcNow);

  private sealed class FakeStages(params FtmsCommandCommissioningOutcome[] outcomes)
    : IFtmsCommandCommissioningRunner
  {
    private readonly Queue<FtmsCommandCommissioningOutcome> _outcomes = new(outcomes);
    public List<FtmsCommandCommissioningRequest> Requests { get; } = [];

    public Task<FtmsCommandCommissioningOutcome> RunAsync(
      FtmsCommandCommissioningRequest request,
      CancellationToken cancellationToken)
    {
      Requests.Add(request);
      return Task.FromResult(_outcomes.Dequeue());
    }
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
}
