using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class LiveSessionEndpointTests(PlanningGatewayFactory factory) :
  IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Gateway_owns_arm_physical_start_completion_and_history()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
    {
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);
    }
    (Guid profileId, Guid revisionId) = await SeedPlanAsync(client);
    const string holderId = "integration-browser";

    HttpResponseMessage leaseResponse = await client.PostAsJsonAsync(
      "/api/live/lease/acquire",
      new { holderId });
    Assert.Equal(HttpStatusCode.OK, leaseResponse.StatusCode);
    ControlLease lease = Assert.IsType<ControlLease>(
      await leaseResponse.Content.ReadFromJsonAsync<ControlLease>());

    HttpResponseMessage armResponse = await client.PostAsJsonAsync(
      "/api/live/sessions/arm",
      new
      {
        profileId,
        workoutRevisionId = revisionId,
        holderId,
        leaseId = lease.Id,
        operationId = Guid.NewGuid(),
        selectionSource = "Library",
      });
    Assert.Equal(HttpStatusCode.Created, armResponse.StatusCode);
    ActiveSessionSnapshot armed = Assert.IsType<ActiveSessionSnapshot>(
      await armResponse.Content.ReadFromJsonAsync<ActiveSessionSnapshot>());
    Assert.Equal(SessionState.ArmedWaitingForPhysicalStart, armed.Live.SessionState);

    HttpResponseMessage motionResponse = await client.PostAsJsonAsync(
      "/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 6.4, measuredInclinePercent = 1.5 });
    Assert.Equal(HttpStatusCode.NoContent, motionResponse.StatusCode);
    ActiveSessionSnapshot running = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(SessionState.Running, running.Live.SessionState);
    Assert.Equal(6.4, running.Live.SpeedKph);
    await Task.Delay(TimeSpan.FromMilliseconds(1_200));

    Guid overrideOperationId = Guid.NewGuid();
    HttpResponseMessage overrideResponse = await client.PostAsJsonAsync(
      "/api/live/sessions/speed-override",
      new
      {
        operationId = overrideOperationId,
        adjustmentKph = 0.1,
        holderId,
        leaseId = lease.Id,
        expectedSessionVersion = running.Version,
      });
    Assert.Equal(HttpStatusCode.OK, overrideResponse.StatusCode);
    ManualControlResponse overriddenResponse = Assert.IsType<ManualControlResponse>(
      await overrideResponse.Content.ReadFromJsonAsync<ManualControlResponse>());
    ActiveSessionSnapshot overridden = overriddenResponse.Snapshot;
    Assert.Equal(6.6, overridden.RequestedSpeedKph, precision: 3);
    Assert.Equal(6.6, overridden.Live.SpeedKph, precision: 3);

    HttpResponseMessage replayResponse = await client.PostAsJsonAsync(
      "/api/live/sessions/speed-override",
      new
      {
        operationId = overrideOperationId,
        adjustmentKph = 0.1,
        holderId,
        leaseId = lease.Id,
        expectedSessionVersion = overridden.Version,
      });
    Assert.Equal(HttpStatusCode.OK, replayResponse.StatusCode);
    ManualControlResponse replayResponseBody = Assert.IsType<ManualControlResponse>(
      await replayResponse.Content.ReadFromJsonAsync<ManualControlResponse>());
    ActiveSessionSnapshot replayed = replayResponseBody.Snapshot;
    Assert.Equal(6.6, replayed.RequestedSpeedKph, precision: 3);

    Guid inclineOperationId = Guid.NewGuid();
    HttpResponseMessage inclineResponse = await client.PostAsJsonAsync(
      "/api/live/sessions/incline-override",
      new
      {
        operationId = inclineOperationId,
        targetPercent = 2.5,
        holderId,
        leaseId = lease.Id,
        expectedSessionVersion = replayed.Version,
      });
    Assert.Equal(HttpStatusCode.OK, inclineResponse.StatusCode);
    ManualControlResponse inclineResponseBody = Assert.IsType<ManualControlResponse>(
      await inclineResponse.Content.ReadFromJsonAsync<ManualControlResponse>());
    Assert.Equal(2.5, inclineResponseBody.Snapshot.RequestedInclinePercent, precision: 3);
    Assert.Equal(2.5, inclineResponseBody.Snapshot.Live.InclinePercent, precision: 3);

    HttpResponseMessage inclineReplayResponse = await client.PostAsJsonAsync(
      "/api/live/sessions/incline-override",
      new
      {
        operationId = inclineOperationId,
        targetPercent = 4.0,
        holderId,
        leaseId = lease.Id,
        expectedSessionVersion = inclineResponseBody.Snapshot.Version,
      });
    Assert.Equal(HttpStatusCode.OK, inclineReplayResponse.StatusCode);
    ManualControlResponse inclineReplayBody = Assert.IsType<ManualControlResponse>(
      await inclineReplayResponse.Content.ReadFromJsonAsync<ManualControlResponse>());
    Assert.Equal(2.5, inclineReplayBody.Snapshot.RequestedInclinePercent, precision: 3);

    HttpResponseMessage completeResponse = await client.PostAsJsonAsync(
      "/api/live/simulator/complete-physical-session",
      new { });
    Assert.Equal(HttpStatusCode.NoContent, completeResponse.StatusCode);
    ActiveSessionSnapshot completed = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(SessionState.Completed, completed.Live.SessionState);

    SessionSummary[] history = Assert.IsType<SessionSummary[]>(
      await client.GetFromJsonAsync<SessionSummary[]>($"/api/history?profileId={profileId}"));
    Assert.Single(history);
    Assert.Equal(completed.SessionId, history[0].SessionId);
    string detailJson = await client.GetStringAsync($"/api/history/{completed.SessionId}");
    Assert.Contains("manual-speed-override", detailJson, StringComparison.Ordinal);
    Assert.Contains("manual-incline-override", detailJson, StringComparison.Ordinal);
    using JsonDocument detail = JsonDocument.Parse(detailJson);
    JsonElement analytics = detail.RootElement.GetProperty("analytics");
    Assert.Equal("Aerobic", analytics.GetProperty("heartRateZones")[0].GetProperty("name").GetString());
    Assert.Equal(1, analytics.GetProperty("eventCounts").GetProperty("manualSpeedOverrides").GetInt32());
    Assert.Equal(1, analytics.GetProperty("eventCounts").GetProperty("manualInclineOverrides").GetInt32());
    Assert.True(analytics.GetProperty("adherencePercentage").GetDouble() >= 0);

    string csv = await client.GetStringAsync($"/api/history/{completed.SessionId}/export.csv");
    Assert.StartsWith("captured_at_utc,elapsed_seconds", csv, StringComparison.Ordinal);
    byte[] fit = await client.GetByteArrayAsync($"/api/history/{completed.SessionId}/export.fit");
    using var fitStream = new MemoryStream(fit);
    var fitDecoder = new Dynastream.Fit.Decode();
    Assert.True(fitDecoder.IsFIT(fitStream));
    fitStream.Position = 0;
    Assert.True(fitDecoder.CheckIntegrity(fitStream));

    JsonElement weekly = await client.GetFromJsonAsync<JsonElement>(
      $"/api/history/weekly?profileId={profileId}");
    Assert.Equal(1, weekly.GetProperty("completedSessionCount").GetInt32());
  }

  [Fact]
  public async Task Remote_start_route_exists_but_fails_closed_without_an_active_verified_context()
  {
    using HttpClient client = factory.CreateClient();
    using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/sessions/start", new
    {
      operationId = Guid.NewGuid(),
      holderId = "no-controller",
      leaseId = Guid.NewGuid(),
      expectedSessionVersion = 0,
    });

    Assert.Equal(HttpStatusCode.Conflict, response.StatusCode);
  }

  [Fact]
  public async Task Preflight_exposes_and_blocks_targets_above_the_selected_profile_limit()
  {
    using HttpClient client = factory.CreateClient();
    await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    (Guid profileId, Guid revisionId) = await SeedPlanAsync(client, maximumSpeedKph: 5, fixedSpeedKph: 6.5);

    PreflightSnapshot preflight = Assert.IsType<PreflightSnapshot>(await client.GetFromJsonAsync<PreflightSnapshot>(
      $"/api/live/preflight?profileId={profileId}&workoutRevisionId={revisionId}"));

    Assert.False(preflight.IsReady);
    Assert.Contains(preflight.Checks, check => check.Id == "workout-targets" && check.Status == PreflightCheckStatus.Blocked);
    WorkoutTargetEvaluation rejected = Assert.Single(preflight.TargetEvaluations, target => target.Disposition == WorkoutTargetDisposition.Rejected);
    Assert.Equal(6.5, rejected.Requested, 3);
    Assert.Null(rejected.Normalized);
  }

  [Fact]
  public async Task Gateway_owned_heart_rate_control_survives_browser_lease_expiry_then_suspends_on_stale_hr()
  {
    using HttpClient client = factory.CreateClient();
    await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    (Guid profileId, Guid revisionId) = await SeedPlanAsync(client, heartRate: true);
    const string holderId = "hr-system-simulation";
    ControlLease lease = Assert.IsType<ControlLease>(await (await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId })).Content.ReadFromJsonAsync<ControlLease>());
    ActiveSessionSnapshot armed = Assert.IsType<ActiveSessionSnapshot>(await (await client.PostAsJsonAsync(
      "/api/live/sessions/arm",
      new { profileId, workoutRevisionId = revisionId, holderId, leaseId = lease.Id, operationId = Guid.NewGuid() }))
      .Content.ReadFromJsonAsync<ActiveSessionSnapshot>());
    await client.PostAsJsonAsync("/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 6.0, measuredInclinePercent = 1.0 });
    ActiveSessionSnapshot running = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    using HttpResponseMessage enable = await client.PostAsJsonAsync("/api/live/sessions/heart-rate-automation", new
    {
      operationId = Guid.NewGuid(),
      mode = HeartRateAutomationMode.Full,
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = running.Version,
    });
    Assert.Equal(HttpStatusCode.OK, enable.StatusCode);

    await client.PostAsJsonAsync("/api/live/simulator/heart-rate", new { beatsPerMinute = 160 });
    await WaitWithHeartRateAsync(client, TimeSpan.FromSeconds(11), 160);
    ActiveSessionSnapshot decreased = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(5.5, decreased.Live.SpeedKph, 1);
    Assert.Equal(5.5, decreased.RequestedSpeedKph, 1);
    Assert.Equal(TreadmillCommandDisposition.Confirmed, decreased.LastCommandResult?.Disposition);

    await client.PostAsJsonAsync("/api/live/simulator/heart-rate", new { beatsPerMinute = 110 });
    await WaitWithHeartRateAsync(client, TimeSpan.FromSeconds(21), 110);
    ActiveSessionSnapshot increased = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(5.7, increased.Live.SpeedKph, 1);
    Assert.Equal(5.7, increased.RequestedSpeedKph, 1);
    Assert.Equal(TreadmillCommandDisposition.Confirmed, increased.LastCommandResult?.Disposition);

    using HttpResponseMessage reacquire = await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId });
    Assert.Equal(HttpStatusCode.OK, reacquire.StatusCode);
    lease = Assert.IsType<ControlLease>(await reacquire.Content.ReadFromJsonAsync<ControlLease>());

    lease = await WaitWithHeartbeatsAsync(client, lease, TimeSpan.FromSeconds(6));
    ActiveSessionSnapshot stale = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(HeartRateAutomationMode.SuspendedSafety, stale.HeartRateAutomationMode);
    Assert.Contains("stale", stale.HeartRateAutomationReason, StringComparison.OrdinalIgnoreCase);

    using HttpResponseMessage stop = await client.PostAsJsonAsync("/api/live/sessions/stop", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = stale.Version,
    });
    Assert.Equal(HttpStatusCode.OK, stop.StatusCode);
  }

  [Fact]
  public async Task Same_holder_reacquires_the_existing_lease_idempotently()
  {
    using HttpClient client = factory.CreateClient();
    await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    string holderId = $"reload-{Guid.NewGuid():N}";

    ControlLease first = Assert.IsType<ControlLease>(await (await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId })).Content.ReadFromJsonAsync<ControlLease>());
    ControlLease recovered = Assert.IsType<ControlLease>(await (await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId })).Content.ReadFromJsonAsync<ControlLease>());

    Assert.Equal(first.Id, recovered.Id);
    Assert.True(recovered.ExpiresAt >= first.ExpiresAt);
  }

  [Fact]
  public async Task Simulator_reset_clears_the_previous_control_lease()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage initialReset = await client.PostAsJsonAsync(
      "/api/live/simulator/reset",
      new { }))
    {
      Assert.Equal(HttpStatusCode.NoContent, initialReset.StatusCode);
    }

    using HttpResponseMessage firstLease = await client.PostAsJsonAsync(
      "/api/live/lease/acquire",
      new { holderId = "browser-before-reset" });
    Assert.Equal(HttpStatusCode.OK, firstLease.StatusCode);

    using HttpResponseMessage reset = await client.PostAsJsonAsync(
      "/api/live/simulator/reset",
      new { });
    Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    using HttpResponseMessage replacementLease = await client.PostAsJsonAsync(
      "/api/live/lease/acquire",
      new { holderId = "browser-after-reset" });
    Assert.Equal(HttpStatusCode.OK, replacementLease.StatusCode);
  }

  [Fact]
  public async Task In_process_controller_heartbeat_acceptance_p95_is_below_100_milliseconds()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
    {
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);
    }

    string holderId = $"latency-{Guid.NewGuid():N}";
    ControlLease lease = Assert.IsType<ControlLease>(
      await (await client.PostAsJsonAsync("/api/live/lease/acquire", new { holderId }))
        .Content.ReadFromJsonAsync<ControlLease>());

    var durations = new List<double>();
    for (var iteration = 0; iteration < 40; iteration++)
    {
      long started = Stopwatch.GetTimestamp();
      using HttpResponseMessage response = await client.PostAsJsonAsync("/api/live/lease/heartbeat", lease);
      durations.Add(Stopwatch.GetElapsedTime(started).TotalMilliseconds);
      Assert.Equal(HttpStatusCode.OK, response.StatusCode);
      lease = Assert.IsType<ControlLease>(await response.Content.ReadFromJsonAsync<ControlLease>());
    }

    double p95 = Percentile(durations, 0.95);
    Assert.True(p95 < 100, $"In-process controller heartbeat p95 was {p95:0.0} ms.");
  }

  private static double Percentile(IReadOnlyCollection<double> values, double percentile)
  {
    double[] ordered = values.Order().ToArray();
    int index = Math.Clamp((int)Math.Ceiling(ordered.Length * percentile) - 1, 0, ordered.Length - 1);
    return ordered[index];
  }

  private static async Task<(Guid ProfileId, Guid RevisionId)> SeedPlanAsync(
    HttpClient client,
    bool heartRate = false,
    double maximumSpeedKph = 15.0,
    double fixedSpeedKph = 6.5)
  {
    using HttpResponseMessage profileResponse = await client.PostAsJsonAsync("/api/planning/profiles", new
    {
      operationId = Guid.NewGuid(),
      displayName = $"Runner {Guid.NewGuid():N}",
      unitSystem = "Metric",
      weightKilograms = 72.5,
      maximumHeartRateBpm = 190,
      maximumSpeedKph,
      heartRateZones = new[] { new { number = 2, name = "Aerobic", minimumBpm = 125, maximumBpm = 145 } },
      expectedVersion = (int?)null,
    });
    Assert.Equal(HttpStatusCode.Created, profileResponse.StatusCode);
    using JsonDocument profileDocument = await JsonDocument.ParseAsync(
      await profileResponse.Content.ReadAsStreamAsync());
    Guid profileId = profileDocument.RootElement.GetProperty("id").GetGuid();

    using HttpResponseMessage workoutResponse = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = $"Workout {Guid.NewGuid():N}",
      description = "Integration session",
      blocks = new[]
      {
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 20.0,
          speedKind = heartRate ? "heartRate" : "fixed", speedStartKph = fixedSpeedKph, speedEndKph = 0.0,
          heartRateMinimumBpm = heartRate ? 125 : 0, heartRateMaximumBpm = heartRate ? 145 : 0,
          heartRateZoneNumber = 0, heartRateInitialSpeedKph = heartRate ? 6.0 : 0.0,
          heartRateMinimumSpeedKph = heartRate ? 4.0 : 0.0, heartRateMaximumSpeedKph = heartRate ? 8.0 : 0.0, inclineKind = "fixed",
          inclineStartPercent = 1.0, inclineEndPercent = 0.0, cue = "Settle", notes = (string?)null,
        },
      },
    });
    Assert.Equal(HttpStatusCode.Created, workoutResponse.StatusCode);
    using JsonDocument workoutDocument = await JsonDocument.ParseAsync(
      await workoutResponse.Content.ReadAsStreamAsync());
    Guid revisionId = workoutDocument.RootElement.GetProperty("revisionId").GetGuid();
    return (profileId, revisionId);
  }

  private static async Task<ControlLease> WaitWithHeartbeatsAsync(
    HttpClient client,
    ControlLease lease,
    TimeSpan duration,
    ushort? heartRateBpm = null)
  {
    DateTimeOffset end = DateTimeOffset.UtcNow + duration;
    while (DateTimeOffset.UtcNow < end)
    {
      TimeSpan remaining = end - DateTimeOffset.UtcNow;
      if (remaining <= TimeSpan.Zero) break;
      await Task.Delay(TimeSpan.FromSeconds(Math.Min(3, remaining.TotalSeconds)));
      if (heartRateBpm is not null)
      {
        using HttpResponseMessage heartRate = await client.PostAsJsonAsync(
          "/api/live/simulator/heart-rate",
          new { beatsPerMinute = heartRateBpm });
        Assert.Equal(HttpStatusCode.OK, heartRate.StatusCode);
      }
      using HttpResponseMessage heartbeat = await client.PostAsJsonAsync("/api/live/lease/heartbeat", lease);
      Assert.Equal(HttpStatusCode.OK, heartbeat.StatusCode);
      lease = Assert.IsType<ControlLease>(await heartbeat.Content.ReadFromJsonAsync<ControlLease>());
    }
    return lease;
  }

  private static async Task WaitWithHeartRateAsync(
    HttpClient client,
    TimeSpan duration,
    ushort heartRateBpm)
  {
    DateTimeOffset end = DateTimeOffset.UtcNow + duration;
    while (DateTimeOffset.UtcNow < end)
    {
      TimeSpan remaining = end - DateTimeOffset.UtcNow;
      if (remaining <= TimeSpan.Zero) break;
      await Task.Delay(TimeSpan.FromSeconds(Math.Min(2, remaining.TotalSeconds)));
      using HttpResponseMessage heartRate = await client.PostAsJsonAsync(
        "/api/live/simulator/heart-rate",
        new { beatsPerMinute = heartRateBpm });
      Assert.Equal(HttpStatusCode.OK, heartRate.StatusCode);
    }
  }
}
