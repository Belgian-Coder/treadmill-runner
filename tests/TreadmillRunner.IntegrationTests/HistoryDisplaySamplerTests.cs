using System.Text;
using System.Text.Json;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class HistoryDisplaySamplerTests
{
  [Fact]
  public void Four_hour_session_is_bounded_for_display_without_changing_full_resolution_data()
  {
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset startedAt = DateTimeOffset.Parse("2026-08-08T14:01:24Z");
    SessionSample[] storedSamples = Enumerable.Range(0, 12_788)
      .Select(sequence => new SessionSample(
        sessionId,
        sequence,
        startedAt.AddSeconds(sequence),
        TimeSpan.FromSeconds(sequence),
        plannedSpeedKph: 4.5,
        requestedSpeedKph: 4.5,
        measuredSpeedKph: sequence % 60 == 0 ? 4.6 : 4.5,
        plannedInclinePercent: 0,
        requestedInclinePercent: 0,
        measuredInclinePercent: 0,
        heartRateBpm: null,
        distanceKilometers: sequence * 4.5 / 3_600,
        estimatedKilocalories: sequence * 0.1,
        telemetryAge: TimeSpan.Zero,
        metricAlgorithmVersion: SessionMetricAlgorithms.EstimatedCaloriesV1))
      .ToArray();

    IReadOnlyList<SessionSample> displaySamples = HistoryDisplaySampler.Select(storedSamples);

    Assert.Equal(12_788, storedSamples.Length);
    Assert.Equal(HistoryDisplaySampler.MaximumSamples, displaySamples.Count);
    Assert.Same(storedSamples[0], displaySamples[0]);
    Assert.Same(storedSamples[^1], displaySamples[^1]);
    Assert.True(displaySamples.Zip(displaySamples.Skip(1), static (left, right) => left.Sequence < right.Sequence).All(static ordered => ordered));
    Assert.True(
      Encoding.UTF8.GetByteCount(JsonSerializer.Serialize(displaySamples)) < 200_000,
      "The bounded display sample payload should remain below 200 KB for the preserved four-hour reproduction shape.");
  }
}
