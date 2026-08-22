using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Tests;

public sealed class SessionCalorieCalculatorTests
{
  [Fact]
  public void Measured_incline_increases_the_interval_estimate()
  {
    double level = SessionCalorieCalculator.CalculateInterval(
      weightKilograms: 70,
      speedKilometersPerHour: 6,
      inclinePercent: 0,
      duration: TimeSpan.FromHours(1));
    double incline = SessionCalorieCalculator.CalculateInterval(
      weightKilograms: 70,
      speedKilometersPerHour: 6,
      inclinePercent: 10,
      duration: TimeSpan.FromHours(1));

    Assert.InRange(level, 283.49, 283.51);
    Assert.InRange(incline, 661.49, 661.51);
    Assert.True(incline > level);
  }

  [Fact]
  public void Stopped_intervals_do_not_add_workout_calories()
  {
    Assert.Equal(0, SessionCalorieCalculator.CalculateInterval(
      weightKilograms: 70,
      speedKilometersPerHour: 0,
      inclinePercent: 10,
      duration: TimeSpan.FromMinutes(30)));
  }
}
