using System.Text.Json;
using TreadmillRunner.Core.Devices;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Planning;

public static class PremadePlanPlanningEndpoints
{
  private const string OperationType = "premade-plan.materialize";
  private static readonly JsonSerializerOptions WebJsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapPremadePlans(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/premade-plans");
    group.MapGet("/", ListAsync);
    group.MapGet("/{templateId}/preview", PreviewAsync);
    group.MapPost("/materialize", MaterializeAsync);
    return endpoints;
  }

  private static async Task<IResult> ListAsync(
    Guid? profileId,
    IPremadePlanStore store,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<PremadePlanInstallation> installations = profileId is { } id && id != Guid.Empty
      ? await store.ListAsync(id, cancellationToken)
      : [];
    return TypedResults.Ok(PremadePlanCatalog.All.Select(template => ToCatalogDto(template, installations)).ToArray());
  }

  private static async Task<IResult> PreviewAsync(
    string templateId,
    Guid profileId,
    string? version,
    IProfileStore profileStore,
    IDeviceEnrollmentStore enrollmentStore,
    IPremadePlanStore planStore,
    CancellationToken cancellationToken)
  {
    if (profileId == Guid.Empty) return Validation("Profile ID is required.");
    PremadePlanTemplate template;
    try { template = PremadePlanCatalog.Find(templateId, version); }
    catch (KeyNotFoundException) { return TypedResults.NotFound(); }
    VersionedUserProfile? storedProfile = await profileStore.FindAsync(profileId, cancellationToken);
    if (storedProfile is null || storedProfile.IsArchived) return TypedResults.NotFound();
    VersionedDeviceEnrollment? treadmill = await enrollmentStore.FindActiveAsync(DeviceRole.Treadmill, cancellationToken);
    PreparedPlan prepared = Prepare(template, storedProfile.Profile, treadmill?.Enrollment.Capabilities);
    IReadOnlyList<PremadePlanInstallation> installations = await planStore.ListAsync(profileId, cancellationToken);
    return TypedResults.Ok(ToPreviewDto(template, storedProfile.Profile, prepared, installations));
  }

  private static async Task<IResult> MaterializeAsync(
    PremadePlanMaterializeRequest request,
    IProfileStore profileStore,
    IDeviceEnrollmentStore enrollmentStore,
    IPremadePlanStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      if (request.OperationId == Guid.Empty) throw new ArgumentException("OperationId is required.");
      if (request.ProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.");
      PremadePlanTemplate template = PremadePlanCatalog.Find(request.TemplateId, request.TemplateVersion);
      fingerprint = PlanningOperationFingerprint.Compute(new
      {
        request.ProfileId,
        request.TemplateId,
        request.TemplateVersion,
        request.FreshCopy,
        template.ContentSha256,
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
        return Replay(receipt, fingerprint);
      VersionedUserProfile profile = await profileStore.FindAsync(request.ProfileId, cancellationToken)
        ?? throw new KeyNotFoundException("Profile was not found.");
      if (profile.IsArchived) throw new KeyNotFoundException("Profile was not found.");
      VersionedDeviceEnrollment? treadmill = await enrollmentStore.FindActiveAsync(DeviceRole.Treadmill, cancellationToken);
      PreparedPlan prepared = Prepare(template, profile.Profile, treadmill?.Enrollment.Capabilities);
      if (!prepared.Compatible) return Validation(prepared.CompatibilityMessage);
      DateTimeOffset now = timeProvider.GetUtcNow();
      var operation = new PersistenceWriteOperation(
        request.OperationId,
        OperationType,
        201,
        "{}",
        now,
        fingerprint);
      PremadePlanMaterializationResult stored = await store.MaterializeAsync(
        new PremadePlanMaterialization(template, request.ProfileId, prepared.WorkoutsByKey, request.FreshCopy),
        operation,
        cancellationToken);
      PremadePlanMaterializeDto response = ToDto(stored);
      return stored.AlreadyAdded
        ? TypedResults.Ok(response)
        : TypedResults.Created($"/api/planning/programs/{stored.Installation.WorkoutProgramId}", response);
    }
    catch (KeyNotFoundException) { return TypedResults.NotFound(); }
    catch (ArgumentException exception) { return Validation(exception.Message); }
    catch (OperationReplayException replay) { return Replay(replay.Receipt, fingerprint); }
    catch (OperationScopeConflictException) { return TypedResults.Conflict(new { message = "OperationId was already used for another request." }); }
  }

  private static PreparedPlan Prepare(
    PremadePlanTemplate template,
    UserProfile profile,
    TreadmillCapabilities? capabilities)
  {
    bool heartRateReady = !template.RequiresHeartRate || template.Sessions
      .Where(static session => session.HeartRateZoneNumber is not null)
      .All(session => profile.HeartRateZones.Any(zone => zone.Number == session.HeartRateZoneNumber));
    var workouts = new Dictionary<string, WorkoutDefinition>(StringComparer.Ordinal);
    var targets = new List<WorkoutTargetEvaluation>();
    foreach (PremadePlanSessionTemplate session in template.Sessions)
    {
      if (workouts.ContainsKey(session.WorkoutKey)) continue;
      WorkoutCapabilityResult result = WorkoutCapabilityPolicy.Evaluate(
        PremadePlanCatalog.BuildWorkout(session),
        capabilities?.SpeedRange,
        capabilities?.InclineRange,
        profile.MaximumSpeedKph);
      workouts.Add(session.WorkoutKey, result.Definition);
      targets.AddRange(result.Targets);
    }
    bool validTargets = targets.All(static target => target.Disposition != WorkoutTargetDisposition.Rejected);
    string message = !heartRateReady
      ? "This plan needs runner heart-rate zones Z1–Z5 before it can be added."
      : !validTargets
        ? "One or more targets cannot fit the selected runner and verified treadmill limits."
        : capabilities is null
          ? "Runner limits are compatible; verified treadmill ranges are not enrolled yet."
          : "Targets fit the selected runner and verified treadmill ranges.";
    return new PreparedPlan(workouts, targets, heartRateReady && validTargets, heartRateReady, message);
  }

  private static PremadePlanPreviewDto ToPreviewDto(
    PremadePlanTemplate template,
    UserProfile profile,
    PreparedPlan prepared,
    IReadOnlyList<PremadePlanInstallation> installations)
  {
    double maximumSpeed = prepared.Targets.Where(static target => target.Kind == WorkoutTargetKind.Speed && target.Normalized is not null)
      .Select(static target => target.Normalized!.Value).DefaultIfEmpty(template.MaximumSpeedKph).Max();
    double maximumIncline = prepared.Targets.Where(static target => target.Kind == WorkoutTargetKind.Incline && target.Normalized is not null)
      .Select(static target => target.Normalized!.Value).DefaultIfEmpty(template.MaximumInclinePercent).Max();
    PremadePlanPhaseDto[] phases = template.Sessions.GroupBy(static session => session.Phase)
      .Select(group => new PremadePlanPhaseDto(
        group.Key,
        group.Min(static session => session.WeekNumber),
        group.Max(static session => session.WeekNumber),
        group.Count()))
      .ToArray();
    return new PremadePlanPreviewDto(
      ToCatalogDto(template, installations),
      profile.Id,
      profile.DisplayName,
      prepared.Compatible,
      prepared.CompatibilityMessage,
      prepared.HeartRateReady,
      maximumSpeed,
      maximumIncline,
      prepared.Targets.Count(static target => target.Disposition == WorkoutTargetDisposition.Normalized),
      prepared.Targets.Count(static target => target.Disposition == WorkoutTargetDisposition.Rejected),
      prepared.WorkoutsByKey.Count,
      phases);
  }

  private static PremadePlanCatalogDto ToCatalogDto(
    PremadePlanTemplate template,
    IReadOnlyList<PremadePlanInstallation> installations)
  {
    PremadePlanInstallation[] matching = installations.Where(installation =>
      installation.TemplateId == template.Id && installation.TemplateVersion == template.Version).ToArray();
    return new PremadePlanCatalogDto(
      template.Id,
      template.Version,
      template.Name,
      template.Description,
      template.Goal,
      template.Experience,
      template.Weeks,
      template.SessionsPerWeek,
      template.SessionCount,
      template.MaximumDurationMinutes,
      template.MaximumSpeedKph,
      template.MaximumInclinePercent,
      template.Repeatable,
      template.RequiresHeartRate,
      template.Tags.Order(StringComparer.Ordinal).ToArray(),
      matching.Length > 0,
      matching.Length);
  }

  private static PremadePlanMaterializeDto ToDto(PremadePlanMaterializationResult result) => new(
    result.Installation.Id,
    result.Installation.WorkoutProgramId,
    result.WorkoutProgramRevisionId,
    result.Installation.TemplateId,
    result.Installation.TemplateVersion,
    result.Installation.CopyNumber,
    result.PositionCount,
    result.UniqueWorkoutCount,
    result.AlreadyAdded,
    result.Replayed);

  private static IResult Replay(OperationReceipt receipt, string fingerprint)
  {
    if (receipt.OperationType != OperationType || receipt.RequestFingerprint != fingerprint)
      return TypedResults.Conflict(new { message = "OperationId was already used for another request." });
    PremadePlanMaterializationResult stored = JsonSerializer.Deserialize<PremadePlanMaterializationResult>(receipt.OutcomeJson, WebJsonOptions)
      ?? throw new InvalidOperationException("Stored premade-plan result is invalid.");
    return Results.Json(ToDto(stored with { Replayed = true }), statusCode: receipt.StatusCode);
  }

  private static IResult Validation(string message) => TypedResults.BadRequest(new { message });

  private sealed record PreparedPlan(
    IReadOnlyDictionary<string, WorkoutDefinition> WorkoutsByKey,
    IReadOnlyList<WorkoutTargetEvaluation> Targets,
    bool Compatible,
    bool HeartRateReady,
    string CompatibilityMessage);
}
