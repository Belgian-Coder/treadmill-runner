namespace TreadmillRunner.Core.Sessions;

public sealed record SessionSampleStatistics(
  double? AverageHeartRateBpm,
  ushort? MinimumHeartRateBpm,
  ushort? MaximumHeartRateBpm,
  TimeSpan? MovingTime,
  double? MaximumSpeedKph,
  double? AverageInclinePercent,
  double? AveragePositiveInclinePercent,
  double? AverageNegativeInclinePercent,
  double? MinimumInclinePercent,
  double? MaximumInclinePercent,
  double? AveragePositiveVerticalSpeedMetersPerSecond,
  double? AverageNegativeVerticalSpeedMetersPerSecond,
  double? MaximumPositiveVerticalSpeedMetersPerSecond,
  double? MaximumNegativeVerticalSpeedMetersPerSecond,
  double TotalAscentMeters,
  double TotalDescentMeters,
  double NetElevationMeters,
  double? EstimatedKilocalories);

public static class SessionSampleStatisticsCalculator
{
  public static SessionSampleStatistics Calculate(IReadOnlyList<SessionSample> samples, double? weightKilograms = null)
  {
    ArgumentNullException.ThrowIfNull(samples);
    ValidateOrder(samples);

    ushort[] heartRates = samples
      .Where(static sample => sample.HeartRateBpm.HasValue)
      .Select(static sample => sample.HeartRateBpm!.Value)
      .ToArray();
    double? averageHeartRate = WeightedAverage(
      samples,
      static sample => sample.HeartRateBpm is { } value ? value : null,
      heartRates.Length == 0 ? null : heartRates.Average(static value => (double)value));

    double[] inclines = samples.Select(static sample => sample.MeasuredInclinePercent).ToArray();
    double? averageIncline = WeightedAverage(
      samples,
      static sample => sample.MeasuredInclinePercent,
      inclines.Length == 0 ? null : inclines.Average());

    double positiveGradeDistance = 0;
    double positiveDistance = 0;
    double negativeGradeDistance = 0;
    double negativeDistance = 0;
    double positiveVerticalDistance = 0;
    double positiveVerticalSeconds = 0;
    double negativeVerticalDistance = 0;
    double negativeVerticalSeconds = 0;
    double? maximumPositiveVerticalSpeed = null;
    double? maximumNegativeVerticalSpeed = null;
    for (var index = 1; index < samples.Count; index++)
    {
      SessionSample previous = samples[index - 1];
      SessionSample current = samples[index];
      double distanceMeters = Math.Max(0, (current.DistanceKilometers - previous.DistanceKilometers) * 1000);
      double durationSeconds = (current.Elapsed - previous.Elapsed).TotalSeconds;
      if (distanceMeters <= 0 || durationSeconds <= 0)
      {
        continue;
      }

      double grade = current.MeasuredInclinePercent;
      double gradeFraction = grade / 100d;
      double verticalMeters = distanceMeters * gradeFraction / Math.Sqrt(1 + (gradeFraction * gradeFraction));
      double verticalSpeed = verticalMeters / durationSeconds;
      if (grade > 0)
      {
        positiveGradeDistance += grade * distanceMeters;
        positiveDistance += distanceMeters;
        positiveVerticalDistance += verticalMeters;
        positiveVerticalSeconds += durationSeconds;
        maximumPositiveVerticalSpeed = Math.Max(maximumPositiveVerticalSpeed ?? verticalSpeed, verticalSpeed);
      }
      else if (grade < 0)
      {
        negativeGradeDistance += grade * distanceMeters;
        negativeDistance += distanceMeters;
        negativeVerticalDistance += verticalMeters;
        negativeVerticalSeconds += durationSeconds;
        maximumNegativeVerticalSpeed = Math.Min(maximumNegativeVerticalSpeed ?? verticalSpeed, verticalSpeed);
      }
    }

    TimeSpan? movingTime = null;
    if (samples.Count > 1)
    {
      long movingTicks = 0;
      for (var index = 1; index < samples.Count; index++)
      {
        if (samples[index].MeasuredSpeedKph > SessionStateMachine.PhysicalStartThresholdKph)
        {
          movingTicks = checked(movingTicks + (samples[index].Elapsed - samples[index - 1].Elapsed).Ticks);
        }
      }

      movingTime = TimeSpan.FromTicks(movingTicks);
    }

    SessionElevationStatistics elevation = SessionElevationCalculator.Calculate(samples);
    return new SessionSampleStatistics(
      averageHeartRate,
      heartRates.Length == 0 ? null : heartRates.Min(),
      heartRates.Length == 0 ? null : heartRates.Max(),
      movingTime,
      samples.Count == 0 ? null : samples.Max(static sample => sample.MeasuredSpeedKph),
      averageIncline,
      positiveDistance > 0 ? positiveGradeDistance / positiveDistance : null,
      negativeDistance > 0 ? negativeGradeDistance / negativeDistance : null,
      inclines.Length == 0 ? null : inclines.Min(),
      inclines.Length == 0 ? null : inclines.Max(),
      positiveVerticalSeconds > 0 ? positiveVerticalDistance / positiveVerticalSeconds : null,
      negativeVerticalSeconds > 0 ? negativeVerticalDistance / negativeVerticalSeconds : null,
      maximumPositiveVerticalSpeed,
      maximumNegativeVerticalSpeed,
      elevation.TotalAscentMeters,
      elevation.TotalDescentMeters,
      elevation.NetElevationMeters,
      weightKilograms is { } weight
        ? SessionCalorieCalculator.Calculate(samples, weight)
        : samples.LastOrDefault()?.EstimatedKilocalories);
  }

  private static double? WeightedAverage(
    IReadOnlyList<SessionSample> samples,
    Func<SessionSample, double?> valueSelector,
    double? fallback)
  {
    double weightedTotal = 0;
    long totalTicks = 0;
    for (var index = 1; index < samples.Count; index++)
    {
      if (valueSelector(samples[index]) is not { } value)
      {
        continue;
      }

      long ticks = (samples[index].Elapsed - samples[index - 1].Elapsed).Ticks;
      if (ticks == 0)
      {
        continue;
      }

      weightedTotal += value * ticks;
      totalTicks = checked(totalTicks + ticks);
    }

    return totalTicks == 0 ? fallback : weightedTotal / totalTicks;
  }

  private static void ValidateOrder(IReadOnlyList<SessionSample> samples)
  {
    for (var index = 1; index < samples.Count; index++)
    {
      if (samples[index].Sequence <= samples[index - 1].Sequence ||
          samples[index].CapturedAt < samples[index - 1].CapturedAt ||
          samples[index].Elapsed < samples[index - 1].Elapsed)
      {
        throw new ArgumentException(
          "Samples must have increasing sequence, capture time, and elapsed time.",
          nameof(samples));
      }
    }
  }
}
