using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Live;
using TreadmillRunner.Gateway.Planning;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed record GarminActivityConnectRequest(string Email, string Password, bool Enabled = false, string WatchActivityHandling = GarminWatchActivityHandling.PreferWatch);
public sealed record GarminActivityMfaRequest(Guid ChallengeId, string Code);
public sealed record GarminActivitySettingsRequest(bool Enabled, string WatchActivityHandling, int ExpectedVersion);
public sealed record GarminActivityDisconnectRequest(int ExpectedVersion);
public sealed record GarminActivityTestRequest(Guid OperationId, int ExpectedVersion);
public sealed record GarminActivityFoundRequest(Guid OperationId);
public sealed record GarminActivityAbsentRetryRequest(Guid OperationId, string Confirmation);
public sealed record GarminActivityReprocessRequest(Guid OperationId);
public sealed record GarminActivityUploadStatusResponse(
  Guid ProfileId,
  bool Connected,
  bool Enabled,
  string? WatchActivityHandling,
  string? AccountLabel,
  string State,
  int Pending,
  int Confirmed,
  int FoundInGarmin,
  int Failed,
  int Unknown,
  int ReviewRequired,
  DateTimeOffset? LastSuccessAtUtc,
  string? LastError,
  int? Version,
  string AdapterState,
  string AdapterMessage,
  bool CanConnect);

public static class GarminActivityUploadEndpoints
{
  public static IEndpointRouteBuilder MapGarminActivityUpload(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/integrations/garmin/activity-upload");
    group.MapGet("/profiles/{profileId:guid}/status", GetStatusAsync);
    group.MapGet("/profiles/{profileId:guid}/jobs", GetJobsAsync);
    group.MapPost("/profiles/{profileId:guid}/connect", ConnectAsync);
    group.MapPost("/profiles/{profileId:guid}/mfa", CompleteMfaAsync);
    group.MapPost("/profiles/{profileId:guid}/settings", SetSettingsAsync);
    group.MapPost("/profiles/{profileId:guid}/disconnect", DisconnectAsync);
    group.MapPost("/profiles/{profileId:guid}/test-activity", CreateTestActivityAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/retry", RetryAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/dismiss", DismissAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/acknowledge-found", AcknowledgeFoundAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/confirm-absent-retry", ConfirmAbsentAndRetryAsync);
    group.MapPost("/profiles/{profileId:guid}/jobs/{jobId:guid}/reprocess-merge", ReprocessMergeAsync);
    return endpoints;
  }

  private static async Task<IResult> GetStatusAsync(
    Guid profileId,
    IGarminActivityUploadStore store,
    IGarminActivityAdapterReadiness readiness,
    CancellationToken cancellationToken) =>
    TypedResults.Ok(await StatusAsync(profileId, store, readiness, cancellationToken));

  private static async Task<IResult> GetJobsAsync(Guid profileId, IGarminActivityUploadStore store, CancellationToken cancellationToken) =>
    TypedResults.Ok(await store.ListJobsAsync(profileId, cancellationToken));

  private static async Task<IResult> ConnectAsync(
    Guid profileId,
    GarminActivityConnectRequest request,
    HttpContext context,
    ILiveSessionCoordinator sessions,
    IGarminActivityAdapterReadiness readiness,
    GarminActivityConnectionService service,
    CancellationToken cancellationToken)
  {
    if (!GarminCredentialTransportPolicy.IsAllowed(context))
      return Results.Json(new { error = "Garmin credentials over HTTP are accepted only from the NUC or a private household-network address. Use HTTPS for any other connection." }, statusCode: StatusCodes.Status426UpgradeRequired);
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Connect Garmin activity upload only while no run is active." });
    GarminAdapterReadiness adapter = await readiness.CheckAsync(cancellationToken);
    if (!adapter.CanConnect)
      return Results.Json(new { error = adapter.Message, adapterState = adapter.State }, statusCode: StatusCodes.Status503ServiceUnavailable);
    if (string.IsNullOrWhiteSpace(request.Email) || request.Email.Length > 254 || string.IsNullOrEmpty(request.Password) || request.Password.Length > 512)
      return TypedResults.BadRequest(new { error = "A bounded Garmin email and password are required for this one-time login." });
    if (!GarminWatchActivityHandling.IsValid(request.WatchActivityHandling))
      return TypedResults.BadRequest(new { error = "Choose prefer-watch or merge-and-replace handling." });
    try { return TypedResults.Ok(await service.BeginAsync(profileId, request.Email.Trim(), request.Password, request.Enabled, request.WatchActivityHandling, cancellationToken)); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (Exception) { return TypedResults.Conflict(new { error = "The unsupported Garmin provider could not authenticate. Verify the adapter installation and account details." }); }
  }

  private static async Task<IResult> CompleteMfaAsync(
    Guid profileId,
    GarminActivityMfaRequest request,
    HttpContext context,
    ILiveSessionCoordinator sessions,
    GarminActivityConnectionService service,
    CancellationToken cancellationToken)
  {
    if (!GarminCredentialTransportPolicy.IsAllowed(context))
      return Results.Json(new { error = "Garmin verification over HTTP is accepted only from the NUC or a private household-network address. Use HTTPS for any other connection." }, statusCode: StatusCodes.Status426UpgradeRequired);
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Complete Garmin login only while no run is active." });
    if (request.Code.Length is < 4 or > 16) return TypedResults.BadRequest(new { error = "A valid verification code is required." });
    try { return TypedResults.Ok(await service.CompleteMfaAsync(profileId, request.ChallengeId, request.Code, cancellationToken)); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (Exception) { return TypedResults.Conflict(new { error = "Garmin verification failed. Start the connection again." }); }
  }

  private static async Task<IResult> SetSettingsAsync(Guid profileId, GarminActivitySettingsRequest request, ILiveSessionCoordinator sessions, IGarminActivityUploadStore store, IGarminActivityAdapterReadiness readiness, GarminActivityUploadWorker worker, TimeProvider timeProvider, CancellationToken cancellationToken)
  {
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Change Garmin upload settings only while no run is active." });
    try
    {
      if (!GarminWatchActivityHandling.IsValid(request.WatchActivityHandling))
        return TypedResults.BadRequest(new { error = "Choose prefer-watch or merge-and-replace handling." });
      GarminActivityUploadAccount account = await store.SetSettingsAsync(profileId, request.Enabled, request.WatchActivityHandling, request.ExpectedVersion, timeProvider.GetUtcNow(), cancellationToken);
      if (account.Enabled) worker.Wake();
      return TypedResults.Ok(await StatusAsync(profileId, store, readiness, cancellationToken));
    }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> DisconnectAsync(Guid profileId, GarminActivityDisconnectRequest request, ILiveSessionCoordinator sessions, IGarminActivityUploadStore store, CancellationToken cancellationToken)
  {
    if (HasActiveRun(sessions)) return TypedResults.Conflict(new { error = "Disconnect Garmin upload only while no run is active." });
    try { return await store.DisconnectAsync(profileId, request.ExpectedVersion, cancellationToken) ? TypedResults.NoContent() : TypedResults.NotFound(); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> CreateTestActivityAsync(
    Guid profileId,
    GarminActivityTestRequest request,
    HttpContext context,
    ILiveSessionCoordinator liveSessions,
    IProfileStore profiles,
    IWorkoutStore workouts,
    ISessionStore sessions,
    IGarminActivityUploadStore uploads,
    IOperationReceiptStore receipts,
    GarminActivityUploadWorker worker,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (!GarminCredentialTransportPolicy.IsAllowed(context))
      return Results.Json(new { error = "Create a Garmin test activity only from the NUC, a private household-network address, or HTTPS." }, statusCode: StatusCodes.Status426UpgradeRequired);
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    if (HasActiveRun(liveSessions))
      return TypedResults.Conflict(new { error = "Create a Garmin test activity only while no run is active." });

    string requestFingerprint = PlanningOperationFingerprint.Compute(new { profileId, request.ExpectedVersion });
    OperationReceipt? completed = await receipts.FindAsync(request.OperationId, cancellationToken);
    if (completed is not null)
    {
      if (completed.OperationType != "garmin.activity.test" || completed.RequestFingerprint != requestFingerprint)
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
      return Results.Accepted($"/api/history/{request.OperationId:D}", new
      {
        sessionId = request.OperationId,
        message = "This synthetic Garmin test operation already completed and will not be queued again.",
      });
    }

    GarminActivityUploadStatus upload = await uploads.GetStatusAsync(profileId, cancellationToken);
    if (!upload.Connected || !upload.Enabled || upload.Version != request.ExpectedVersion)
      return TypedResults.Conflict(new { error = "Garmin activity upload must be connected, enabled, and at the expected profile version." });
    if (await sessions.FindAsync(request.OperationId, cancellationToken) is not null)
      return TypedResults.Conflict(new { error = "That test operation was already created. Review its existing Garmin job instead of sending it again." });

    VersionedUserProfile profile = await profiles.FindAsync(profileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Profile {profileId} was not found.");
    IReadOnlyList<StoredWorkout> availableWorkouts = await workouts.ListAsync(cancellationToken);
    StoredWorkout? sourceWorkout = availableWorkouts
      .FirstOrDefault(candidate => candidate.Kind == WorkoutKind.ManualTemplate && !candidate.IsArchived)
      ?? availableWorkouts.FirstOrDefault(candidate => !candidate.IsArchived);
    if (sourceWorkout is null)
      return TypedResults.Conflict(new { error = "A workout revision required for the test activity is unavailable." });

    const int durationSeconds = 60;
    const double speedKph = 4.5;
    const double inclinePercent = 0.5;
    const ushort heartRateBpm = 120;
    DateTimeOffset endedAt = timeProvider.GetUtcNow();
    DateTimeOffset startedAt = endedAt.AddSeconds(-durationSeconds);
    string configurationJson = JsonSerializer.Serialize(new SessionExecutionConfiguration(
      "GarminUploadTest",
      "Disabled",
      SessionProfileSnapshot.FromProfile(profile.Profile),
      "Synthetic test",
      "Simulated",
      "Synthetic"));
    var definition = new NewWorkoutSession(
      request.OperationId,
      profileId,
      profile.Profile.DisplayName,
      sourceWorkout.LatestRevisionId,
      "TreadmillRunner Garmin upload test",
      startedAt.AddSeconds(-1),
      configurationJson,
      SessionMetricAlgorithms.EstimatedCaloriesV1,
      new WorkoutSessionSelection(WorkoutSelectionSource.Manual),
      SessionOrigin.SystemTest);

    await sessions.CreateAsync(definition, cancellationToken);
    await sessions.MarkRunningAsync(request.OperationId, startedAt, cancellationToken);
    for (var second = 1; second <= durationSeconds; second++)
    {
      double distanceKilometers = speedKph * second / 3600d;
      await sessions.AppendSampleAsync(new SessionSample(
        request.OperationId,
        second - 1,
        startedAt.AddSeconds(second),
        TimeSpan.FromSeconds(second),
        speedKph,
        speedKph,
        speedKph,
        inclinePercent,
        inclinePercent,
        inclinePercent,
        heartRateBpm,
        distanceKilometers,
        0.1 * second,
        TimeSpan.Zero,
        SessionMetricAlgorithms.EstimatedCaloriesV1), cancellationToken);
    }
    await sessions.FinalizeAsync(new SessionSummary(
      request.OperationId,
      profileId,
      profile.Profile.DisplayName,
      sourceWorkout.LatestRevisionId,
      definition.WorkoutTitle,
      SessionState.Completed,
      startedAt,
      endedAt,
      TimeSpan.FromSeconds(durationSeconds),
      speedKph * durationSeconds / 3600d,
      6,
      heartRateBpm,
      heartRateBpm,
      speedKph,
      inclinePercent), cancellationToken);
    await uploads.EnqueueSystemTestAsync(profileId, request.OperationId, endedAt, cancellationToken);
    var receipt = new OperationReceipt(
      Guid.NewGuid(),
      request.OperationId,
      "garmin.activity.test",
      StatusCodes.Status202Accepted,
      JsonSerializer.Serialize(new { sessionId = request.OperationId }),
      endedAt,
      requestFingerprint);
    if (!await receipts.TryAddAsync(receipt, cancellationToken))
    {
      OperationReceipt? raced = await receipts.FindAsync(request.OperationId, cancellationToken);
      if (raced?.OperationType != receipt.OperationType || raced.RequestFingerprint != requestFingerprint)
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
    }
    worker.Wake();
    return Results.Accepted($"/api/history/{request.OperationId:D}", new
    {
      sessionId = request.OperationId,
      message = "A one-minute synthetic TreadmillRunner FIT activity was queued for Garmin. Review its job before any retry.",
    });
  }

  private static async Task<IResult> RetryAsync(Guid profileId, Guid jobId, IGarminActivityUploadStore store, GarminActivityUploadWorker worker, TimeProvider timeProvider, CancellationToken cancellationToken)
  {
    bool changed = await store.RetryFailedAsync(jobId, profileId, timeProvider.GetUtcNow(), cancellationToken);
    if (changed) worker.Wake();
    return changed ? Results.Accepted() : TypedResults.NotFound();
  }

  private static async Task<IResult> DismissAsync(Guid profileId, Guid jobId, IGarminActivityUploadStore store, TimeProvider timeProvider, CancellationToken cancellationToken) =>
    await store.DismissAsync(jobId, profileId, timeProvider.GetUtcNow(), cancellationToken) ? TypedResults.NoContent() : TypedResults.NotFound();

  private static async Task<IResult> AcknowledgeFoundAsync(
    Guid profileId,
    Guid jobId,
    GarminActivityFoundRequest request,
    IGarminActivityUploadStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    string fingerprint = PlanningOperationFingerprint.Compute(new { profileId, jobId });
    try
    {
      return TypedResults.Ok(await store.AcknowledgeFoundInGarminAsync(
        jobId,
        profileId,
        request.OperationId,
        fingerprint,
        timeProvider.GetUtcNow(),
        cancellationToken));
    }
    catch (OperationReplayException replay)
    {
      if (replay.Receipt.OperationType != "garmin.activity.found" || replay.Receipt.RequestFingerprint != fingerprint)
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
      GarminActivityUploadJob? job = JsonSerializer.Deserialize<GarminActivityUploadJob>(replay.Receipt.OutcomeJson);
      return job is null
        ? TypedResults.Conflict(new { error = "The completed Garmin acknowledgment could not be read." })
        : TypedResults.Ok(job);
    }
    catch (OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
    }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> ReprocessMergeAsync(
    Guid profileId,
    Guid jobId,
    GarminActivityReprocessRequest request,
    IGarminActivityUploadStore store,
    GarminActivityUploadWorker worker,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    string fingerprint = PlanningOperationFingerprint.Compute(new { profileId, jobId });
    try
    {
      GarminActivityUploadJob job = await store.ReprocessLegacyConfirmedForMergeAsync(
        jobId,
        profileId,
        request.OperationId,
        fingerprint,
        timeProvider.GetUtcNow(),
        cancellationToken);
      worker.Wake();
      return Results.Accepted(value: job);
    }
    catch (OperationReplayException replay)
    {
      if (replay.Receipt.OperationType != "garmin.activity.reprocess-merge" || replay.Receipt.RequestFingerprint != fingerprint)
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
      GarminActivityUploadJob? job = JsonSerializer.Deserialize<GarminActivityUploadJob>(replay.Receipt.OutcomeJson);
      return job is null
        ? TypedResults.Conflict(new { error = "The completed Garmin reprocess operation could not be read." })
        : Results.Accepted(value: job);
    }
    catch (OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
    }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static async Task<IResult> ConfirmAbsentAndRetryAsync(
    Guid profileId,
    Guid jobId,
    GarminActivityAbsentRetryRequest request,
    IGarminActivityUploadStore store,
    GarminActivityUploadWorker worker,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    if (request.OperationId == Guid.Empty)
      return TypedResults.BadRequest(new { error = "OperationId is required." });
    if (!string.Equals(request.Confirmation, "NOT FOUND", StringComparison.Ordinal))
      return TypedResults.BadRequest(new { error = "Confirmation must exactly equal NOT FOUND." });
    string fingerprint = PlanningOperationFingerprint.Compute(new { profileId, jobId });
    try
    {
      GarminActivityUploadJob job = await store.RetryUnknownVerifiedAbsentAsync(
        jobId,
        profileId,
        request.OperationId,
        fingerprint,
        timeProvider.GetUtcNow(),
        cancellationToken);
      worker.Wake();
      return Results.Accepted(value: job);
    }
    catch (OperationReplayException replay)
    {
      if (replay.Receipt.OperationType != "garmin.activity.absent-retry" || replay.Receipt.RequestFingerprint != fingerprint)
        return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
      GarminActivityUploadJob? job = JsonSerializer.Deserialize<GarminActivityUploadJob>(replay.Receipt.OutcomeJson);
      return job is null
        ? TypedResults.Conflict(new { error = "The completed Garmin retry acknowledgment could not be read." })
        : Results.Accepted(value: job);
    }
    catch (OperationScopeConflictException)
    {
      return TypedResults.Conflict(new { error = "That operation ID was already used for a different request." });
    }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { error = exception.Message }); }
    catch (InvalidOperationException exception) { return TypedResults.Conflict(new { error = exception.Message }); }
  }

  private static bool HasActiveRun(ILiveSessionCoordinator sessions) => sessions.CurrentSession?.Live.SessionState is
    SessionState.ArmedWaitingForPhysicalStart or SessionState.Running or SessionState.PausedWaitingForPhysicalResume;

  private static async Task<GarminActivityUploadStatusResponse> StatusAsync(
    Guid profileId,
    IGarminActivityUploadStore store,
    IGarminActivityAdapterReadiness readiness,
    CancellationToken cancellationToken)
  {
    GarminActivityUploadStatus status = await store.GetStatusAsync(profileId, cancellationToken);
    GarminAdapterReadiness adapter = await readiness.CheckAsync(cancellationToken);
    return new(
      status.ProfileId,
      status.Connected,
      status.Enabled,
      status.WatchActivityHandling,
      status.AccountLabel,
      status.State,
      status.Pending,
      status.Confirmed,
      status.FoundInGarmin,
      status.Failed,
      status.Unknown,
      status.ReviewRequired,
      status.LastSuccessAtUtc,
      status.LastError,
      status.Version,
      adapter.State,
      adapter.Message,
      adapter.CanConnect);
  }
}
