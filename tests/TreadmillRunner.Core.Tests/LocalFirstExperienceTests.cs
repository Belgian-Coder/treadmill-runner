using TreadmillRunner.Core.Household;

namespace TreadmillRunner.Core.Tests;

public sealed class LocalFirstExperienceTests
{
  [Fact]
  public void Readiness_separates_record_manual_and_planned_control()
  {
    RunReadinessAssessment assessment = RunReadinessClassifier.Classify(new(
      ProfileSelected: true,
      WorkoutSelected: true,
      DatabaseReady: true,
      TreadmillFresh: true,
      HeartRateRequired: true,
      HeartRateFresh: false,
      ManualControlAvailable: true,
      PlannedAutomationAllowed: true,
      HasUnknownCommandOutcome: false));

    Assert.True(assessment.CanRecord);
    Assert.True(assessment.CanUseManualControl);
    Assert.False(assessment.CanUsePlannedAutomation);
    Assert.Contains("heart rate", assessment.PlannedAutomationReason, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Unknown_command_outcome_blocks_control_but_not_local_recording()
  {
    RunReadinessAssessment assessment = RunReadinessClassifier.Classify(new(
      true, true, true, true, false, false, true, true, true));

    Assert.True(assessment.CanRecord);
    Assert.False(assessment.CanUseManualControl);
    Assert.False(assessment.CanUsePlannedAutomation);
  }

  [Fact]
  public void Preferences_require_two_or_three_distinct_metrics_and_bounded_volume()
  {
    var preferences = new RunnerExperiencePreferences(
      LiveDisplayStyle.HighContrast,
      [LiveMetric.HeartRate, LiveMetric.ElapsedTime, LiveMetric.Speed],
      new RunCuePreferences(true, true, true, true, true, 65));

    Assert.Equal(3, preferences.PrimaryMetrics.Count);
    Assert.Throws<ArgumentException>(() => new RunnerExperiencePreferences(
      LiveDisplayStyle.LargeText,
      [LiveMetric.Speed, LiveMetric.Speed],
      RunCuePreferences.Default));
    Assert.Throws<ArgumentOutOfRangeException>(() => new RunCuePreferences(true, true, true, true, true, 101));
  }

  [Theory]
  [InlineData(95, 5, true, ProgressionAction.Advance)]
  [InlineData(62, 8, true, ProgressionAction.Reduce)]
  [InlineData(95, 9, true, ProgressionAction.Reduce)]
  [InlineData(90, 6, false, ProgressionAction.Repeat)]
  public void Adviser_is_deterministic_and_conservative(
    double adherence,
    int rpe,
    bool telemetryComplete,
    ProgressionAction expected)
  {
    ProgressionRecommendation recommendation = LocalProgressionAdviser.Recommend(new(
      Guid.NewGuid(), Guid.NewGuid(), SessionCompletion.Completed, adherence, rpe,
      HeartRateCoveragePercentage: 90,
      TelemetryComplete: telemetryComplete,
      WasInterrupted: false,
      MissedScheduledSessions: 0));

    Assert.Equal(expected, recommendation.Action);
    Assert.Equal(LocalProgressionAdviser.AlgorithmVersion, recommendation.AlgorithmVersion);
    Assert.False(string.IsNullOrWhiteSpace(recommendation.Reason));
  }

  [Fact]
  public void Trends_exclude_simulator_and_system_tests_and_label_incomplete_data()
  {
    Guid profileId = Guid.NewGuid();
    LocalTrendSummary summary = LocalTrendCalculator.Calculate(profileId, [
      Fact(profileId, LocalSessionOrigin.Hardware, 5, 30, 160, complete: true),
      Fact(profileId, LocalSessionOrigin.Hardware, 4, 28, 155, complete: false),
      Fact(profileId, LocalSessionOrigin.Simulator, 99, 99, 999, complete: true),
      Fact(profileId, LocalSessionOrigin.SystemTest, 99, 99, 999, complete: true),
    ]);

    Assert.Equal(2, summary.CompletedSessions);
    Assert.Equal(9, summary.DistanceKilometers);
    Assert.Equal(1, summary.IncompleteTelemetrySessions);
    Assert.Equal(5, summary.LongestDistanceKilometers);
  }

  [Fact]
  public void Quick_start_requires_one_fresh_assigned_sensor_and_never_switches_active_session()
  {
    Guid runner = Guid.NewGuid();
    QuickStartSuggestion suggestion = RunnerQuickStartAdvisor.Suggest(null, [
      new AssignedHeartRateObservation(runner, "Polar H10", true, DateTimeOffset.UtcNow),
    ]);
    Assert.Equal(runner, suggestion.SuggestedProfileId);
    Assert.True(suggestion.RequiresConfirmation);

    Guid activeRunner = Guid.NewGuid();
    QuickStartSuggestion active = RunnerQuickStartAdvisor.Suggest(activeRunner, [
      new AssignedHeartRateObservation(runner, "Polar H10", true, DateTimeOffset.UtcNow),
    ]);
    Assert.Null(active.SuggestedProfileId);
    Assert.Contains("active", active.Reason, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Backup_policy_accepts_local_or_unc_paths_and_rejects_remote_urls()
  {
    var local = new LocalBackupPolicy(@"D:\TreadmillRunnerBackups", 24, 14, true);
    var unc = new LocalBackupPolicy(@"\\household-nas\backups\treadmill", 12, 7, true);

    Assert.True(local.Enabled);
    Assert.True(unc.Enabled);
    Assert.Throws<ArgumentException>(() => new LocalBackupPolicy("https://example.test/backups", 24, 14, true));
  }

  [Fact]
  public void Operations_health_reports_the_worst_local_component()
  {
    OperationsHealthSummary summary = OperationsHealthAggregator.Combine([
      new LocalHealthComponent("database", LocalHealthState.Healthy, "Verified"),
      new LocalHealthComponent("ble", LocalHealthState.Degraded, "Treadmill offline"),
      new LocalHealthComponent("backup", LocalHealthState.ActionRequired, "Last verification failed"),
    ]);

    Assert.Equal(LocalHealthState.ActionRequired, summary.State);
    Assert.Equal(3, summary.Components.Count);
  }

  private static LocalSessionFact Fact(
    Guid profileId,
    LocalSessionOrigin origin,
    double distance,
    double minutes,
    int averageHeartRate,
    bool complete) => new(
      Guid.NewGuid(), profileId, origin, SessionCompletion.Completed,
      DateTimeOffset.UtcNow, TimeSpan.FromMinutes(minutes), distance,
      averageHeartRate, 90, complete);
}
