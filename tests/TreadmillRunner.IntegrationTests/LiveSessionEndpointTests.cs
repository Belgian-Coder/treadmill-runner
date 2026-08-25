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
  private static readonly Guid HeartRateService = Guid.Parse("0000180d-0000-1000-8000-00805f9b34fb");

  [Fact]
  public async Task Prepared_non_heart_rate_session_allows_heart_rate_enrollment_but_not_treadmill_replacement()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
    {
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);
    }

    (Guid profileId, Guid revisionId) = await SeedPlanAsync(client);
    string holderId = $"prepared-enrollment-{Guid.NewGuid():N}";
    ControlLease lease = Assert.IsType<ControlLease>(
      await (await client.PostAsJsonAsync("/api/live/lease/acquire", new { holderId }))
        .Content.ReadFromJsonAsync<ControlLease>());
    using HttpResponseMessage armResponse = await client.PostAsJsonAsync(
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

    using HttpResponseMessage heartRateEnrollment = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "HeartRate",
      deviceId = $"HR{Guid.NewGuid():N}"[..12],
      displayName = "Venu 3",
      serviceUuids = new[] { HeartRateService },
      telemetryMode = (string?)null,
      ownerProfileIds = new[] { profileId },
      autoConnect = true,
    });
    Assert.Equal(HttpStatusCode.Created, heartRateEnrollment.StatusCode);

    using HttpResponseMessage treadmillEnrollment = await client.PostAsJsonAsync("/api/devices/enrollments", new
    {
      operationId = Guid.NewGuid(),
      role = "Treadmill",
      deviceId = "A1B2C3D4E5F6",
      displayName = "Replacement treadmill",
      serviceUuids = new[] { Guid.Parse("00001826-0000-1000-8000-00805f9b34fb") },
      telemetryMode = "Ftms",
    });
    Assert.Equal(HttpStatusCode.Conflict, treadmillEnrollment.StatusCode);
    Assert.Contains(
      "Device enrollment cannot change while a workout is active.",
      await treadmillEnrollment.Content.ReadAsStringAsync(),
      StringComparison.Ordinal);

    using HttpResponseMessage disconnect = await client.PostAsync(
      $"/api/devices/enrollments/{Guid.NewGuid()}/disconnect",
      null);
    Assert.Equal(HttpStatusCode.Conflict, disconnect.StatusCode);
    Assert.Contains(
      "Bluetooth disconnect is not a treadmill stop mechanism.",
      await disconnect.Content.ReadAsStringAsync(),
      StringComparison.Ordinal);

    using HttpResponseMessage finalReset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    Assert.Equal(HttpStatusCode.NoContent, finalReset.StatusCode);
  }

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
    Assert.True(overridden.Live.EstimatedKilocalories > 0);
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
    Assert.Equal(
      detail.RootElement.GetProperty("samples").GetArrayLength(),
      detail.RootElement.GetProperty("totalSampleCount").GetInt32());
    JsonElement analytics = detail.RootElement.GetProperty("analytics");
    Assert.Equal("Aerobic", analytics.GetProperty("heartRateZones")[0].GetProperty("name").GetString());
    JsonElement zoneSnapshots = detail.RootElement.GetProperty("heartRateZones");
    Assert.Equal(1, zoneSnapshots.GetArrayLength());
    Assert.Equal(125, zoneSnapshots[0].GetProperty("minimumBpm").GetInt32());
    Assert.Equal(145, zoneSnapshots[0].GetProperty("maximumBpm").GetInt32());
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
    string tcx = await client.GetStringAsync($"/api/history/{completed.SessionId}/export.tcx");
    Assert.Contains("TrainingCenterDatabase", tcx, StringComparison.Ordinal);
    Assert.Contains("DistanceMeters", tcx, StringComparison.Ordinal);
    using JsonDocument nativeExport = JsonDocument.Parse(
      await client.GetByteArrayAsync($"/api/history/{completed.SessionId}/export.json"));
    Assert.Equal("treadmillrunner.session/v1", nativeExport.RootElement.GetProperty("schema").GetString());
    Assert.Equal("Metric", nativeExport.RootElement.GetProperty("unitSystem").GetString());
    Assert.Equal(
      detail.RootElement.GetProperty("totalSampleCount").GetInt32(),
      nativeExport.RootElement.GetProperty("samples").GetArrayLength());

    using HttpResponseMessage firstDebrief = await client.PutAsJsonAsync(
      $"/api/history/{completed.SessionId}/debrief",
      new { profileId, perceivedExertion = 7, note = "  Controlled finish.  " });
    Assert.Equal(HttpStatusCode.OK, firstDebrief.StatusCode);
    SessionDebrief savedDebrief = Assert.IsType<SessionDebrief>(
      await firstDebrief.Content.ReadFromJsonAsync<SessionDebrief>());
    Assert.Equal("Controlled finish.", savedDebrief.Note);
    using HttpResponseMessage editedDebrief = await client.PutAsJsonAsync(
      $"/api/history/{completed.SessionId}/debrief",
      new { profileId, perceivedExertion = 6, note = "Edited from History." });
    Assert.Equal(HttpStatusCode.OK, editedDebrief.StatusCode);
    using JsonDocument detailAfterDebrief = JsonDocument.Parse(
      await client.GetStringAsync($"/api/history/{completed.SessionId}"));
    Assert.Equal(6, detailAfterDebrief.RootElement.GetProperty("debrief").GetProperty("perceivedExertion").GetInt32());
    Assert.Equal("Edited from History.", detailAfterDebrief.RootElement.GetProperty("debrief").GetProperty("note").GetString());

    JsonElement weekly = await client.GetFromJsonAsync<JsonElement>(
      $"/api/history/weekly?profileId={profileId}");
    Assert.Equal(1, weekly.GetProperty("completedSessionCount").GetInt32());
  }

  [Fact]
  public async Task Physical_manual_targets_persist_until_the_next_fixed_segment()
  {
    using HttpClient client = factory.CreateClient();
    using (HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/simulator/reset", new { }))
      Assert.Equal(HttpStatusCode.NoContent, reset.StatusCode);

    (Guid profileId, Guid revisionId) = await SeedPlanAsync(
      client,
      firstStepDurationMinutes: 0.05,
      includeSecondStep: true);
    const string holderId = "physical-manual-segment";
    ControlLease lease = Assert.IsType<ControlLease>(await (await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId })).Content.ReadFromJsonAsync<ControlLease>());
    using HttpResponseMessage arm = await client.PostAsJsonAsync("/api/live/sessions/arm", new
    {
      profileId,
      workoutRevisionId = revisionId,
      holderId,
      leaseId = lease.Id,
      operationId = Guid.NewGuid(),
    });
    Assert.Equal(HttpStatusCode.Created, arm.StatusCode);

    using HttpResponseMessage start = await client.PostAsJsonAsync(
      "/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 6.5, measuredInclinePercent = 1.0 });
    Assert.Equal(HttpStatusCode.NoContent, start.StatusCode);
    await Task.Delay(TimeSpan.FromMilliseconds(750));

    using HttpResponseMessage physicalOverride = await client.PostAsJsonAsync(
      "/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 7.2, measuredInclinePercent = 3.0 });
    Assert.Equal(HttpStatusCode.NoContent, physicalOverride.StatusCode);
    await Task.Delay(TimeSpan.FromMilliseconds(750));

    ActiveSessionSnapshot currentStep = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(0, Assert.IsType<ActiveWorkoutStep>(currentStep.CurrentStep).Index);
    Assert.Equal(7.2, currentStep.Live.SpeedKph, precision: 3);
    Assert.Equal(3.0, currentStep.Live.InclinePercent, precision: 3);

    ActiveSessionSnapshot nextStep = await WaitForStepAsync(client, 1, TimeSpan.FromSeconds(4));
    await Task.Delay(TimeSpan.FromMilliseconds(750));
    nextStep = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(1, Assert.IsType<ActiveWorkoutStep>(nextStep.CurrentStep).Index);
    Assert.Equal(5.5, nextStep.Live.SpeedKph, precision: 3);
    Assert.Equal(0.5, nextStep.Live.InclinePercent, precision: 3);
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
  public async Task Confirmed_stop_pauses_reset_preserves_recording_and_end_is_explicit()
  {
    using HttpClient client = factory.CreateClient();
    await client.PostAsJsonAsync("/api/live/simulator/reset", new { });
    (Guid profileId, Guid revisionId) = await SeedPlanAsync(client);
    const string holderId = "pause-reset-end";
    ControlLease lease = Assert.IsType<ControlLease>(await (await client.PostAsJsonAsync(
      "/api/live/lease/acquire", new { holderId })).Content.ReadFromJsonAsync<ControlLease>());
    ActiveSessionSnapshot armed = Assert.IsType<ActiveSessionSnapshot>(await (await client.PostAsJsonAsync(
      "/api/live/sessions/arm",
      new { profileId, workoutRevisionId = revisionId, holderId, leaseId = lease.Id, operationId = Guid.NewGuid() }))
      .Content.ReadFromJsonAsync<ActiveSessionSnapshot>());
    await client.PostAsJsonAsync("/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 6.0, measuredInclinePercent = 1.0 });
    await Task.Delay(TimeSpan.FromMilliseconds(1_200));
    ActiveSessionSnapshot running = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));

    using HttpResponseMessage stop = await client.PostAsJsonAsync("/api/live/sessions/stop", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = running.Version,
    });
    Assert.Equal(HttpStatusCode.OK, stop.StatusCode);
    ActiveSessionSnapshot paused = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(SessionState.PausedWaitingForPhysicalResume, paused.Live.SessionState);
    Assert.True(paused.Live.Elapsed > TimeSpan.Zero);
    Assert.True(paused.WorkoutElapsed > TimeSpan.Zero);
    Assert.Empty(Assert.IsType<SessionSummary[]>(
      await client.GetFromJsonAsync<SessionSummary[]>($"/api/history?profileId={profileId}")));

    using HttpResponseMessage reset = await client.PostAsJsonAsync("/api/live/sessions/reset-progress", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = paused.Version,
    });
    Assert.Equal(HttpStatusCode.OK, reset.StatusCode);
    ActiveSessionSnapshot restarted = Assert.IsType<ActiveSessionSnapshot>(
      await reset.Content.ReadFromJsonAsync<ActiveSessionSnapshot>());
    Assert.Equal(SessionState.PausedWaitingForPhysicalResume, restarted.Live.SessionState);
    Assert.Equal(0, restarted.CurrentStep?.Index);
    Assert.Equal(TimeSpan.Zero, restarted.WorkoutElapsed);
    Assert.Equal(paused.Live.Elapsed, restarted.Live.Elapsed);

    string unfinishedDetail = await client.GetStringAsync($"/api/history/{restarted.SessionId}");
    Assert.Contains("workout-progress-reset", unfinishedDetail, StringComparison.Ordinal);

    using HttpResponseMessage invalidEnd = await client.PostAsJsonAsync("/api/live/sessions/end", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = restarted.Version - 1,
    });
    Assert.Equal(HttpStatusCode.Conflict, invalidEnd.StatusCode);

    await client.PostAsJsonAsync("/api/live/simulator/physical-motion",
      new { isMoving = true, measuredSpeedKph = 6.0, measuredInclinePercent = 1.0 });
    ActiveSessionSnapshot resumed = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
    Assert.Equal(SessionState.Running, resumed.Live.SessionState);
    using HttpResponseMessage secondStop = await client.PostAsJsonAsync("/api/live/sessions/stop", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = resumed.Version,
    });
    Assert.Equal(HttpStatusCode.OK, secondStop.StatusCode);
    ActiveSessionSnapshot stoppedAgain = Assert.IsType<ActiveSessionSnapshot>(
      await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));

    using HttpResponseMessage end = await client.PostAsJsonAsync("/api/live/sessions/end", new
    {
      operationId = Guid.NewGuid(),
      holderId,
      leaseId = lease.Id,
      expectedSessionVersion = stoppedAgain.Version,
    });
    Assert.Equal(HttpStatusCode.OK, end.StatusCode);
    ActiveSessionSnapshot ended = Assert.IsType<ActiveSessionSnapshot>(
      await end.Content.ReadFromJsonAsync<ActiveSessionSnapshot>());
    Assert.Equal(SessionState.Stopped, ended.Live.SessionState);
    Assert.Single(Assert.IsType<SessionSummary[]>(
      await client.GetFromJsonAsync<SessionSummary[]>($"/api/history?profileId={profileId}")));
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
    Assert.Null(stale.Live.HeartRateBpm);
    Assert.NotNull(stale.Live.HeartRateObservedAt);
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
    double fixedSpeedKph = 6.5,
    double firstStepDurationMinutes = 20,
    bool includeSecondStep = false)
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

    object[] blocks =
    [
      new
      {
        kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = firstStepDurationMinutes,
        speedKind = heartRate ? "heartRate" : "fixed", speedStartKph = fixedSpeedKph, speedEndKph = 0.0,
        heartRateMinimumBpm = heartRate ? 125 : 0, heartRateMaximumBpm = heartRate ? 145 : 0,
        heartRateZoneNumber = 0, heartRateInitialSpeedKph = heartRate ? 6.0 : 0.0,
        heartRateMinimumSpeedKph = heartRate ? 4.0 : 0.0, heartRateMaximumSpeedKph = heartRate ? 8.0 : 0.0, inclineKind = "fixed",
        inclineStartPercent = 1.0, inclineEndPercent = 0.0, cue = "Settle", notes = (string?)null,
      },
    ];
    if (includeSecondStep)
    {
      blocks =
      [
        .. blocks,
        new
        {
          kind = "step", repetitions = 1, blocks = Array.Empty<object>(), goalKind = "time", goalValue = 10.0,
          speedKind = "fixed", speedStartKph = 5.5, speedEndKph = 0.0,
          heartRateMinimumBpm = 0, heartRateMaximumBpm = 0,
          heartRateZoneNumber = 0, heartRateInitialSpeedKph = 0.0,
          heartRateMinimumSpeedKph = 0.0, heartRateMaximumSpeedKph = 0.0, inclineKind = "fixed",
          inclineStartPercent = 0.5, inclineEndPercent = 0.0, cue = "Recover", notes = (string?)null,
        },
      ];
    }

    using HttpResponseMessage workoutResponse = await client.PostAsJsonAsync("/api/planning/workouts", new
    {
      operationId = Guid.NewGuid(),
      name = $"Workout {Guid.NewGuid():N}",
      description = "Integration session",
      blocks,
    });
    Assert.Equal(HttpStatusCode.Created, workoutResponse.StatusCode);
    using JsonDocument workoutDocument = await JsonDocument.ParseAsync(
      await workoutResponse.Content.ReadAsStreamAsync());
    Guid revisionId = workoutDocument.RootElement.GetProperty("revisionId").GetGuid();
    return (profileId, revisionId);
  }

  private static async Task<ActiveSessionSnapshot> WaitForStepAsync(
    HttpClient client,
    int stepIndex,
    TimeSpan timeout)
  {
    DateTimeOffset deadline = DateTimeOffset.UtcNow + timeout;
    do
    {
      ActiveSessionSnapshot snapshot = Assert.IsType<ActiveSessionSnapshot>(
        await client.GetFromJsonAsync<ActiveSessionSnapshot>("/api/live/session"));
      if (snapshot.CurrentStep?.Index >= stepIndex)
        return snapshot;
      await Task.Delay(TimeSpan.FromMilliseconds(100));
    }
    while (DateTimeOffset.UtcNow < deadline);

    throw new TimeoutException($"Workout did not reach step {stepIndex} within {timeout}.");
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
