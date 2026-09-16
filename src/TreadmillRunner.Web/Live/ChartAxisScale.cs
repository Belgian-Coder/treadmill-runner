using System.Globalization;

namespace TreadmillRunner.Web.Live;

public sealed record ChartAxisTick(double Value, string Label, double SvgY, double PositionPercent);

public sealed class ChartAxisScale
{
  private static readonly double[] NiceMultipliers = [1, 2, 2.5, 5, 10];

  private ChartAxisScale(double minimum, double maximum, double step, IReadOnlyList<ChartAxisTick> ticks)
  {
    Minimum = minimum;
    Maximum = maximum;
    Step = step;
    Ticks = ticks;
  }

  public double Minimum { get; }
  public double Maximum { get; }
  public double Step { get; }
  public IReadOnlyList<ChartAxisTick> Ticks { get; }

  public static ChartAxisScale Create(
    IEnumerable<double?> values,
    double defaultMinimum = 0,
    double defaultMaximum = 10,
    int targetTickCount = 5)
  {
    ArgumentNullException.ThrowIfNull(values);
    if (!double.IsFinite(defaultMinimum) || !double.IsFinite(defaultMaximum) || defaultMaximum <= defaultMinimum)
      throw new ArgumentOutOfRangeException(nameof(defaultMaximum), "The default axis range must be finite and increasing.");

    double minimum = defaultMinimum;
    double maximum = defaultMaximum;
    foreach (double? candidate in values)
    {
      if (candidate is not { } value || !double.IsFinite(value)) continue;
      minimum = Math.Min(minimum, value);
      maximum = Math.Max(maximum, value);
    }

    return CreateFromBounds(minimum, maximum, targetTickCount);
  }

  public static ChartAxisScale CreateFromBounds(double minimum, double maximum, int targetTickCount = 5)
  {
    if (!double.IsFinite(minimum) || !double.IsFinite(maximum))
      throw new ArgumentOutOfRangeException(nameof(maximum), "Axis bounds must be finite.");
    if (maximum <= minimum) maximum = minimum + 1;
    targetTickCount = Math.Clamp(targetTickCount, 4, 6);

    double range = maximum - minimum;
    double rawStep = range / (targetTickCount - 1);
    int exponent = (int)Math.Floor(Math.Log10(rawStep));
    ScaleCandidate? best = null;
    for (int power = exponent - 2; power <= exponent + 2; power++)
    {
      double magnitude = Math.Pow(10, power);
      foreach (double multiplier in NiceMultipliers)
      {
        double step = multiplier * magnitude;
        double niceMinimum = SnapNearZero(Math.Floor(minimum / step) * step, step);
        double niceMaximum = SnapNearZero(Math.Ceiling(maximum / step) * step, step);
        int tickCount = (int)Math.Round((niceMaximum - niceMinimum) / step) + 1;
        if (tickCount is < 4 or > 6) continue;

        double padding = ((minimum - niceMinimum) + (niceMaximum - maximum)) / range;
        double score = Math.Abs(tickCount - targetTickCount) * 100 + padding + Math.Abs(Math.Log(step / rawStep)) * .01;
        if (best is null || score < best.Score)
          best = new ScaleCandidate(niceMinimum, niceMaximum, step, tickCount, score);
      }
    }

    best ??= new ScaleCandidate(minimum, maximum, range / (targetTickCount - 1), targetTickCount, 0);
    var ticks = new List<ChartAxisTick>(best.TickCount);
    for (int index = best.TickCount - 1; index >= 0; index--)
    {
      double value = SnapNearZero(best.Minimum + (index * best.Step), best.Step);
      double svgY = Project(value, best.Minimum, best.Maximum);
      ticks.Add(new ChartAxisTick(value, Format(value), svgY, svgY / 220 * 100));
    }

    return new ChartAxisScale(best.Minimum, best.Maximum, best.Step, ticks);
  }

  public double ProjectY(double value) => Project(value, Minimum, Maximum);

  public static string SvgCoordinate(double value) =>
    value.ToString("0.##", CultureInfo.InvariantCulture);

  private static double Project(double value, double minimum, double maximum) =>
    210 - (Math.Clamp((value - minimum) / Math.Max(double.Epsilon, maximum - minimum), 0, 1) * 200);

  private static double SnapNearZero(double value, double step) => Math.Abs(value) < Math.Abs(step) * 1e-9 ? 0 : value;
  private static string Format(double value) => value.ToString("0.##", CultureInfo.InvariantCulture);

  private sealed record ScaleCandidate(double Minimum, double Maximum, double Step, int TickCount, double Score);
}
