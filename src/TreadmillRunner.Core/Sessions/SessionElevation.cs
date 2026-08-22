namespace TreadmillRunner.Core.Sessions;

public sealed record SessionElevationPoint(
  long Sequence,
  TimeSpan Elapsed,
  double ElevationMeters);

public sealed record SessionElevationStatistics(
  double TotalAscentMeters,
  double TotalDescentMeters,
  double NetElevationMeters,
  IReadOnlyList<SessionElevationPoint> Points);

public static class SessionElevationCalculator
{
  public static SessionElevationStatistics Calculate(IReadOnlyList<SessionSample> samples)
  {
    ArgumentNullException.ThrowIfNull(samples);
    if (samples.Count == 0)
    {
      return new SessionElevationStatistics(0, 0, 0, []);
    }

    var points = new SessionElevationPoint[samples.Count];
    double ascent = 0;
    double descent = 0;
    double elevation = 0;
    points[0] = new SessionElevationPoint(samples[0].Sequence, samples[0].Elapsed, 0);

    for (var index = 1; index < samples.Count; index++)
    {
      SessionSample previous = samples[index - 1];
      SessionSample current = samples[index];
      if (current.Sequence <= previous.Sequence || current.Elapsed < previous.Elapsed)
      {
        throw new ArgumentException("Samples must have increasing sequence and elapsed time.", nameof(samples));
      }

      double distanceMeters = (current.DistanceKilometers - previous.DistanceKilometers) * 1000;
      if (distanceMeters > 0)
      {
        // Treadmill distance follows the belt along the slope. Grade is rise/run,
        // so sin(atan(grade)) converts belt distance into vertical distance.
        double grade = current.MeasuredInclinePercent / 100d;
        double verticalMeters = distanceMeters * grade / Math.Sqrt(1 + (grade * grade));
        elevation += verticalMeters;
        if (verticalMeters >= 0) ascent += verticalMeters;
        else descent -= verticalMeters;
      }

      points[index] = new SessionElevationPoint(current.Sequence, current.Elapsed, elevation);
    }

    return new SessionElevationStatistics(ascent, descent, elevation, Array.AsReadOnly(points));
  }
}
