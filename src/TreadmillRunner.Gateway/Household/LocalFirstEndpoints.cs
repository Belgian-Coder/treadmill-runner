using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Household;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Devices;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Operations;
using TreadmillRunner.Gateway.Updates;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Household;

public sealed record CuePreferencesRequest(
  bool StepChanges,
  bool HeartRateDeparture,
  bool Halfway,
  bool ConnectionProblems,
  bool Completion,
  int VolumePercent);

public sealed record ExperiencePreferencesRequest(
  string DisplayStyle,
  IReadOnlyList<string> PrimaryMetrics,
  CuePreferencesRequest Cues,
  int? ExpectedVersion);

public sealed record LocalGoalRequest(
  Guid? Id,
  string Kind,
  string Period,
  double TargetValue,
  bool Enabled,
  int? ExpectedVersion);

public sealed record RecommendationRequest(Guid OperationId, Guid SessionId);
public sealed record RecommendationDecisionRequest(bool Accepted, int ExpectedVersion);
public sealed record BackupPolicyRequest(Guid? Id, string DestinationPath, int IntervalHours, int RetentionCount, bool Enabled, int? ExpectedVersion);

public static class LocalFirstEndpoints
{
  public static IEndpointRouteBuilder MapLocalFirstExperience(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/local-first");
    group.MapGet("/profiles/{profileId:guid}/preferences", GetPreferencesAsync);
    group.MapPut("/profiles/{profileId:guid}/preferences", SavePreferencesAsync);
    group.MapGet("/profiles/{profileId:guid}/goals", ListGoalsAsync);
    group.MapPut("/profiles/{profileId:guid}/goals", SaveGoalAsync);
    group.MapGet("/profiles/{profileId:guid}/insights", GetInsightsAsync);
    group.MapGet("/profiles/{profileId:guid}/comparisons/{sessionId:guid}", GetComparisonAsync);
    group.MapGet("/profiles/{profileId:guid}/recommendations", ListRecommendationsAsync);
    group.MapPost("/profiles/{profileId:guid}/recommendations", CreateRecommendationAsync);
    group.MapPost("/profiles/{profileId:guid}/recommendations/{id:guid}/decision", DecideRecommendationAsync);
    group.MapGet("/backup-policy", GetBackupPolicyAsync);
    group.MapPut("/backup-policy", SaveBackupPolicyAsync);
    group.MapGet("/backup-verifications", ListBackupVerificationsAsync);
    group.MapPost("/backup-verifications", VerifyBackupNowAsync);
    group.MapGet("/quick-start", GetQuickStartAsync);
    group.MapGet("/operations-summary", GetOperationsSummaryAsync);
    return endpoints;
  }

  private static async Task<IResult> GetPreferencesAsync(Guid profileId, ILocalFirstExperienceStore store, CancellationToken cancellationToken) =>
    await TryAsync(async () => Results.Ok(await store.GetPreferencesAsync(profileId, cancellationToken)));

  private static async Task<IResult> SavePreferencesAsync(
    Guid profileId,
    ExperiencePreferencesRequest request,
    ILocalFirstExperienceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    if (!Enum.TryParse(request.DisplayStyle, true, out LiveDisplayStyle style) || !Enum.IsDefined(style))
      throw new ArgumentException("DisplayStyle must be Balanced, LargeText, or HighContrast.");
    if (request.PrimaryMetrics is null || request.Cues is null) throw new ArgumentException("Metrics and cues are required.");
    LiveMetric[] metrics = request.PrimaryMetrics.Select(value =>
      Enum.TryParse(value, true, out LiveMetric metric) && Enum.IsDefined(metric)
        ? metric
        : throw new ArgumentException($"Unsupported live metric '{value}'.")).ToArray();
    var cues = new RunCuePreferences(
      request.Cues.StepChanges, request.Cues.HeartRateDeparture, request.Cues.Halfway,
      request.Cues.ConnectionProblems, request.Cues.Completion, request.Cues.VolumePercent);
    var preferences = new RunnerExperiencePreferences(style, metrics, cues);
    return Results.Ok(await store.SavePreferencesAsync(profileId, preferences, request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken));
  });

  private static async Task<IResult> ListGoalsAsync(Guid profileId, ILocalFirstExperienceStore store, CancellationToken cancellationToken) =>
    await TryAsync(async () => Results.Ok(await store.ListGoalsAsync(profileId, cancellationToken)));

  private static async Task<IResult> SaveGoalAsync(
    Guid profileId,
    LocalGoalRequest request,
    ILocalFirstExperienceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
      Results.Ok(await store.SaveGoalAsync(
        profileId, request.Id, request.Kind, request.Period, request.TargetValue, request.Enabled,
        request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken)));

  private static async Task<IResult> GetInsightsAsync(
    Guid profileId,
    ISessionStore sessionStore,
    ILocalFirstExperienceStore experienceStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    IReadOnlyList<SessionSummary> summaries = await sessionStore.ListSummariesAsync(profileId, 500, cancellationToken, includeSystemTests: false);
    LocalSessionFact[] facts = summaries.Select(summary => new LocalSessionFact(
      summary.SessionId,
      summary.UserProfileId,
      MapOrigin(summary.Origin),
      MapCompletion(summary.Status),
      summary.EndedAt,
      summary.Duration,
      summary.DistanceKilometers,
      summary.AverageHeartRateBpm is { } average ? (int?)Math.Round(average) : null,
      null,
      TelemetryComplete: summary.Duration == TimeSpan.Zero || summary.AverageSpeedKph > 0 || summary.DistanceKilometers > 0)).ToArray();
    LocalTrendSummary trends = LocalTrendCalculator.Calculate(profileId, facts);
    DateTimeOffset now = timeProvider.GetUtcNow();
    LocalTrendSummary weeklyTrends = LocalTrendCalculator.Calculate(profileId, facts.Where(item => item.EndedAtUtc >= now.AddDays(-7)));
    LocalTrendSummary monthlyTrends = LocalTrendCalculator.Calculate(profileId, facts.Where(item => item.EndedAtUtc >= now.AddDays(-30)));
    IReadOnlyList<LocalGoalDefinition> goals = await experienceStore.ListGoalsAsync(profileId, cancellationToken);
    IReadOnlyList<StoredProgressionRecommendation> recommendations = await experienceStore.ListRecommendationsAsync(profileId, cancellationToken);
    return Results.Ok(new { Trends = trends, WeeklyTrends = weeklyTrends, MonthlyTrends = monthlyTrends, Goals = goals, Recommendations = recommendations });
  });

  private static async Task<IResult> ListRecommendationsAsync(Guid profileId, ILocalFirstExperienceStore store, CancellationToken cancellationToken) =>
    await TryAsync(async () => Results.Ok(await store.ListRecommendationsAsync(profileId, cancellationToken)));

  private static async Task<IResult> GetComparisonAsync(
    Guid profileId,
    Guid sessionId,
    ISessionStore sessions,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    StoredWorkoutSession current = await sessions.FindAsync(sessionId, cancellationToken)
      ?? throw new KeyNotFoundException();
    if (current.Definition.UserProfileId != profileId) throw new KeyNotFoundException();
    IReadOnlyList<SessionSummary> summaries = await sessions.ListSummariesAsync(profileId, 500, cancellationToken, includeSystemTests: false);
    SessionSummary[] comparable = summaries
      .Where(item => item.SessionId != sessionId && item.WorkoutRevisionId == current.Definition.WorkoutRevisionId && item.Status == SessionState.Completed)
      .OrderByDescending(item => item.EndedAt)
      .Take(5)
      .ToArray();
    return Results.Ok(new
    {
      WorkoutRevisionId = current.Definition.WorkoutRevisionId,
      ComparisonCount = comparable.Length,
      Previous = comparable.FirstOrDefault(),
      Explanation = comparable.Length == 0
        ? "No earlier completed execution of this immutable workout revision is available."
        : "Compared with the most recent completed execution of the exact same immutable workout revision; missing telemetry is not estimated.",
    });
  });

  private static async Task<IResult> CreateRecommendationAsync(
    Guid profileId,
    RecommendationRequest request,
    ISessionStore sessionStore,
    ILocalFirstExperienceStore experienceStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    StoredWorkoutSession session = await sessionStore.FindAsync(request.SessionId, cancellationToken)
      ?? throw new KeyNotFoundException($"Session {request.SessionId} was not found.");
    if (session.Definition.UserProfileId != profileId) throw new KeyNotFoundException("Session was not found for this profile.");
    SessionAnalytics analytics = SessionAnalyticsCalculator.Calculate(
      request.SessionId, session.Samples, session.Events, ReadHeartRateZones(session.Definition.ControllerConfigurationJson));
    double heartRateCoverage = session.Samples.Count == 0
      ? 0
      : (double)session.Samples.Count(sample => sample.HeartRateBpm.HasValue) / session.Samples.Count * 100;
    bool interrupted = session.State is SessionState.Interrupted or SessionState.Faulted;
    var evidence = new ProgressionEvidence(
      profileId, request.SessionId, MapCompletion(session.State), analytics.AdherencePercentage,
      session.Debrief?.PerceivedExertion, heartRateCoverage,
      TelemetryComplete: session.Samples.Count > 1 && !session.Events.Any(item => item is DeviceDisconnectedEvent),
      WasInterrupted: interrupted, MissedScheduledSessions: 0);
    ProgressionRecommendation recommendation = LocalProgressionAdviser.Recommend(evidence);
    StoredProgressionRecommendation saved = await experienceStore.SaveRecommendationAsync(
      request.OperationId, profileId, request.SessionId, evidence, recommendation, timeProvider.GetUtcNow(), cancellationToken);
    return Results.Ok(saved);
  });

  private static async Task<IResult> DecideRecommendationAsync(
    Guid profileId,
    Guid id,
    RecommendationDecisionRequest request,
    ILocalFirstExperienceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
      Results.Ok(await store.DecideRecommendationAsync(id, profileId, request.Accepted, request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken)));

  private static async Task<IResult> GetBackupPolicyAsync(ILocalFirstExperienceStore store, CancellationToken cancellationToken) =>
    await TryAsync(async () => await store.GetBackupPolicyAsync(cancellationToken) is { } policy ? Results.Ok(policy) : Results.NoContent());

  private static async Task<IResult> SaveBackupPolicyAsync(
    BackupPolicyRequest request,
    ILocalFirstExperienceStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    var policy = new LocalBackupPolicy(request.DestinationPath, request.IntervalHours, request.RetentionCount, request.Enabled);
    return Results.Ok(await store.SaveBackupPolicyAsync(request.Id, policy, request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken));
  });

  private static async Task<IResult> ListBackupVerificationsAsync(ILocalFirstExperienceStore store, CancellationToken cancellationToken, int take = 20) =>
    await TryAsync(async () => Results.Ok(await store.ListBackupVerificationsAsync(take, cancellationToken)));

  private static async Task<IResult> VerifyBackupNowAsync(ILocalBackupCoordinator coordinator, CancellationToken cancellationToken) =>
    await TryAsync(async () => Results.Ok(await coordinator.VerifyNowAsync(cancellationToken)));

  private static async Task<IResult> GetQuickStartAsync(
    IDeviceEnrollmentStore enrollments,
    IProfileStore profiles,
    IReadOnlyDeviceCoordinator devices,
    ILiveSessionCoordinator live,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => await TryAsync(async () =>
  {
    IReadOnlyList<TreadmillRunner.Core.Devices.HeartRateDeviceAssignment> assignments = await enrollments.ListHeartRateAssignmentsAsync(cancellationToken);
    IReadOnlyList<VersionedDeviceEnrollment> enrolled = await enrollments.ListActiveAsync(cancellationToken);
    var labels = enrolled.ToDictionary(item => item.Enrollment.Id, item => item.Enrollment.DisplayName);
    DateTimeOffset now = timeProvider.GetUtcNow();
    AssignedHeartRateObservation[] observations = assignments.Select(assignment =>
    {
      TreadmillRunner.Core.Devices.DeviceTelemetrySnapshot snapshot = devices.CurrentForProfile(assignment.UserProfileId);
      bool selected = snapshot.SelectedHeartRateEnrollmentId == assignment.DeviceEnrollmentId;
      bool fresh = selected && snapshot.HeartRateObservedAt is { } observed && now - observed <= TimeSpan.FromSeconds(5);
      return new AssignedHeartRateObservation(
        assignment.UserProfileId,
        labels.GetValueOrDefault(assignment.DeviceEnrollmentId, "assigned heart-rate sensor"),
        fresh,
        snapshot.HeartRateObservedAt ?? DateTimeOffset.UnixEpoch);
    }).ToArray();
    Guid? activeProfileId = live.CurrentSession is { Live.SessionState: not (SessionState.Completed or SessionState.Stopped or SessionState.Interrupted or SessionState.Faulted) } active
      ? active.UserProfileId : null;
    QuickStartSuggestion suggestion = RunnerQuickStartAdvisor.Suggest(activeProfileId, observations);
    string? profileName = suggestion.SuggestedProfileId is { } profileId
      ? (await profiles.FindAsync(profileId, cancellationToken))?.Profile.DisplayName
      : null;
    return Results.Ok(new { Suggestion = suggestion, ProfileName = profileName });
  });

  private static async Task<IResult> GetOperationsSummaryAsync(
    IDatabaseIntegrityCoordinator database,
    IReadOnlyDeviceCoordinator devices,
    UpdateManager updates,
    ILocalFirstExperienceStore store,
    CancellationToken cancellationToken)
  {
    StoredBackupVerification? backup = (await store.ListBackupVerificationsAsync(1, cancellationToken)).FirstOrDefault();
    var components = new[]
    {
      new LocalHealthComponent("Service", LocalHealthState.Healthy, "The local gateway is responding."),
      new LocalHealthComponent("Database", database.Current.RecoveryRequired ? LocalHealthState.ActionRequired : database.Current.State.ToString() == "Healthy" ? LocalHealthState.Healthy : LocalHealthState.Degraded, database.Current.Message),
      new LocalHealthComponent("BLE", devices.Current.Treadmill.State.ToString() == "Connected" ? LocalHealthState.Healthy : LocalHealthState.Degraded, $"Treadmill {devices.Current.Treadmill.State}; heart rate {devices.Current.HeartRate.State}."),
      new LocalHealthComponent("Storage", backup?.Status == "Verified" ? LocalHealthState.Healthy : backup is null ? LocalHealthState.Degraded : LocalHealthState.ActionRequired, backup?.Detail ?? "No owner-selected backup has been verified yet."),
      new LocalHealthComponent("Release", updates.Status.State.ToString() is "Failed" or "RollbackFailed" ? LocalHealthState.ActionRequired : LocalHealthState.Healthy, updates.Status.Message),
    };
    OperationsHealthSummary summary = OperationsHealthAggregator.Combine(components);
    return Results.Ok(new
    {
      State = summary.State.ToString(),
      Components = summary.Components.Select(static component => new
      {
        component.Id,
        State = component.State.ToString(),
        component.Detail,
      }),
    });
  }

  private static async Task<IResult> TryAsync(Func<Task<IResult>> action)
  {
    try { return await action(); }
    catch (KeyNotFoundException) { return Results.NotFound(); }
    catch (DbUpdateConcurrencyException exception) { return Results.Conflict(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return Results.Conflict(new { error = exception.Message }); }
    catch (ArgumentException exception) { return Results.BadRequest(new { error = exception.Message }); }
    catch (JsonException) { return Results.Problem("Stored local-first data could not be read.", statusCode: 500); }
  }

  private static LocalSessionOrigin MapOrigin(SessionOrigin origin) => origin switch
  {
    SessionOrigin.Hardware => LocalSessionOrigin.Hardware,
    SessionOrigin.Simulator => LocalSessionOrigin.Simulator,
    SessionOrigin.SystemTest => LocalSessionOrigin.SystemTest,
    _ => LocalSessionOrigin.Imported,
  };

  private static SessionCompletion MapCompletion(SessionState state) => state switch
  {
    SessionState.Completed => SessionCompletion.Completed,
    SessionState.Interrupted or SessionState.Faulted => SessionCompletion.Interrupted,
    _ => SessionCompletion.Completed,
  };

  private static IReadOnlyList<TreadmillRunner.Core.Profiles.HeartRateZone> ReadHeartRateZones(string json)
  {
    try
    {
      SessionExecutionConfiguration? configuration = JsonSerializer.Deserialize<SessionExecutionConfiguration>(
        json, new JsonSerializerOptions(JsonSerializerDefaults.Web));
      return configuration?.Profile.HeartRateZones.Select(static zone => zone.ToHeartRateZone()).ToArray() ?? [];
    }
    catch (JsonException) { return []; }
  }
}
