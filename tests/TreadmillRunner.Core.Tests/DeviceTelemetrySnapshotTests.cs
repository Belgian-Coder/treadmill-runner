using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Tests;

public sealed class DeviceTelemetrySnapshotTests
{
  [Fact]
  public void Speed_and_incline_ages_remain_independent_when_a_field_is_omitted()
  {
    DateTimeOffset captured = new(2026, 8, 23, 12, 0, 10, TimeSpan.Zero);
    DateTimeOffset speedObserved = captured.AddSeconds(-2);
    DeviceTelemetrySnapshot snapshot = new(
      captured,
      Connection(DeviceRole.Treadmill),
      Connection(DeviceRole.HeartRate),
      new TreadmillTelemetry(
        captured.AddSeconds(-1),
        8,
        1,
        speedObserved,
        InclineObservedAt: null),
      null,
      null,
      null);

    Assert.Equal(TimeSpan.FromSeconds(2), snapshot.TreadmillSpeedAge!.Value);
    Assert.Null(snapshot.TreadmillInclineAge);
  }

  [Fact]
  public void Future_observation_timestamps_are_not_reported_as_fresh_telemetry()
  {
    DateTimeOffset captured = new(2026, 8, 23, 12, 0, 10, TimeSpan.Zero);
    DateTimeOffset future = captured.AddSeconds(1);
    DeviceTelemetrySnapshot snapshot = new(
      captured,
      Connection(DeviceRole.Treadmill),
      Connection(DeviceRole.HeartRate),
      new TreadmillTelemetry(
        future,
        8,
        1,
        SpeedObservedAt: future,
        InclineObservedAt: future),
      140,
      future,
      null);

    Assert.Null(snapshot.TreadmillAge);
    Assert.Null(snapshot.TreadmillSpeedAge);
    Assert.Null(snapshot.TreadmillInclineAge);
    Assert.Null(snapshot.HeartRateAge);
  }

  private static DeviceConnectionSnapshot Connection(DeviceRole role) => new(
    role,
    DeviceConnectionState.Ready,
    1,
    null,
    null,
    null,
    null,
    null);
}
