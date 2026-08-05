using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.Mvc;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Gateway.Planning;

public static class WorkoutPlanningEndpoints
{
  private const int MultipartRequestOverheadBytes = 64 * 1024;
  private const long MaximumImportRequestBytes = WorkoutImportLimits.MaximumBytes + MultipartRequestOverheadBytes;

  public static IEndpointRouteBuilder MapWorkoutPlanning(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/workouts");
    group.MapGet("/", ListAsync);
    group.MapGet("/reuse", ListReuseAsync);
    group.MapGet("/{id:guid}/revisions", ListRevisionsAsync);
    group.MapPost("/", CreateAsync);
    group.MapPost("/{id:guid}/revisions", AppendRevisionAsync);
    group.MapPost("/{id:guid}/archive", ArchiveAsync);
    group.MapPost("/import/preview", PreviewImportAsync).DisableAntiforgery();
    group.MapPost("/import/confirm", ConfirmImportAsync);
    return endpoints;
  }

  private static async Task<IResult> ListAsync(IWorkoutStore store, CancellationToken cancellationToken)
  {
    IReadOnlyList<StoredWorkout> workouts = await store.ListAsync(cancellationToken);
    return TypedResults.Ok(workouts
      .Where(static workout => !workout.IsArchived)
      .Select(ToSummary)
      .ToArray());
  }

  private static async Task<IResult> ListReuseAsync(
    Guid profileId,
    IWorkoutStore store,
    CancellationToken cancellationToken,
    int take = 4)
  {
    try
    {
      IReadOnlyList<StoredWorkoutReuse> reusable = await store.ListReusableAsync(profileId, take, cancellationToken);
      return TypedResults.Ok(reusable.Select(item =>
      {
        WorkoutStoredJsonSummary summary = ReadStoredSummary(item.DefinitionJson);
        using JsonDocument document = JsonDocument.Parse(item.DefinitionJson);
        return new WorkoutReuseDto(
          item.WorkoutId,
          item.WorkoutRevisionId,
          document.RootElement.GetProperty("title").GetString() ?? "Saved workout",
          summary.Description,
          summary.ExpandedStepCount,
          summary.DurationMinutes,
          item.LastCompletedAtUtc,
          item.LastActualDuration,
          item.CompletionCount);
      }).ToArray());
    }
    catch (ArgumentException exception)
    {
      return TypedResults.BadRequest(new { message = exception.Message });
    }
  }

  private static async Task<IResult> ListRevisionsAsync(
    Guid id,
    IWorkoutStore store,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<StoredWorkoutRevision> revisions = await store.ListRevisionsAsync(id, cancellationToken);
    return TypedResults.Ok(revisions.Select(ToRevisionDto).ToArray());
  }

  private static async Task<IResult> CreateAsync(
    WorkoutSaveRequest request,
    IWorkoutStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      WorkoutDefinition definition = CreateDefinition(request);
      if (!Enum.TryParse(request.Kind, ignoreCase: true, out WorkoutKind workoutKind))
      {
        throw new ArgumentException("Workout kind must be Structured or ManualTemplate.");
      }
      string contentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(definition);
      requestFingerprint = PlanningOperationFingerprint.Compute(new { ContentSha256 = contentSha256, Kind = workoutKind });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return await ReplayWorkoutAsync(receipt, "workout.create", requestFingerprint, store, cancellationToken);
      }
      Guid workoutId = Guid.NewGuid();
      DateTimeOffset now = timeProvider.GetUtcNow();
      var locator = new WorkoutWriteLocator(workoutId, contentSha256, now);
      StoredWorkoutRevision revision = await store.CreateAsync(
        workoutId,
        definition,
        now,
        WriteOperation(request.OperationId, "workout.create", StatusCodes.Status201Created, locator, now, requestFingerprint),
        cancellationToken,
        workoutKind);
      return TypedResults.Created(
        $"/api/planning/workouts/{workoutId}/revisions",
        ToSaveResponse(revision));
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationReplayException replay)
    {
      return await ReplayWorkoutAsync(replay, "workout.create", requestFingerprint, store, cancellationToken);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> AppendRevisionAsync(
    Guid id,
    WorkoutSaveRequest request,
    IWorkoutStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      WorkoutDefinition definition = CreateDefinition(request);
      string contentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(definition);
      requestFingerprint = PlanningOperationFingerprint.Compute(new { WorkoutId = id, ContentSha256 = contentSha256 });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return await ReplayWorkoutAsync(receipt, "workout.revision.create", requestFingerprint, store, cancellationToken);
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      var locator = new WorkoutWriteLocator(id, contentSha256, now);
      StoredWorkoutRevision revision = await store.AppendRevisionAsync(
        id,
        definition,
        now,
        WriteOperation(request.OperationId, "workout.revision.create", StatusCodes.Status201Created, locator, now, requestFingerprint),
        cancellationToken);
      return TypedResults.Created(
        $"/api/planning/workouts/{id}/revisions/{revision.Id}",
        ToSaveResponse(revision));
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationReplayException replay)
    {
      return await ReplayWorkoutAsync(replay, "workout.revision.create", requestFingerprint, store, cancellationToken);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> ArchiveAsync(
    Guid id,
    ArchiveWorkoutRequest request,
    IWorkoutStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      requestFingerprint = PlanningOperationFingerprint.Compute(new { WorkoutId = id });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "workout.archive", requestFingerprint);
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      bool archived = await store.SetArchivedAsync(
        id,
        isArchived: true,
        WriteOperation(request.OperationId, "workout.archive", StatusCodes.Status204NoContent, new { }, now, requestFingerprint),
        cancellationToken);
      return archived ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "workout.archive", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  [RequestSizeLimit(MaximumImportRequestBytes)]
  [RequestFormLimits(MultipartBodyLengthLimit = WorkoutImportLimits.MaximumBytes)]
  private static async Task<IResult> PreviewImportAsync(
    HttpRequest request,
    IEnumerable<IWorkoutImporter> importers,
    IWorkoutImportPreviewStore previewStore,
    CancellationToken cancellationToken)
  {
    try
    {
      if (request.ContentLength is > MaximumImportRequestBytes)
      {
        return TypedResults.Problem(
          "The multipart import request is too large.",
          statusCode: StatusCodes.Status413PayloadTooLarge);
      }

      if (!request.HasFormContentType)
      {
        throw new WorkoutImportException("Import preview requires multipart form data.");
      }

      IFormCollection form = await request.ReadFormAsync(cancellationToken);
      IFormFile? file = form.Files.GetFile("file");
      if (form.Files.Count != 1 || file is null ||
          form.Count != 1 || !form.ContainsKey("format") || form["format"].Count != 1)
      {
        throw new WorkoutImportException("Import preview accepts exactly one file part and one format field.");
      }

      if (file.Length is <= 0 or > WorkoutImportLimits.MaximumBytes)
      {
        throw new WorkoutImportException("Choose a non-empty workout file no larger than 10 MB.");
      }

      string formatText = form["format"].ToString();
      if (int.TryParse(formatText, out _) ||
          !Enum.TryParse(formatText, ignoreCase: true, out WorkoutImportFormat format) ||
          !Enum.IsDefined(format))
      {
        throw new WorkoutImportException("Choose NativeJson, QDomyosXml, or GarminFit.");
      }

      IWorkoutImporter importer = importers.SingleOrDefault(candidate => candidate.Format == format)
        ?? throw new WorkoutImportException($"The {format} importer is unavailable.");
      byte[] sourceBytes = await ReadBytesAsync(file, cancellationToken);
      await using var source = new MemoryStream(sourceBytes, writable: false);
      WorkoutImportResult result = await importer.ImportAsync(source, file.FileName, cancellationToken);
      WorkoutImportPreview preview = previewStore.Add(file.FileName, format, sourceBytes, result);
      return TypedResults.Ok(ToPreviewDto(preview));
    }
    catch (WorkoutImportException exception)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["file"] = [exception.Message],
      });
    }
    catch (InvalidDataException)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["file"] = ["The multipart import request is malformed or exceeds the 10 MB file limit."],
      });
    }
    catch (InvalidOperationException exception)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["format"] = [exception.Message],
      });
    }
  }

  private static async Task<IResult> ConfirmImportAsync(
    ImportConfirmRequest request,
    IEnumerable<IWorkoutImporter> importers,
    IWorkoutImportPreviewStore previewStore,
    IWorkoutStore workoutStore,
    IProfileStore profileStore,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      ValidateOperationId(request.OperationId);
      string requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        request.PreviewId,
        request.SourceSha256,
        request.ProfileId,
        request.QDomyosUnits,
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } completed)
      {
        if (completed.OperationType != "workout.import.confirm" ||
            completed.RequestFingerprint != requestFingerprint)
        {
          return OperationConflict();
        }

        ImportConfirmationReceipt receipt = ImportConfirmationReceipt.Parse(completed.OutcomeJson);
        if (receipt.PreviewId != request.PreviewId ||
            !string.Equals(receipt.SourceSha256, request.SourceSha256, StringComparison.Ordinal))
        {
          return TypedResults.Conflict(new { message = "That operation ID was already used for a different import preview." });
        }

        return Results.Json(ToImportResponse(receipt, replayed: true), statusCode: completed.StatusCode);
      }

      if (!previewStore.TryGet(request.PreviewId, out WorkoutImportPreview? preview) || preview is null)
      {
        return TypedResults.Problem(
          "The import preview expired or is no longer available.",
          statusCode: StatusCodes.Status410Gone);
      }

      string sourceSha256 = Convert.ToHexStringLower(SHA256.HashData(preview.SourceBytes));
      if (!string.Equals(request.SourceSha256, sourceSha256, StringComparison.Ordinal))
      {
        return TypedResults.Conflict(new { message = "The preview source does not match the confirmation request." });
      }

      if (request.ProfileId is { } profileId && await profileStore.FindAsync(profileId, cancellationToken) is not { IsArchived: false })
      {
        return TypedResults.ValidationProblem(new Dictionary<string, string[]>
        {
          ["profileId"] = ["The selected profile does not exist or is archived."],
        });
      }

      IWorkoutImporter importer = importers.Single(candidate => candidate.Format == preview.Format);
      await using var source = new MemoryStream(preview.SourceBytes, writable: false);
      WorkoutImportResult reparsed = await importer.ImportAsync(source, preview.FileName, cancellationToken);
      reparsed = ApplyQDomyosUnits(reparsed, request.QDomyosUnits);
      DateTimeOffset now = timeProvider.GetUtcNow();
      Guid workoutId = Guid.NewGuid();
      var audit = new ImportAuditRecord(
        Guid.NewGuid(),
        request.ProfileId,
        workoutId,
        Guid.Empty,
        preview.FileName,
        preview.Format.ToString(),
        sourceSha256,
        JsonSerializer.Serialize(reparsed.Warnings),
        now);
      ImportConfirmationOutcome outcome = await workoutStore.ConfirmImportAsync(
        new PersistenceWriteOperation(
          request.OperationId,
          "workout.import.confirm",
          StatusCodes.Status201Created,
          "{}",
          now,
          requestFingerprint),
        request.PreviewId,
        workoutId,
        reparsed.Definition,
        audit,
        now,
        cancellationToken);
      ImportConfirmResponse response = ToImportResponse(outcome.Receipt, outcome.Replayed);
      return TypedResults.Created(
        $"/api/planning/workouts/{outcome.Revision.WorkoutId}/revisions/{outcome.Revision.Id}",
        response);
    }
    catch (WorkoutImportException exception)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["previewId"] = [$"The stored source no longer reparses safely: {exception.Message}"],
      });
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static WorkoutDefinition CreateDefinition(WorkoutSaveRequest request)
  {
    if (request.Blocks is null || request.Blocks.Count == 0)
    {
      throw new ArgumentException("A workout requires at least one block.");
    }

    WorkoutBlock[] blocks = request.Blocks.Select(CreateBlock).ToArray();
    return new WorkoutDefinition(1, request.Name, request.Description, blocks);
  }

  private static WorkoutBlock CreateBlock(WorkoutBlockRequest? block)
  {
    if (block is null)
    {
      throw new ArgumentException("Workout blocks cannot contain null entries.");
    }

    if (string.Equals(block.Kind, "repeat", StringComparison.OrdinalIgnoreCase))
    {
      if (block.Blocks is null)
      {
        throw new ArgumentException("Repeat blocks cannot have null children.");
      }

      return new WorkoutRepeat(block.Repetitions, block.Blocks.Select(CreateBlock).ToArray());
    }

    if (!string.Equals(block.Kind, "step", StringComparison.OrdinalIgnoreCase))
    {
      throw new ArgumentException("Block kind must be step or repeat.");
    }

    StepGoal goal = block.GoalKind?.ToLowerInvariant() switch
    {
      "time" => new TimeGoal(TimeSpan.FromMinutes(block.GoalValue)),
      "distance" => new DistanceGoal(block.GoalValue),
      _ => throw new ArgumentException("Goal kind must be time or distance."),
    };
    SpeedDirective speed = block.SpeedKind?.ToLowerInvariant() switch
    {
      "open" => new OpenSpeed(),
      "fixed" => new FixedSpeed(block.SpeedStartKph),
      "ramp" => new SpeedRamp(block.SpeedStartKph, block.SpeedEndKph),
      "heartrate" => new HeartRateSpeed(
        block.HeartRateMinimumBpm,
        block.HeartRateMaximumBpm,
        block.HeartRateInitialSpeedKph,
        block.HeartRateMinimumSpeedKph,
        block.HeartRateMaximumSpeedKph),
      "heartratezone" => new HeartRateZoneSpeed(
        block.HeartRateZoneNumber,
        block.HeartRateInitialSpeedKph,
        block.HeartRateMinimumSpeedKph,
        block.HeartRateMaximumSpeedKph),
      _ => throw new ArgumentException("Speed kind must be open, fixed, ramp, heartRate, or heartRateZone."),
    };
    InclineDirective incline = block.InclineKind?.ToLowerInvariant() switch
    {
      "fixed" => new FixedIncline(block.InclineStartPercent),
      "ramp" => new InclineRamp(block.InclineStartPercent, block.InclineEndPercent),
      _ => throw new ArgumentException("Incline kind must be fixed or ramp."),
    };
    return new WorkoutStep(goal, speed, incline, block.Cue, block.Notes);
  }

  private static WorkoutSummaryDto ToSummary(StoredWorkout workout)
  {
    WorkoutStoredJsonSummary summary = ReadStoredSummary(workout.LatestDefinitionJson);
    return new WorkoutSummaryDto(
      workout.Id,
      workout.Name,
      summary.Description,
      workout.Kind.ToString(),
      workout.IsArchived,
      workout.LatestRevisionId,
      workout.LatestRevisionNumber,
      summary.ExpandedStepCount,
      summary.DurationMinutes,
      workout.LatestCreatedAtUtc);
  }

  private static WorkoutStoredJsonSummary ReadStoredSummary(string definitionJson)
  {
    using JsonDocument document = JsonDocument.Parse(definitionJson);
    (int steps, long durationTicks, bool hasDistance) = CountBlocks(document.RootElement.GetProperty("blocks"));
    return new WorkoutStoredJsonSummary(
      document.RootElement.TryGetProperty("description", out JsonElement description) &&
        description.ValueKind == JsonValueKind.String
          ? description.GetString()
          : null,
      steps,
      hasDistance ? null : TimeSpan.FromTicks(durationTicks).TotalMinutes);
  }

  private static (int Steps, long DurationTicks, bool HasDistance) CountBlocks(JsonElement blocks)
  {
    int steps = 0;
    long durationTicks = 0;
    bool hasDistance = false;
    foreach (JsonElement block in blocks.EnumerateArray())
    {
      switch (block.GetProperty("kind").GetString())
      {
        case "step":
          steps++;
          JsonElement goal = block.GetProperty("goal");
          if (goal.GetProperty("kind").GetString() == "time")
          {
            durationTicks = checked(durationTicks + goal.GetProperty("durationTicks").GetInt64());
          }
          else
          {
            hasDistance = true;
          }

          break;
        case "repeat":
          int repetitions = block.GetProperty("repetitions").GetInt32();
          var nested = CountBlocks(block.GetProperty("blocks"));
          steps = checked(steps + (nested.Steps * repetitions));
          durationTicks = checked(durationTicks + (nested.DurationTicks * repetitions));
          hasDistance |= nested.HasDistance;
          break;
      }
    }

    return (steps, durationTicks, hasDistance);
  }

  private static async Task<byte[]> ReadBytesAsync(IFormFile file, CancellationToken cancellationToken)
  {
    await using Stream source = file.OpenReadStream();
    using var buffer = new MemoryStream((int)file.Length);
    await source.CopyToAsync(buffer, cancellationToken);
    return buffer.ToArray();
  }

  private static WorkoutSaveResponse ToSaveResponse(StoredWorkoutRevision revision) => new(
    revision.WorkoutId,
    revision.Id,
    revision.RevisionNumber,
    revision.ContentSha256);

  private static ImportConfirmResponse ToImportResponse(ImportConfirmationReceipt receipt, bool replayed)
  {
    WorkoutImportWarning[] warnings = JsonSerializer.Deserialize<WorkoutImportWarning[]>(receipt.WarningSummaryJson) ?? [];
    return new ImportConfirmResponse(
      receipt.WorkoutId,
      receipt.WorkoutRevisionId,
      receipt.RevisionNumber,
      replayed,
      warnings.Select(static warning => new ImportWarningDto(warning.Code, warning.Message)).ToArray());
  }

  private static WorkoutRevisionDto ToRevisionDto(StoredWorkoutRevision revision)
  {
    using JsonDocument document = JsonDocument.Parse(revision.DefinitionJson);
    JsonElement root = document.RootElement;
    return new WorkoutRevisionDto(
      revision.WorkoutId,
      revision.Id,
      revision.RevisionNumber,
      revision.ContentSha256,
      root.GetProperty("title").GetString() ?? "Untitled workout",
      root.GetProperty("description").GetString(),
      root.GetProperty("blocks").EnumerateArray().Select(ParseBlock).ToArray());
  }

  private static WorkoutBlockRequest ParseBlock(JsonElement block)
  {
    if (block.GetProperty("kind").GetString() == "repeat")
    {
      return EmptyBlock with
      {
        Kind = "repeat",
        Repetitions = block.GetProperty("repetitions").GetInt32(),
        Blocks = block.GetProperty("blocks").EnumerateArray().Select(ParseBlock).ToArray(),
      };
    }

    JsonElement goal = block.GetProperty("goal");
    JsonElement speed = block.GetProperty("speed");
    JsonElement incline = block.GetProperty("incline");
    string goalKind = goal.GetProperty("kind").GetString()!;
    string speedKind = speed.GetProperty("kind").GetString()!;
    string inclineKind = incline.GetProperty("kind").GetString()!;
    return new WorkoutBlockRequest(
      "step",
      1,
      [],
      goalKind,
      goalKind == "time" ? TimeSpan.FromTicks(goal.GetProperty("durationTicks").GetInt64()).TotalMinutes : goal.GetProperty("kilometers").GetDouble(),
      speedKind,
      ReadNumber(speed, speedKind == "fixed" ? "kilometersPerHour" : "startKilometersPerHour"),
      ReadNumber(speed, "endKilometersPerHour"),
      ReadUShort(speed, "minimumBpm"),
      ReadUShort(speed, "maximumBpm"),
      ReadInt(speed, "zoneNumber"),
      ReadNumber(speed, "initialKilometersPerHour"),
      ReadNumber(speed, "minimumKilometersPerHour"),
      ReadNumber(speed, "maximumKilometersPerHour"),
      inclineKind,
      ReadNumber(incline, inclineKind == "fixed" ? "percent" : "startPercent"),
      ReadNumber(incline, "endPercent"),
      block.GetProperty("cue").GetString(),
      block.GetProperty("notes").GetString());
  }

  private static readonly WorkoutBlockRequest EmptyBlock = new(
    "step", 1, [], "time", 5, "fixed", 0, 0, 0, 0, 0, 0, 0, 0, "fixed", 0, 0, null, null);

  private static double ReadNumber(JsonElement element, string property) =>
    element.TryGetProperty(property, out JsonElement value) ? value.GetDouble() : 0;

  private static ushort ReadUShort(JsonElement element, string property) =>
    element.TryGetProperty(property, out JsonElement value) ? value.GetUInt16() : (ushort)0;

  private static int ReadInt(JsonElement element, string property) =>
    element.TryGetProperty(property, out JsonElement value) ? value.GetInt32() : 0;

  private static ImportPreviewDto ToPreviewDto(WorkoutImportPreview preview) => new(
    preview.Id,
    Convert.ToHexStringLower(SHA256.HashData(preview.SourceBytes)),
    preview.FileName,
    preview.Format.ToString(),
    preview.Result.Definition.Title,
    preview.Result.ExpandedStepCount,
    preview.Result.TotalDuration?.TotalMinutes,
    preview.ExpiresAtUtc,
    preview.Result.Warnings.Select(static warning => new ImportWarningDto(warning.Code, warning.Message)).ToArray());

  private static WorkoutImportResult ApplyQDomyosUnits(WorkoutImportResult result, string? units)
  {
    if (result.Format != WorkoutImportFormat.QDomyosXml)
    {
      return result;
    }

    if (units is not ("KilometersPerHour" or "MilesPerHour"))
    {
      throw new ArgumentException("QDomyosUnits must explicitly be KilometersPerHour or MilesPerHour.");
    }

    WorkoutImportWarning[] confirmedWarnings =
    [
      .. result.Warnings.Where(static warning => warning.Code != "qdomyos.assumed-speed-units"),
      new WorkoutImportWarning(
        units == "KilometersPerHour" ? "qdomyos.confirmed-kph-units" : "qdomyos.confirmed-mph-units",
        units == "KilometersPerHour"
          ? "The source speed and distance units were explicitly confirmed as metric."
          : "The chosen source miles/mph units were converted to kilometers and km/h."),
    ];
    if (units == "KilometersPerHour")
    {
      return result with { Warnings = confirmedWarnings };
    }

    const double milesToKilometers = 1.609344;
    WorkoutBlock ConvertBlock(WorkoutBlock block) => block switch
    {
      WorkoutRepeat repeat => new WorkoutRepeat(repeat.Repetitions, repeat.Blocks.Select(ConvertBlock).ToArray()),
      WorkoutStep step => new WorkoutStep(
        step.Goal is DistanceGoal distance
          ? new DistanceGoal(distance.Kilometers * milesToKilometers)
          : step.Goal,
        step.Speed switch
        {
          FixedSpeed speed => new FixedSpeed(speed.KilometersPerHour * milesToKilometers),
          SpeedRamp speed => new SpeedRamp(
            speed.StartKilometersPerHour * milesToKilometers,
            speed.EndKilometersPerHour * milesToKilometers),
          HeartRateSpeed speed => new HeartRateSpeed(
            speed.MinimumBpm,
            speed.MaximumBpm,
            speed.InitialKilometersPerHour * milesToKilometers,
            speed.MinimumKilometersPerHour * milesToKilometers,
            speed.MaximumKilometersPerHour * milesToKilometers),
          HeartRateZoneSpeed speed => new HeartRateZoneSpeed(
            speed.ZoneNumber,
            speed.InitialKilometersPerHour * milesToKilometers,
            speed.MinimumKilometersPerHour * milesToKilometers,
            speed.MaximumKilometersPerHour * milesToKilometers),
          _ => step.Speed,
        },
        step.Incline,
        step.Cue,
        step.Notes),
      _ => throw new ArgumentException("Unsupported workout block."),
    };

    var converted = new WorkoutDefinition(
      result.Definition.SchemaVersion,
      result.Definition.Title,
      result.Definition.Description,
      result.Definition.Blocks.Select(ConvertBlock).ToArray());
    return new WorkoutImportResult(
      result.Format,
      converted,
      converted.ExpandedStepCount,
      converted.KnownDuration,
      confirmedWarnings);
  }

  private static void ValidateOperationId(Guid operationId)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("OperationId cannot be empty.");
    }
  }

  private static IResult Validation(ArgumentException exception) => TypedResults.ValidationProblem(
    new Dictionary<string, string[]> { ["request"] = [exception.Message] });

  private static PersistenceWriteOperation WriteOperation(
    Guid id,
    string type,
    int statusCode,
    object outcome,
    DateTimeOffset now,
    string requestFingerprint) => new(
      id, type, statusCode, JsonSerializer.Serialize(outcome), now, requestFingerprint);

  private static IResult Replay(OperationReplayException replay, string expectedType, string requestFingerprint) =>
    Replay(replay.Receipt, expectedType, requestFingerprint);

  private static IResult Replay(OperationReceipt receipt, string expectedType, string requestFingerprint) =>
    receipt.OperationType == expectedType && receipt.RequestFingerprint == requestFingerprint
      ? Results.Content(receipt.OutcomeJson, "application/json", statusCode: receipt.StatusCode)
      : OperationConflict();

  private static IResult OperationConflict() =>
    TypedResults.Conflict(new { message = "That operation ID was already used for another action or request." });

  private static async Task<IResult> ReplayWorkoutAsync(
    OperationReplayException replay,
    string expectedType,
    string requestFingerprint,
    IWorkoutStore store,
    CancellationToken cancellationToken)
  {
    return await ReplayWorkoutAsync(replay.Receipt, expectedType, requestFingerprint, store, cancellationToken);
  }

  private static async Task<IResult> ReplayWorkoutAsync(
    OperationReceipt receipt,
    string expectedType,
    string requestFingerprint,
    IWorkoutStore store,
    CancellationToken cancellationToken)
  {
    if (receipt.OperationType != expectedType || receipt.RequestFingerprint != requestFingerprint)
    {
      return OperationConflict();
    }

    await Task.CompletedTask;
    WorkoutRevisionReceipt revision = WorkoutRevisionReceipt.Parse(receipt.OutcomeJson);
    return Results.Json(
      new WorkoutSaveResponse(revision.WorkoutId, revision.Id, revision.RevisionNumber, revision.ContentSha256),
      statusCode: receipt.StatusCode);
  }

  private sealed record WorkoutStoredJsonSummary(
    string? Description,
    int ExpandedStepCount,
    double? DurationMinutes);
  private sealed record WorkoutWriteLocator(Guid WorkoutId, string ContentSha256, DateTimeOffset CreatedAtUtc);
}
