namespace TreadmillRunner.Core.Sessions;

public sealed record SessionSampleStatistics(
  double? AverageHeartRateBpm,
  ushort? MinimumHeartRateBpm,
  ushort? MaximumHeartRateBpm,
  TimeSpan? MovingTime,
  double? MaximumSpeedKph,
  double? AverageInclinePercent,
  double? MinimumInclinePercent,
  double? MaximumInclinePercent);

public static class SessionSampleStatisticsCalculator
{
  public static SessionSampleStatistics Calculate(IReadOnlyList<SessionSample> samples)
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

    return new SessionSampleStatistics(
      averageHeartRate,
      heartRates.Length == 0 ? null : heartRates.Min(),
      heartRates.Length == 0 ? null : heartRates.Max(),
      movingTime,
      samples.Count == 0 ? null : samples.Max(static sample => sample.MeasuredSpeedKph),
      averageIncline,
      inclines.Length == 0 ? null : inclines.Min(),
      inclines.Length == 0 ? null : inclines.Max());
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
