using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Planning;

public static class ProfilePlanningEndpoints
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
  public static IEndpointRouteBuilder MapProfilePlanning(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/profiles");
    group.MapGet("/", ListAsync);
    group.MapGet("/{id:guid}", FindAsync);
    group.MapPost("/", CreateAsync);
    group.MapPut("/{id:guid}", UpdateAsync);
    group.MapPost("/{id:guid}/archive", ArchiveAsync);
    return endpoints;
  }

  private static async Task<IResult> ListAsync(IProfileStore store, CancellationToken cancellationToken)
  {
    IReadOnlyList<VersionedUserProfile> profiles = await store.ListAsync(cancellationToken);
    return TypedResults.Ok(profiles.Where(static profile => !profile.IsArchived).Select(ToDto).ToArray());
  }

  private static async Task<IResult> FindAsync(Guid id, IProfileStore store, CancellationToken cancellationToken)
  {
    VersionedUserProfile? profile = await store.FindAsync(id, cancellationToken);
    return profile is null || profile.IsArchived ? TypedResults.NotFound() : TypedResults.Ok(ToDto(profile));
  }

  private static async Task<IResult> CreateAsync(
    ProfileUpsertRequest request,
    IProfileStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      UserProfile profile = CreateProfile(Guid.NewGuid(), request);
      requestFingerprint = ProfileFingerprint(targetProfileId: null, request);
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "profile.create", requestFingerprint);
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      var expected = new VersionedUserProfile(profile, 1, false, null);
      VersionedUserProfile saved = await store.CreateAsync(
        profile,
        now,
        WriteOperation(request.OperationId, "profile.create", StatusCodes.Status201Created, ToDto(expected), now, requestFingerprint),
        cancellationToken);
      return TypedResults.Created($"/api/planning/profiles/{saved.Profile.Id}", ToDto(saved));
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateException)
    {
      return TypedResults.Conflict(new { message = "A profile with that name already exists." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "profile.create", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> UpdateAsync(
    Guid id,
    ProfileUpsertRequest request,
    IProfileStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      if (request.ExpectedVersion is not > 0)
      {
        throw new ArgumentException("ExpectedVersion is required for profile updates.");
      }

      UserProfile profile = CreateProfile(id, request);
      requestFingerprint = ProfileFingerprint(id, request);
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "profile.update", requestFingerprint);
      }

      VersionedUserProfile? current = await store.FindAsync(id, cancellationToken);
      if (current is null || current.IsArchived)
      {
        return TypedResults.NotFound();
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      var expected = new VersionedUserProfile(profile, request.ExpectedVersion.Value + 1, false, null);
      VersionedUserProfile saved = await store.UpdateAsync(
        profile,
        request.ExpectedVersion.Value,
        now,
        WriteOperation(request.OperationId, "profile.update", StatusCodes.Status200OK, ToDto(expected), now, requestFingerprint),
        cancellationToken);
      return TypedResults.Ok(ToDto(saved));
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The profile changed in another client. Reload and try again." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "profile.update", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> ArchiveAsync(
    Guid id,
    ArchiveProfileRequest request,
    IProfileStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      if (request.ExpectedVersion < 1)
      {
        throw new ArgumentException("ExpectedVersion is required to archive a profile.");
      }

      requestFingerprint = PlanningOperationFingerprint.Compute(new { ProfileId = id, request.ExpectedVersion });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "profile.archive", requestFingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      bool archived = await store.SetArchivedAsync(
        id,
        isArchived: true,
        request.ExpectedVersion,
        now,
        WriteOperation(request.OperationId, "profile.archive", StatusCodes.Status204NoContent, new { }, now, requestFingerprint),
        cancellationToken);
      return archived ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The profile changed in another client. Reload and try again." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "profile.archive", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static UserProfile CreateProfile(
    Guid id,
    ProfileUpsertRequest request)
  {
    if (request.HeartRateZones is null)
    {
      throw new ArgumentException("HeartRateZones cannot be null.");
    }

    if (!string.Equals(request.UnitSystem, nameof(UnitSystem.Metric), StringComparison.Ordinal))
    {
      throw new ArgumentException("UnitSystem must be Metric.");
    }

    return new UserProfile(
      id,
      request.DisplayName,
      UnitSystem.Metric,
      request.WeightKilograms,
      request.MaximumHeartRateBpm,
      request.MaximumSpeedKph,
      request.HeartRateZones.Select(CreateHeartRateZone).ToArray(),
      new HeartRateControllerSettings(
        request.HeartRateIncreaseStepKph ?? HeartRateControllerSettings.Default.IncreaseStepKph,
        request.HeartRateIncreaseCooldownSeconds ?? HeartRateControllerSettings.Default.IncreaseCooldownSeconds,
        request.HeartRateDecreaseStepKph ?? HeartRateControllerSettings.Default.DecreaseStepKph,
        request.HeartRateDecreaseCooldownSeconds ?? HeartRateControllerSettings.Default.DecreaseCooldownSeconds));
  }

  private static HeartRateZone CreateHeartRateZone(HeartRateZoneDto? zone)
  {
    if (zone is null)
    {
      throw new ArgumentException("HeartRateZones cannot contain null zones.");
    }

    return new HeartRateZone(zone.Number, zone.Name, zone.MinimumBpm, zone.MaximumBpm);
  }

  private static ProfileDto ToDto(VersionedUserProfile profile) => new(
    profile.Profile.Id,
    profile.Profile.DisplayName,
    nameof(UnitSystem.Metric),
    profile.Profile.WeightKilograms,
    profile.Profile.MaximumHeartRateBpm,
    profile.Profile.MaximumSpeedKph,
    profile.Profile.HeartRateController.IncreaseStepKph,
    profile.Profile.HeartRateController.IncreaseCooldownSeconds,
    profile.Profile.HeartRateController.DecreaseStepKph,
    profile.Profile.HeartRateController.DecreaseCooldownSeconds,
    profile.Version,
    profile.Profile.HeartRateZones
      .Select(static zone => new HeartRateZoneDto(zone.Number, zone.Name, zone.MinimumBpm, zone.MaximumBpm))
      .ToArray());

  private static void ValidateOperationId(Guid operationId)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("OperationId cannot be empty.");
    }
  }

  private static IResult Validation(ArgumentException exception) => TypedResults.ValidationProblem(
    new Dictionary<string, string[]> { ["request"] = [exception.Message] });

  private static string ProfileFingerprint(Guid? targetProfileId, ProfileUpsertRequest request) =>
    PlanningOperationFingerprint.Compute(new
    {
      TargetProfileId = targetProfileId,
      request.DisplayName,
      request.UnitSystem,
      request.WeightKilograms,
      request.MaximumHeartRateBpm,
      request.MaximumSpeedKph,
      request.HeartRateZones,
      request.HeartRateIncreaseStepKph,
      request.HeartRateIncreaseCooldownSeconds,
      request.HeartRateDecreaseStepKph,
      request.HeartRateDecreaseCooldownSeconds,
      request.ExpectedVersion,
    });

  private static PersistenceWriteOperation WriteOperation(
    Guid id,
    string type,
    int statusCode,
    object outcome,
    DateTimeOffset now,
    string requestFingerprint) => new(
      id, type, statusCode, JsonSerializer.Serialize(outcome, JsonOptions), now, requestFingerprint);

  private static IResult Replay(OperationReplayException replay, string expectedType, string requestFingerprint) =>
    Replay(replay.Receipt, expectedType, requestFingerprint);

  private static IResult Replay(OperationReceipt receipt, string expectedType, string requestFingerprint) =>
    receipt.OperationType == expectedType && receipt.RequestFingerprint == requestFingerprint
      ? Results.Content(receipt.OutcomeJson, "application/json", statusCode: receipt.StatusCode)
      : OperationConflict();

  private static IResult OperationConflict() =>
    TypedResults.Conflict(new { message = "That operation ID was already used for another action or request." });
}
