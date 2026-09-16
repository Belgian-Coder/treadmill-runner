using System.Globalization;
using System.Text;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Web.Live;

public sealed record SessionChartProjection(
  long Version,
  TimeSpan Duration,
  ChartAxisScale SpeedAxis,
  ChartAxisScale InclineAxis,
  int HeartRateAxisMinimum,
  int HeartRateAxisMaximum,
  IReadOnlyList<SessionSample> RenderSamples,
  IReadOnlyList<SessionHeartRateZoneSnapshot> HeartRateBoundaryZones,
  string PlannedSpeedPath,
  string RequestedSpeedPath,
  string MeasuredSpeedPath,
  string PlannedInclinePath,
  string RequestedInclinePath,
  string MeasuredInclinePath,
  string HeartRatePath,
  IReadOnlyList<ChartInspectionPoint> SpeedInclineInspectionPoints,
  IReadOnlyList<ChartInspectionPoint> HeartRateInspectionPoints);

public sealed class SessionChartProjectionCache
{
  private ProjectionKey? cachedKey;
  private SessionChartProjection? cachedProjection;

  public SessionChartProjection Get(StoredWorkoutSessionView session)
  {
    ArgumentNullException.ThrowIfNull(session);
    SessionSample? last = session.Samples.LastOrDefault();
    int zoneFingerprint = session.HeartRateZones is null
      ? 0
      : session.HeartRateZones.Aggregate(17, static (hash, zone) => HashCode.Combine(hash, zone.Number, zone.MinimumBpm, zone.MaximumBpm));
    var key = new ProjectionKey(
      session.Definition.SessionId,
      session.Samples.Count,
      session.TotalSampleCount,
      last?.Sequence ?? -1,
      last?.Elapsed.Ticks ?? 0,
      session.Duration.Ticks,
      zoneFingerprint);
    if (cachedKey == key && cachedProjection is not null) return cachedProjection;
    cachedKey = key;
    cachedProjection = Build(session, key);
    return cachedProjection;
  }

  private static SessionChartProjection Build(StoredWorkoutSessionView session, ProjectionKey key)
  {
    IReadOnlyList<SessionSample> samples = Reduce(session.Samples);
    TimeSpan duration = session.Samples.Count > 0 && session.Samples[^1].Elapsed > session.Duration
      ? session.Samples[^1].Elapsed
      : session.Duration;
    ChartAxisScale speedAxis = ChartAxisScale.Create(session.Samples
      .SelectMany(static sample => new double?[] { sample.PlannedSpeedKph, sample.RequestedSpeedKph, sample.MeasuredSpeedKph }));
    ChartAxisScale inclineAxis = ChartAxisScale.Create(session.Samples
      .SelectMany(static sample => new double?[] { sample.PlannedInclinePercent, sample.RequestedInclinePercent, sample.MeasuredInclinePercent }));
    SessionHeartRateZoneSnapshot[] zones = session.HeartRateZones?
      .Where(static zone => zone.MinimumBpm > 0)
      .OrderBy(static zone => zone.Number)
      .ToArray() ?? [];
    int dataMinimum = Math.Max(0, ((session.Samples.Where(static sample => sample.HeartRateBpm is not null)
      .Select(static sample => (int)sample.HeartRateBpm!.Value).DefaultIfEmpty(60).Min() - 10) / 10) * 10);
    int dataMaximum = Math.Max(dataMinimum + 20, ((session.Samples.Where(static sample => sample.HeartRateBpm is not null)
      .Select(static sample => (int)sample.HeartRateBpm!.Value).DefaultIfEmpty(170).Max() + 19) / 10) * 10);
    int heartRateMinimum = Math.Min(dataMinimum, zones.Select(static zone => (zone.MinimumBpm / 10) * 10).DefaultIfEmpty(dataMinimum).Min());
    int heartRateMaximum = Math.Max(dataMaximum, zones.Select(static zone => ((zone.MinimumBpm + 19) / 10) * 10).DefaultIfEmpty(dataMaximum).Max());
    double X(TimeSpan elapsed) => 10 + (Math.Clamp(elapsed.TotalSeconds / Math.Max(1, duration.TotalSeconds), 0, 1) * 700);
    double HeartRateY(double value) => 210 - (Math.Clamp((value - heartRateMinimum) / Math.Max(1, heartRateMaximum - heartRateMinimum), 0, 1) * 200);

    string Path(Func<SessionSample, double?> selector, ChartAxisScale axis, bool step = false)
    {
      var path = new StringBuilder();
      bool drawing = false;
      double previousY = 0;
      foreach (SessionSample sample in samples)
      {
        if (selector(sample) is not { } value) { drawing = false; continue; }
        double x = X(sample.Elapsed);
        double y = axis.ProjectY(value);
        if (drawing && step)
        {
          path.Append(" L")
            .Append(x.ToString("0.##", CultureInfo.InvariantCulture)).Append(' ')
            .Append(previousY.ToString("0.##", CultureInfo.InvariantCulture));
        }
        path.Append(drawing ? " L" : "M")
          .Append(x.ToString("0.##", CultureInfo.InvariantCulture)).Append(' ')
          .Append(y.ToString("0.##", CultureInfo.InvariantCulture));
        previousY = y;
        drawing = true;
      }
      return path.ToString();
    }

    var heartRatePath = new StringBuilder();
    bool heartRateDrawing = false;
    foreach (SessionSample sample in samples)
    {
      if (sample.HeartRateBpm is not { } heartRate) { heartRateDrawing = false; continue; }
      heartRatePath.Append(heartRateDrawing ? " L" : "M")
        .Append(X(sample.Elapsed).ToString("0.##", CultureInfo.InvariantCulture)).Append(' ')
        .Append(HeartRateY(heartRate).ToString("0.##", CultureInfo.InvariantCulture));
      heartRateDrawing = true;
    }

    ChartInspectionPoint[] speedInclinePoints = samples.Select(sample => new ChartInspectionPoint(sample.Elapsed, X(sample.Elapsed),
    [
      sample.PlannedSpeedKph, sample.RequestedSpeedKph, sample.MeasuredSpeedKph,
      sample.PlannedInclinePercent, sample.RequestedInclinePercent, sample.MeasuredInclinePercent,
    ])).ToArray();
    ChartInspectionPoint[] heartRatePoints = samples.Select(sample => new ChartInspectionPoint(
      sample.Elapsed, X(sample.Elapsed), [sample.HeartRateBpm])).ToArray();
    var versionHash = new HashCode();
    versionHash.Add(key.SessionId);
    versionHash.Add(key.SampleCount);
    versionHash.Add(key.TotalSampleCount);
    versionHash.Add(key.LastSequence);
    versionHash.Add(key.LastElapsedTicks);
    versionHash.Add(key.DurationTicks);
    versionHash.Add(speedAxis.Minimum);
    versionHash.Add(speedAxis.Maximum);
    versionHash.Add(inclineAxis.Minimum);
    versionHash.Add(inclineAxis.Maximum);
    versionHash.Add(heartRateMinimum);
    versionHash.Add(heartRateMaximum);
    versionHash.Add(key.ZoneFingerprint);
    long version = versionHash.ToHashCode();
    return new SessionChartProjection(
      version, duration, speedAxis, inclineAxis, heartRateMinimum, heartRateMaximum,
      samples, zones,
      Path(static sample => sample.PlannedSpeedKph, speedAxis),
      Path(static sample => sample.RequestedSpeedKph, speedAxis, step: true),
      Path(static sample => sample.MeasuredSpeedKph, speedAxis),
      Path(static sample => sample.PlannedInclinePercent, inclineAxis),
      Path(static sample => sample.RequestedInclinePercent, inclineAxis, step: true),
      Path(static sample => sample.MeasuredInclinePercent, inclineAxis),
      heartRatePath.ToString(), speedInclinePoints, heartRatePoints);
  }

  private static IReadOnlyList<SessionSample> Reduce(IReadOnlyList<SessionSample> samples)
  {
    const int maximum = 720;
    if (samples.Count <= maximum) return samples;
    int stride = (int)Math.Ceiling(samples.Count / (double)(maximum - 1));
    return samples.Where((_, index) => index % stride == 0).Append(samples[^1]).Distinct().ToArray();
  }

  private sealed record ProjectionKey(
    Guid SessionId,
    int SampleCount,
    int TotalSampleCount,
    long LastSequence,
    long LastElapsedTicks,
    long DurationTicks,
    int ZoneFingerprint);
}
