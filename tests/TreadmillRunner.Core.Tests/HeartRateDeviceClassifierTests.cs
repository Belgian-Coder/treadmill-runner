using TreadmillRunner.Core.Devices;

namespace TreadmillRunner.Core.Tests;

public sealed class HeartRateDeviceClassifierTests
{
  [Theory]
  [InlineData("Polar H10", HeartRateDeviceKind.ChestStrap)]
  [InlineData("Garmin Forerunner 965", HeartRateDeviceKind.Watch)]
  [InlineData("Apple Watch", HeartRateDeviceKind.Watch)]
  [InlineData("Generic HRM", HeartRateDeviceKind.Sensor)]
  public void Classifies_named_heart_rate_devices(string name, HeartRateDeviceKind expected) =>
    Assert.Equal(expected, HeartRateDeviceClassifier.Classify(name));

  [Fact]
  public void Polar_service_classifies_an_unnamed_advertisement_as_preferred_chest_strap()
  {
    Guid[] services =
    [
      HeartRateDeviceClassifier.HeartRateService,
      HeartRateDeviceClassifier.PolarService,
    ];

    Assert.Equal(HeartRateDeviceKind.ChestStrap, HeartRateDeviceClassifier.Classify(null, services));
    Assert.True(HeartRateDeviceClassifier.IsPreferredPolar(null, services));
    Assert.Equal(0, HeartRateDeviceClassifier.Priority(null, services));
  }

  [Fact]
  public void Priority_orders_polar_then_other_strap_then_watch_then_generic()
  {
    Assert.Equal(0, HeartRateDeviceClassifier.Priority("Polar H10"));
    Assert.Equal(1, HeartRateDeviceClassifier.Priority("Chest belt"));
    Assert.Equal(2, HeartRateDeviceClassifier.Priority("Running Watch"));
    Assert.Equal(3, HeartRateDeviceClassifier.Priority("HRM"));
  }
}
