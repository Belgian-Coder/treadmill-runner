using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Live;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class SessionContractsTests
{
  [Fact]
  public void Session_profile_snapshot_freezes_heart_rate_controller_settings()
  {
    var profile = new UserProfile(
      Guid.NewGuid(),
      "Runner",
      UnitSystem.Metric,
      70,
      190,
      15,
      [new HeartRateZone(2, "Aerobic", 125, 145)],
      new HeartRateControllerSettings(0.3, 45, 0.7, 20));

    SessionProfileSnapshot snapshot = SessionProfileSnapshot.FromProfile(profile);

    SessionHeartRateControllerSnapshot controller = Assert.IsType<SessionHeartRateControllerSnapshot>(
      snapshot.HeartRateController);
    Assert.Equal(0.3, controller.IncreaseStepKph);
    Assert.Equal(45, controller.IncreaseCooldownSeconds);
    Assert.Equal(0.7, controller.DecreaseStepKph);
    Assert.Equal(20, controller.DecreaseCooldownSeconds);
  }

  [Fact]
  public void Preflight_is_ready_only_when_every_required_check_is_ready()
  {
    var ready = CreatePreflight(
        [
          new PreflightCheck("treadmill", "Treadmill", PreflightCheckStatus.Ready),
          new PreflightCheck("heart-rate", "Heart rate", PreflightCheckStatus.NotRequired)
        ]);
    var blocked = CreatePreflight(
        [new PreflightCheck("database", "Database", PreflightCheckStatus.Blocked, "Recovery is incomplete.")]);

    Assert.True(ready.IsReady);
    Assert.Empty(ready.ReadinessBlockers);
    Assert.False(blocked.IsReady);
    Assert.Equal("Recovery is incomplete.", Assert.Single(blocked.ReadinessBlockers).Detail);
  }

  [Fact]
  public void Session_sample_requires_reproducible_finite_metrics()
  {
    var sample = CreateSample(sequence: 4);

    Assert.Equal(4, sample.Sequence);
    Assert.Equal(SessionMetricAlgorithms.EstimatedCaloriesV1, sample.MetricAlgorithmVersion);
    Assert.Throws<ArgumentOutOfRangeException>(() => CreateSample(measuredSpeedKph: double.NaN));
    Assert.Throws<ArgumentException>(() => CreateSample(metricAlgorithmVersion: " "));
  }

  [Fact]
  public void Debrief_normalizes_note_and_bounds_rpe()
  {
    var sessionId = Guid.NewGuid();
    var updatedAt = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var debrief = new SessionDebrief(sessionId, 7, "  Felt controlled.  ", updatedAt);

    Assert.Equal("Felt controlled.", debrief.Note);
    Assert.Throws<ArgumentOutOfRangeException>(() => new SessionDebrief(sessionId, 11, null, updatedAt));
    Assert.Throws<ArgumentOutOfRangeException>(() => new SessionDebrief(sessionId, null, new string('x', 1_001), updatedAt));
  }

  [Fact]
  public void Rich_events_retain_typed_persistence_details()
  {
    var now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    SessionEvent[] events =
    [
      new WorkoutStepTransitionEvent(0, 1, "Tempo", now),
      new SessionPausedEvent(SessionPauseReason.PhysicalConsole, now),
      new SessionResumedEvent(now),
      new DeviceDisconnectedEvent(SessionDeviceRole.HeartRate, "Signal lost", now),
      new DeviceReconnectedEvent(SessionDeviceRole.HeartRate, now),
      new SessionWarningEvent("stale-hr", "Heart-rate automation suspended.", now),
      new ControlLeaseEvent(ControlLeaseEventKind.Acquired, Guid.NewGuid(), "phone", now),
      new SessionCompletedEvent(now),
      new SessionStoppedEvent(now),
      new SessionInterruptedEvent("Gateway restarted", now),
      new SessionFaultedEvent("simulator-fault", "Simulator faulted.", now)
    ];

    Assert.Equal(11, events.Select(static item => item.EventType).Distinct().Count());
    Assert.Equal("workout-step-transition", events[0].EventType);
  }

  [Fact]
  public void Active_snapshot_composes_existing_live_snapshot_and_session_context()
  {
    var live = new LiveSnapshot(
        DateTimeOffset.Parse("2026-08-02T10:00:00Z"),
        DeviceConnectionState.Ready,
        DeviceConnectionState.Ready,
        SessionState.Running,
        8,
        1,
        145,
        TimeSpan.FromMinutes(2),
        0.25,
        TimeSpan.FromMinutes(7.5),
        TimeSpan.Zero);
    var snapshot = new ActiveSessionSnapshot(
        Guid.NewGuid(),
        Guid.NewGuid(),
        "Runner",
        Guid.NewGuid(),
        "Tempo",
        live,
        12,
        new ActiveWorkoutStep(0, 2, "Warm up", null, 0.5, 8, 1),
        new ActiveWorkoutStep(1, 2, "Tempo", null, 0, 10, 2),
        TimeSpan.FromMinutes(8),
        8,
        8,
        1,
        1,
        new HeartRateTarget(140, 150, null),
        HeartRateSource.PolarH10,
        TimeSpan.FromSeconds(1),
        SessionControlAccess.Controller,
        DateTimeOffset.Parse("2026-08-02T10:00:15Z"),
        []);

    Assert.Same(live, snapshot.Live);
    Assert.Equal("Tempo", snapshot.NextStep?.Cue);
    Assert.Equal(SessionControlAccess.Controller, snapshot.ControlAccess);
  }

  private static PreflightSnapshot CreatePreflight(IReadOnlyList<PreflightCheck> checks) => new(
      DateTimeOffset.Parse("2026-08-02T10:00:00Z"),
      Guid.NewGuid(),
      "Runner",
      Guid.NewGuid(),
      "Easy",
      TimeSpan.FromMinutes(30),
      "Easy",
      false,
      HeartRateSource.None,
      checks);

  private static SessionSample CreateSample(
      long sequence = 1,
      double measuredSpeedKph = 8,
      string metricAlgorithmVersion = SessionMetricAlgorithms.EstimatedCaloriesV1) => new(
          Guid.NewGuid(),
          sequence,
          DateTimeOffset.Parse("2026-08-02T10:00:00Z"),
          TimeSpan.FromSeconds(sequence),
          8,
          8,
          measuredSpeedKph,
          1,
          1,
          1,
          140,
          0.1,
          10,
          TimeSpan.Zero,
          metricAlgorithmVersion);
}
