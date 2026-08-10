using System.Globalization;
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
    group.MapGet("/revisions/{revisionId:guid}", GetRevisionAsync);
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
    IReadOnlyList<StoredWorkout> workouts = await store.ListVisibleAsync(cancellationToken);
    return TypedResults.Ok(workouts
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

  private static async Task<IResult> GetRevisionAsync(
    Guid revisionId,
    IWorkoutStore store,
    CancellationToken cancellationToken)
  {
    StoredWorkoutRevision? revision = await store.FindRevisionAsync(revisionId, cancellationToken);
    return revision is null
      ? TypedResults.NotFound()
      : TypedResults.Ok(ToRevisionDto(revision));
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
        throw new ArgumentException("Workout kind must be Structured, ManualTemplate, or PlanInternal.");
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
      workout.LatestCreatedAtUtc,
      summary.StructureLabel,
      summary.GoalLabel,
      summary.SpeedLabel,
      summary.InclineLabel,
      summary.UsesHeartRate);
  }

  private static WorkoutStoredJsonSummary ReadStoredSummary(string definitionJson)
  {
    using JsonDocument document = JsonDocument.Parse(definitionJson);
    var accumulator = new WorkoutSummaryAccumulator();
    SummarizeBlocks(document.RootElement.GetProperty("blocks"), 1, accumulator);
    return new WorkoutStoredJsonSummary(
      document.RootElement.TryGetProperty("description", out JsonElement description) &&
        description.ValueKind == JsonValueKind.String
          ? description.GetString()
          : null,
      accumulator.Steps,
      accumulator.HasDistance ? null : TimeSpan.FromTicks(accumulator.DurationTicks).TotalMinutes,
      StructureLabel(accumulator),
      GoalLabel(accumulator),
      SpeedLabel(accumulator),
      InclineLabel(accumulator),
      accumulator.UsesHeartRate);
  }

  private static void SummarizeBlocks(JsonElement blocks, int multiplier, WorkoutSummaryAccumulator summary)
  {
    foreach (JsonElement block in blocks.EnumerateArray())
    {
      switch (block.GetProperty("kind").GetString())
      {
        case "step":
          summary.Steps = checked(summary.Steps + multiplier);
          JsonElement goal = block.GetProperty("goal");
          if (goal.GetProperty("kind").GetString() == "time")
          {
            summary.HasTime = true;
            summary.DurationTicks = checked(summary.DurationTicks + (goal.GetProperty("durationTicks").GetInt64() * multiplier));
          }
          else
          {
            summary.HasDistance = true;
            summary.DistanceKilometers += goal.GetProperty("kilometers").GetDouble() * multiplier;
          }

          SummarizeSpeed(block.GetProperty("speed"), summary);
          SummarizeIncline(block.GetProperty("incline"), summary);

          break;
        case "repeat":
          int repetitions = block.GetProperty("repetitions").GetInt32();
          summary.HasRepeat = true;
          SummarizeBlocks(block.GetProperty("blocks"), checked(multiplier * repetitions), summary);
          break;
      }
    }
  }

  private static void SummarizeSpeed(JsonElement speed, WorkoutSummaryAccumulator summary)
  {
    string kind = speed.GetProperty("kind").GetString() ?? "open";
    summary.StepRepresentativeSpeeds.Add(kind switch
    {
      "fixed" => ReadNumber(speed, "kilometersPerHour"),
      "ramp" => ReadNumber(speed, "endKilometersPerHour"),
      "heartRate" or "heartRateZone" => ReadNumber(speed, "initialKilometersPerHour"),
      _ => null,
    });
    switch (kind)
    {
      case "open":
        summary.HasOpenSpeed = true;
        break;
      case "fixed":
        AddRange(summary, ReadNumber(speed, "kilometersPerHour"), ReadNumber(speed, "kilometersPerHour"));
        break;
      case "ramp":
        summary.HasRamp = true;
        AddRange(summary, ReadNumber(speed, "startKilometersPerHour"), ReadNumber(speed, "endKilometersPerHour"));
        break;
      case "heartRate":
        summary.UsesHeartRate = true;
        AddRange(summary, ReadNumber(speed, "minimumKilometersPerHour"), ReadNumber(speed, "maximumKilometersPerHour"));
        summary.MinimumHeartRateBpm = Minimum(summary.MinimumHeartRateBpm, ReadUShort(speed, "minimumBpm"));
        summary.MaximumHeartRateBpm = Maximum(summary.MaximumHeartRateBpm, ReadUShort(speed, "maximumBpm"));
        break;
      case "heartRateZone":
        summary.UsesHeartRate = true;
        AddRange(summary, ReadNumber(speed, "minimumKilometersPerHour"), ReadNumber(speed, "maximumKilometersPerHour"));
        summary.HeartRateZones.Add(ReadInt(speed, "zoneNumber"));
        break;
    }
  }

  private static void SummarizeIncline(JsonElement incline, WorkoutSummaryAccumulator summary)
  {
    string kind = incline.GetProperty("kind").GetString() ?? "fixed";
    if (kind == "ramp")
    {
      summary.HasRamp = true;
      AddInclineRange(summary, ReadNumber(incline, "startPercent"), ReadNumber(incline, "endPercent"));
      return;
    }

    double value = ReadNumber(incline, "percent");
    AddInclineRange(summary, value, value);
  }

  private static void AddRange(WorkoutSummaryAccumulator summary, double first, double second)
  {
    summary.MinimumSpeedKph = Minimum(summary.MinimumSpeedKph, Math.Min(first, second));
    summary.MaximumSpeedKph = Maximum(summary.MaximumSpeedKph, Math.Max(first, second));
  }

  private static void AddInclineRange(WorkoutSummaryAccumulator summary, double first, double second)
  {
    summary.MinimumInclinePercent = Minimum(summary.MinimumInclinePercent, Math.Min(first, second));
    summary.MaximumInclinePercent = Maximum(summary.MaximumInclinePercent, Math.Max(first, second));
  }

  private static double? Minimum(double? current, double value) => current is null ? value : Math.Min(current.Value, value);
  private static double? Maximum(double? current, double value) => current is null ? value : Math.Max(current.Value, value);
  private static ushort? Minimum(ushort? current, ushort value) => value == 0 ? current : current is null ? value : Math.Min(current.Value, value);
  private static ushort? Maximum(ushort? current, ushort value) => value == 0 ? current : current is null ? value : Math.Max(current.Value, value);

  private static string StructureLabel(WorkoutSummaryAccumulator summary)
  {
    if (summary.UsesHeartRate) return summary.HasRepeat ? "HR intervals" : "HR adaptive";
    if (summary.HasRepeat) return "Intervals";
    if (summary.HasRamp) return "Progression";
    double[] speeds = summary.StepRepresentativeSpeeds.Where(static speed => speed is not null).Select(static speed => speed!.Value).ToArray();
    if (speeds.Distinct().Skip(1).Any())
    {
      bool nonDecreasing = speeds.Zip(speeds.Skip(1), static (left, right) => right >= left).All(static ordered => ordered);
      return nonDecreasing && speeds[^1] > speeds[0] ? "Progression" : "Intervals";
    }

    return summary.Steps == 1 ? "Steady" : "Multi-stage";
  }

  private static string GoalLabel(WorkoutSummaryAccumulator summary)
  {
    if (summary.HasTime && summary.HasDistance) return "Time + distance";
    if (summary.HasDistance) return $"{Format(summary.DistanceKilometers)} km";
    return $"{Format(TimeSpan.FromTicks(summary.DurationTicks).TotalMinutes)} min";
  }

  private static string SpeedLabel(WorkoutSummaryAccumulator summary)
  {
    string range = FormatRange(summary.MinimumSpeedKph, summary.MaximumSpeedKph, " km/h");
    if (summary.HeartRateZones.Count > 0)
    {
      int minimum = summary.HeartRateZones.Min;
      int maximum = summary.HeartRateZones.Max;
      string zones = minimum == maximum ? $"Z{minimum}" : $"Z{minimum}–Z{maximum}";
      return $"{zones} · {range}";
    }

    if (summary.MinimumHeartRateBpm is { } minimumBpm && summary.MaximumHeartRateBpm is { } maximumBpm)
    {
      return $"{minimumBpm}–{maximumBpm} bpm · {range}";
    }

    if (summary.HasOpenSpeed && summary.MinimumSpeedKph is null) return "Manual speed";
    return summary.HasOpenSpeed ? $"Manual + {range}" : range;
  }

  private static string InclineLabel(WorkoutSummaryAccumulator summary) =>
    FormatRange(summary.MinimumInclinePercent, summary.MaximumInclinePercent, "% incline");

  private static string FormatRange(double? minimum, double? maximum, string suffix)
  {
    if (minimum is null || maximum is null) return $"No fixed {suffix.Trim()}";
    return Math.Abs(minimum.Value - maximum.Value) < 0.001
      ? $"{Format(minimum.Value)}{suffix}"
      : $"{Format(minimum.Value)}–{Format(maximum.Value)}{suffix}";
  }

  private static string Format(double value) => value.ToString("0.##", CultureInfo.InvariantCulture);

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

  internal static IReadOnlyList<WorkoutBlockRequest> ToBlockRequests(WorkoutDefinition definition)
  {
    using JsonDocument document = JsonDocument.Parse(WorkoutDefinitionCanonicalizer.Serialize(definition));
    return document.RootElement.GetProperty("blocks").EnumerateArray().Select(ParseBlock).ToArray();
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
    double? DurationMinutes,
    string StructureLabel,
    string GoalLabel,
    string SpeedLabel,
    string InclineLabel,
    bool UsesHeartRate);

  private sealed class WorkoutSummaryAccumulator
  {
    public int Steps { get; set; }
    public long DurationTicks { get; set; }
    public double DistanceKilometers { get; set; }
    public bool HasTime { get; set; }
    public bool HasDistance { get; set; }
    public bool HasRepeat { get; set; }
    public bool HasRamp { get; set; }
    public bool HasOpenSpeed { get; set; }
    public bool UsesHeartRate { get; set; }
    public double? MinimumSpeedKph { get; set; }
    public double? MaximumSpeedKph { get; set; }
    public double? MinimumInclinePercent { get; set; }
    public double? MaximumInclinePercent { get; set; }
    public ushort? MinimumHeartRateBpm { get; set; }
    public ushort? MaximumHeartRateBpm { get; set; }
    public SortedSet<int> HeartRateZones { get; } = [];
    public List<double?> StepRepresentativeSpeeds { get; } = [];
  }
  private sealed record WorkoutWriteLocator(Guid WorkoutId, string ContentSha256, DateTimeOffset CreatedAtUtc);
}
