using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Web.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionChartProjectionCacheTests
{
  [Fact]
  public void Middle_heart_rate_recovery_invalidates_the_cached_graph_path()
  {
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset startedAt = DateTimeOffset.Parse("2026-09-17T18:00:00Z");
    var cache = new SessionChartProjectionCache();
    StoredWorkoutSessionView before = View(sessionId, startedAt, heartRate: null);
    StoredWorkoutSessionView after = View(sessionId, startedAt, heartRate: 142);

    SessionChartProjection initial = cache.Get(before);
    SessionChartProjection recovered = cache.Get(after);

    Assert.Empty(initial.HeartRatePath);
    Assert.NotEmpty(recovered.HeartRatePath);
    Assert.NotEqual(initial.Version, recovered.Version);
  }

  private static StoredWorkoutSessionView View(Guid sessionId, DateTimeOffset startedAt, ushort? heartRate)
  {
    var definition = new NewWorkoutSession(
      sessionId, Guid.NewGuid(), "Runner", Guid.NewGuid(), "Run", startedAt.AddSeconds(-1), "{}", "v1");
    SessionSample sample = new(
      sessionId, 0, startedAt, TimeSpan.Zero, 7, 7, 7, 0, 0, 0, heartRate, 0, 0, TimeSpan.Zero, "v1");
    var analytics = new SessionAnalytics(
      sessionId, [], 100, SessionMetricAlgorithms.AdherenceV1, new SessionEventCounts(0, 0, 0, 0, 0));
    return new(
      definition, SessionState.Completed, startedAt, startedAt.AddSeconds(1), TimeSpan.FromSeconds(1),
      0, 0, heartRate, heartRate, 7, 0, 0, 0, 0, null, [sample], [], analytics, 1);
  }
}
