using System.Text.Json;

namespace TreadmillRunner.Core.Sessions;

public static class SessionCalorieCalculator
{
  public static double Calculate(
    IReadOnlyList<SessionSample> samples,
    double weightKilograms)
  {
    IReadOnlyList<double> cumulative = CalculateCumulative(samples, weightKilograms);
    return cumulative.Count == 0 ? 0 : cumulative[^1];
  }

  public static IReadOnlyList<double> CalculateCumulative(
    IReadOnlyList<SessionSample> samples,
    double weightKilograms)
  {
    ArgumentNullException.ThrowIfNull(samples);
    ValidateWeight(weightKilograms);
    var cumulative = new double[samples.Count];
    for (var index = 1; index < samples.Count; index++)
    {
      TimeSpan duration = samples[index].Elapsed - samples[index - 1].Elapsed;
      if (duration < TimeSpan.Zero)
        throw new ArgumentException("Samples must have increasing elapsed time.", nameof(samples));
      cumulative[index] = cumulative[index - 1] + CalculateInterval(
        weightKilograms,
        samples[index].MeasuredSpeedKph,
        samples[index].MeasuredInclinePercent,
        duration);
    }
    return Array.AsReadOnly(cumulative);
  }

  public static double CalculateInterval(
    double weightKilograms,
    double speedKilometersPerHour,
    double inclinePercent,
    TimeSpan duration)
  {
    ValidateWeight(weightKilograms);
    if (!double.IsFinite(speedKilometersPerHour) || speedKilometersPerHour < 0)
      throw new ArgumentOutOfRangeException(nameof(speedKilometersPerHour));
    if (!double.IsFinite(inclinePercent))
      throw new ArgumentOutOfRangeException(nameof(inclinePercent));
    if (duration < TimeSpan.Zero)
      throw new ArgumentOutOfRangeException(nameof(duration));
    if (speedKilometersPerHour <= SessionStateMachine.PhysicalStartThresholdKph || duration == TimeSpan.Zero)
      return 0;

    double speedMetersPerMinute = speedKilometersPerHour * 1000d / 60d;
    double grade = inclinePercent / 100d;
    double walkingVo2 = 3.5 + (0.1 * speedMetersPerMinute) + (1.8 * speedMetersPerMinute * grade);
    double runningVo2 = 3.5 + (0.2 * speedMetersPerMinute) + (0.9 * speedMetersPerMinute * grade);
    double vo2 = speedKilometersPerHour switch
    {
      <= 6 => walkingVo2,
      >= 8 => runningVo2,
      _ => walkingVo2 + ((runningVo2 - walkingVo2) * ((speedKilometersPerHour - 6) / 2)),
    };

    // Clamp decline estimates at resting oxygen consumption; this remains an
    // estimate and never reports negative energy expenditure.
    vo2 = Math.Max(3.5, vo2);
    double kilocaloriesPerMinute = vo2 * weightKilograms / 1000d * 5d;
    return kilocaloriesPerMinute * duration.TotalMinutes;
  }

  public static double? ReadWeightKilograms(string configurationJson)
  {
    try
    {
      SessionExecutionConfiguration? configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
        configurationJson,
        new JsonSerializerOptions(JsonSerializerDefaults.Web));
      double weight = configuration?.Profile is { } profile ? profile.WeightKilograms : 0;
      return double.IsFinite(weight) && weight > 0 ? weight : null;
    }
    catch (JsonException)
    {
      return null;
    }
  }

  private static void ValidateWeight(double weightKilograms)
  {
    if (!double.IsFinite(weightKilograms) || weightKilograms <= 0)
      throw new ArgumentOutOfRangeException(nameof(weightKilograms));
  }
}
