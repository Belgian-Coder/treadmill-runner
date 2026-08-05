using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class TreadmillCommandContractsTests
{
  [Theory]
  [InlineData(TreadmillCommandKind.SetSpeed, 1.0)]
  [InlineData(TreadmillCommandKind.SetIncline, 2.5)]
  public void Target_commands_require_and_preserve_a_finite_value(
    TreadmillCommandKind kind,
    double requestedValue)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    var intent = new TreadmillCommandIntent(
      Guid.NewGuid(), Guid.NewGuid(), kind, now, now.AddSeconds(4), 1,
      SessionState.Running, Guid.NewGuid(), "controller", 2, requestedValue);

    Assert.Equal(requestedValue, intent.RequestedValue);
  }

  [Theory]
  [InlineData(TreadmillCommandKind.Stop)]
  [InlineData(TreadmillCommandKind.Pause)]
  public void Targetless_commands_reject_a_requested_value(TreadmillCommandKind kind)
  {
    DateTimeOffset now = DateTimeOffset.UtcNow;
    Assert.Throws<ArgumentException>(() => new TreadmillCommandIntent(
      Guid.NewGuid(), Guid.NewGuid(), kind, now, now.AddSeconds(4), 1,
      SessionState.Running, Guid.NewGuid(), "controller", 2, 1.0));
  }

  private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-08-03T20:00:00Z");

  [Fact]
  public void Start_intent_captures_every_execution_guard()
  {
    var operationId = Guid.NewGuid();
    var leaseId = Guid.NewGuid();
    var intent = new TreadmillCommandIntent(
      operationId,
      Guid.NewGuid(),
      TreadmillCommandKind.Start,
      Now,
      Now.AddSeconds(3),
      1,
      SessionState.ArmedWaitingForPhysicalStart,
      leaseId,
      "browser-a",
      42,
      0.8);

    Assert.Equal(operationId, intent.OperationId);
    Assert.Equal(leaseId, intent.LeaseId);
    Assert.Equal(42, intent.ConnectionGeneration);
    Assert.Equal(0.8, intent.RequestedValue);
  }

  [Theory]
  [InlineData(0)]
  [InlineData(5.001)]
  public void Rejects_non_short_lived_intents(double lifetimeSeconds)
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Start,
      Now,
      Now.AddSeconds(lifetimeSeconds),
      1,
      SessionState.ArmedWaitingForPhysicalStart,
      Guid.NewGuid(),
      "browser-a",
      1,
      0.8));
  }

  [Fact]
  public void Start_requires_a_finite_non_negative_expected_minimum_speed()
  {
    Assert.Throws<ArgumentOutOfRangeException>(() => new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Start,
      Now,
      Now.AddSeconds(3),
      1,
      SessionState.ArmedWaitingForPhysicalStart,
      Guid.NewGuid(),
      "browser-a",
      1,
      double.NaN));
  }

  [Fact]
  public void Stop_does_not_accept_a_requested_value()
  {
    Assert.Throws<ArgumentException>(() => new TreadmillCommandIntent(
      Guid.NewGuid(),
      Guid.NewGuid(),
      TreadmillCommandKind.Stop,
      Now,
      Now.AddSeconds(3),
      2,
      SessionState.Running,
      Guid.NewGuid(),
      "browser-a",
      1,
      0));
  }

  [Fact]
  public void Result_preserves_unknown_physical_outcome()
  {
    var result = new TreadmillCommandResult(
      Guid.NewGuid(),
      TreadmillCommandKind.Start,
      TreadmillCommandDisposition.Unknown,
      0.8,
      0.8,
      0,
      "Telemetry confirmation timed out.",
      9,
      Now,
      Now.AddSeconds(4));

    Assert.Equal(TreadmillCommandDisposition.Unknown, result.Disposition);
    Assert.Equal(0, result.MeasuredValue);
    Assert.Equal(9, result.ConnectionGeneration);
  }
}
