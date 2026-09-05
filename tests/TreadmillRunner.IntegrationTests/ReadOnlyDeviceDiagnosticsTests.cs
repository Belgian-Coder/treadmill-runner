using TreadmillRunner.Core.Bluetooth;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Gateway.Devices;

namespace TreadmillRunner.IntegrationTests;

public sealed class ReadOnlyDeviceDiagnosticsTests
{
  [Fact]
  public void Contact_lost_and_invalid_notifications_are_counted_before_any_valid_sample()
  {
    var attempt = new ReadOnlyDeviceCoordinator.ConnectionAttemptRuntime();
    DateTimeOffset firstAt = new(2099, 9, 5, 12, 0, 0, TimeSpan.Zero);

    bool firstNotification = attempt.ObserveHeartRateNotification(firstAt);
    attempt.ObserveHeartRateQuality(HeartRateSignalQuality.ContactLost);
    bool firstSummary = attempt.ShouldRecordHeartRateSummary(firstAt);
    bool secondNotification = attempt.ObserveHeartRateNotification(firstAt.AddSeconds(2));
    attempt.ObserveHeartRateQuality(HeartRateSignalQuality.Invalid);

    Assert.True(firstNotification);
    Assert.True(firstSummary);
    Assert.False(secondNotification);
    Assert.Equal(2L, attempt.Notifications);
    Assert.Equal(1L, attempt.ContactLostSamples);
    Assert.Equal(1L, attempt.InvalidValueSamples);
    Assert.Equal(0, attempt.TelemetrySampleCount);
    Assert.Equal(2d, attempt.MaximumNotificationIntervalSeconds);
    Assert.Equal(2d, attempt.MaximumNotificationIntervalForDiagnostics!.Value);
    Assert.Equal("notification-stream", attempt.OperationStage);
    Assert.False(attempt.ShouldRecordHeartRateSummary(firstAt.AddSeconds(59)));
    Assert.True(attempt.ShouldRecordHeartRateSummary(firstAt.AddMinutes(1)));
  }

  [Theory]
  [InlineData(true, "subscription-awaiting-first-notification")]
  [InlineData(false, "notification-stream")]
  public void Telemetry_silence_reports_initial_or_established_subscription_stage(
    bool initial,
    string expectedStage)
  {
    var exception = new ReadOnlyDeviceCoordinator.BleTelemetrySilenceException(
      initial,
      new TimeoutException());

    Assert.Equal(initial, exception.Initial);
    Assert.Equal(
      expectedStage,
      ReadOnlyDeviceCoordinator.OperationStageForFailure(exception, "other-stage"));
  }

  [Fact]
  public void Missing_required_measurement_remains_in_characteristic_validation_stage()
  {
    var attempt = new ReadOnlyDeviceCoordinator.ConnectionAttemptRuntime();

    Assert.Throws<InvalidDataException>(() =>
      ReadOnlyDeviceCoordinator.RequireHeartRateMeasurement(
        Array.Empty<BleService>(),
        attempt));

    Assert.Equal("required-characteristic-validation", attempt.OperationStage);
  }
}
