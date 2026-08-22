using System.Text.Json;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Protocols.Exports;

internal sealed record SessionFitMetrics(
  IReadOnlyDictionary<long, float> VerticalSpeedBySequence,
  IReadOnlyDictionary<long, byte> HeartRateZoneBySequence,
  IReadOnlyList<float>? TimeInHeartRateZoneSeconds);

internal static class SessionFitMetricsCalculator
{
  public static SessionFitMetrics Calculate(
    StoredWorkoutSession session,
    SessionElevationStatistics elevation)
  {
    ArgumentNullException.ThrowIfNull(session);
    ArgumentNullException.ThrowIfNull(elevation);
    var verticalSpeeds = new Dictionary<long, float>(session.Samples.Count);
    if (session.Samples.Count > 0)
    {
      verticalSpeeds[session.Samples[0].Sequence] = 0;
      for (var index = 1; index < session.Samples.Count; index++)
      {
        double seconds = (session.Samples[index].Elapsed - session.Samples[index - 1].Elapsed).TotalSeconds;
        double delta = elevation.Points[index].ElevationMeters - elevation.Points[index - 1].ElevationMeters;
        verticalSpeeds[session.Samples[index].Sequence] = seconds > 0 ? (float)(delta / seconds) : 0;
      }
    }

    SessionHeartRateZoneSnapshot[] zones = ReadFiveZoneProfile(session.Definition.ControllerConfigurationJson);
    var zoneBySequence = new Dictionary<long, byte>();
    if (zones.Length != 5)
    {
      return new SessionFitMetrics(verticalSpeeds, zoneBySequence, null);
    }

    var zoneSeconds = new double[5];
    for (var index = 1; index < session.Samples.Count; index++)
    {
      SessionSample sample = session.Samples[index];
      if (sample.HeartRateBpm is not { } heartRate)
      {
        continue;
      }

      int zoneIndex = Array.FindIndex(zones, zone => heartRate >= zone.MinimumBpm && heartRate <= zone.MaximumBpm);
      if (zoneIndex < 0)
      {
        continue;
      }

      zoneBySequence[sample.Sequence] = (byte)(zoneIndex + 1);
      zoneSeconds[zoneIndex] += (sample.Elapsed - session.Samples[index - 1].Elapsed).TotalSeconds;
    }

    return new SessionFitMetrics(
      verticalSpeeds,
      zoneBySequence,
      Array.AsReadOnly(zoneSeconds.Select(static value => (float)value).ToArray()));
  }

  private static SessionHeartRateZoneSnapshot[] ReadFiveZoneProfile(string configurationJson)
  {
    try
    {
      SessionExecutionConfiguration? configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
        configurationJson,
        new JsonSerializerOptions(JsonSerializerDefaults.Web));
      SessionHeartRateZoneSnapshot[] zones = configuration?.Profile?.HeartRateZones?
        .OrderBy(static zone => zone.Number)
        .ToArray() ?? [];
      return zones.Length == 5 && zones.Select(static zone => zone.Number).SequenceEqual([1, 2, 3, 4, 5])
        ? zones
        : [];
    }
    catch (JsonException)
    {
      return [];
    }
  }
}
