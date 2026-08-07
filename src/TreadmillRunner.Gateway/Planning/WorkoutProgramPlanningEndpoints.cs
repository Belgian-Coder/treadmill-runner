using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Planning;

public static class WorkoutProgramPlanningEndpoints
{
  private static readonly JsonSerializerOptions WebJsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapWorkoutPrograms(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/programs");
    group.MapGet("/", ListAsync);
    group.MapPost("/", CreateAsync);
    group.MapPost("/{id:guid}/revisions", AppendRevisionAsync);
    group.MapPost("/{id:guid}/archive", ArchiveAsync);
    group.MapPost("/{id:guid}/start", StartAsync);
    group.MapPost("/{id:guid}/restart", RestartAsync);
    return endpoints;
  }

  private static async Task<IResult> ListAsync(
    Guid? profileId,
    IWorkoutProgramStore store,
    IWorkoutStore workoutStore,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<StoredWorkoutProgramProgress> programs = await store.ListAsync(profileId, cancellationToken);
    var result = new List<WorkoutProgramDto>(programs.Count);
    foreach (StoredWorkoutProgramProgress program in programs.Where(static item => !item.Program.IsArchived))
    {
      result.Add(await ToDtoAsync(program, workoutStore, cancellationToken));
    }
    return TypedResults.Ok(result);
  }

  private static async Task<IResult> CreateAsync(
    WorkoutProgramSaveRequest request,
    IWorkoutProgramStore store,
    IWorkoutStore workoutStore,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      Guid programId = Guid.NewGuid();
      WorkoutProgramRevision revision = CreateRevision(programId, Guid.NewGuid(), 1, request);
      fingerprint = PlanningOperationFingerprint.Compute(new
      {
        revision.Name,
        revision.Description,
        revision.Category,
        Items = revision.Items.Select(static item => item.WorkoutRevisionId),
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "program.create", fingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      WorkoutProgramDto response = await ToDtoAsync(
        new StoredWorkoutProgramProgress(new StoredWorkoutProgram(programId, false, now, revision), null, null),
        workoutStore,
        cancellationToken);
      await store.CreateAsync(
        revision,
        now,
        WriteOperation(request.OperationId, "program.create", 201, response, now, fingerprint),
        cancellationToken);
      return TypedResults.Created($"/api/planning/programs/{programId}", response);
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (OperationReplayException replay) { return Replay(replay.Receipt, "program.create", fingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static async Task<IResult> AppendRevisionAsync(
    Guid id,
    WorkoutProgramSaveRequest request,
    IWorkoutProgramStore store,
    IWorkoutStore workoutStore,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      StoredWorkoutProgram existing = await store.FindAsync(id, cancellationToken)
        ?? throw new KeyNotFoundException();
      if (existing.CurrentRevision.TemplateId is not null)
      {
        return TypedResults.Conflict(new
        {
          message = "Premade training plans are immutable. Add a fresh copy from the catalog instead.",
        });
      }
      WorkoutProgramRevision revision = CreateRevision(
        id, Guid.NewGuid(), existing.CurrentRevision.RevisionNumber + 1, request);
      fingerprint = PlanningOperationFingerprint.Compute(new
      {
        ProgramId = id,
        revision.Name,
        revision.Description,
        revision.Category,
        Items = revision.Items.Select(static item => item.WorkoutRevisionId),
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "program.revision.create", fingerprint);
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      WorkoutProgramDto response = await ToDtoAsync(
        new StoredWorkoutProgramProgress(new StoredWorkoutProgram(id, false, existing.CreatedAtUtc, revision), null, null),
        workoutStore,
        cancellationToken);
      await store.AppendRevisionAsync(
        revision,
        WriteOperation(request.OperationId, "program.revision.create", 201, response, now, fingerprint),
        cancellationToken);
      return TypedResults.Created($"/api/planning/programs/{id}/revisions/{revision.RevisionId}", response);
    }
    catch (KeyNotFoundException) { return TypedResults.NotFound(); }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { message = exception.Message }); }
    catch (OperationReplayException replay) { return Replay(replay.Receipt, "program.revision.create", fingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static async Task<IResult> ArchiveAsync(
    Guid id,
    ArchiveWorkoutProgramRequest request,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string fingerprint = PlanningOperationFingerprint.Compute(new { ProgramId = id });
    try
    {
      ValidateOperationId(request.OperationId);
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "program.archive", fingerprint);
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      bool archived = await store.SetArchivedAsync(
        id, true,
        WriteOperation(request.OperationId, "program.archive", 204, new { }, now, fingerprint),
        cancellationToken);
      return archived ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (OperationReplayException replay) { return Replay(replay.Receipt, "program.archive", fingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static Task<IResult> StartAsync(
    Guid id,
    WorkoutProgramStartRequest request,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => StartOrRestartAsync(
      id, request, restart: false, store, receiptStore, timeProvider, cancellationToken);

  private static Task<IResult> RestartAsync(
    Guid id,
    WorkoutProgramStartRequest request,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken) => StartOrRestartAsync(
      id, request, restart: true, store, receiptStore, timeProvider, cancellationToken);

  private static async Task<IResult> StartOrRestartAsync(
    Guid id,
    WorkoutProgramStartRequest request,
    bool restart,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string operationType = restart ? "program.restart" : "program.start";
    string fingerprint = PlanningOperationFingerprint.Compute(new
    {
      ProgramId = id,
      request.ProfileId,
      request.ExpectedProgramRevisionId,
      request.ExpectedActiveRunId,
      request.ExpectedActiveRunVersion,
      request.ScheduledStartDate,
      request.ScheduledWeekdayMask,
      request.ScheduleTimeZoneId,
    });
    try
    {
      ValidateOperationId(request.OperationId);
      if (request.ProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.");
      if (request.ExpectedProgramRevisionId == Guid.Empty) throw new ArgumentException("Expected program revision ID is required.");
      if (request.ExpectedActiveRunId.HasValue != request.ExpectedActiveRunVersion.HasValue)
        throw new ArgumentException("Expected active run ID and version must be supplied together.");
      if (request.ScheduledStartDate.HasValue != (request.ScheduledWeekdayMask != 0) ||
          request.ScheduledStartDate.HasValue != !string.IsNullOrWhiteSpace(request.ScheduleTimeZoneId))
        throw new ArgumentException("Start date, training days, and time zone must be supplied together.");
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, operationType, fingerprint);
      }
      StoredWorkoutProgram program = await store.FindAsync(id, cancellationToken)
        ?? throw new KeyNotFoundException();
      if (program.CurrentRevision.RevisionId != request.ExpectedProgramRevisionId)
        throw new DbUpdateConcurrencyException("The training plan revision changed after confirmation was shown.");
      if (program.CurrentRevision.OwnerProfileId is { } ownerProfileId && ownerProfileId != request.ProfileId)
        return TypedResults.Problem(
          title: "This training plan belongs to another runner.",
          statusCode: StatusCodes.Status403Forbidden);
      WorkoutProgramSchedule? schedule = request.ScheduledStartDate is { } scheduledStartDate
        ? new WorkoutProgramSchedule(scheduledStartDate, (WeekdayFlags)request.ScheduledWeekdayMask, request.ScheduleTimeZoneId!)
        : null;
      if (schedule is not null && program.CurrentRevision.TemplateId is not null)
      {
        int expectedDays = program.CurrentRevision.Items
          .Where(static item => item.WeekNumber is not null)
          .GroupBy(static item => item.WeekNumber)
          .Select(static group => group.Count())
          .DefaultIfEmpty(1)
          .Max();
        if (WorkoutProgramScheduleProjector.CountSelectedDays(schedule.Weekdays) != expectedDays)
          throw new ArgumentException($"Select exactly {expectedDays} training days for this plan.");
      }
      DateTimeOffset now = timeProvider.GetUtcNow();
      Guid runId = Guid.NewGuid();
      var expected = new WorkoutProgramRunDto(
        runId, request.ProfileId, nameof(WorkoutProgramRunStatus.Active), now, null, 1,
        schedule?.StartDate, (int)(schedule?.Weekdays ?? WeekdayFlags.None), schedule?.TimeZoneId);
      PersistenceWriteOperation operation = WriteOperation(
        request.OperationId, operationType, 200, expected, now, fingerprint);
      WorkoutProgramRun run = await store.StartAsync(
        runId,
        request.ProfileId,
        request.ExpectedProgramRevisionId,
        request.ExpectedActiveRunId,
        request.ExpectedActiveRunVersion,
        schedule,
        now,
        operation,
        cancellationToken);
      return TypedResults.Ok(ToDto(run));
    }
    catch (KeyNotFoundException) { return TypedResults.NotFound(); }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (DbUpdateConcurrencyException exception) { return TypedResults.Conflict(new { message = exception.Message }); }
    catch (OperationReplayException replay) { return Replay(replay.Receipt, operationType, fingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static WorkoutProgramRevision CreateRevision(
    Guid programId,
    Guid revisionId,
    int revisionNumber,
    WorkoutProgramSaveRequest request) => new(
      programId,
      revisionId,
      revisionNumber,
      request.Name,
      request.Description,
      request.Category,
      request.Items.Select((item, index) =>
        new WorkoutProgramItem(Guid.NewGuid(), item.WorkoutRevisionId, index + 1)).ToArray());

  private static async Task<WorkoutProgramDto> ToDtoAsync(
    StoredWorkoutProgramProgress stored,
    IWorkoutStore workoutStore,
    CancellationToken cancellationToken)
  {
    var items = new List<WorkoutProgramItemDto>(stored.Program.CurrentRevision.Items.Count);
    foreach (WorkoutProgramItem item in stored.Program.CurrentRevision.Items)
    {
      StoredWorkoutRevision revision = await workoutStore.FindRevisionAsync(item.WorkoutRevisionId, cancellationToken)
        ?? throw new ArgumentException($"Workout revision {item.WorkoutRevisionId} was not found.");
      using JsonDocument json = JsonDocument.Parse(revision.DefinitionJson);
      string name = json.RootElement.GetProperty("title").GetString() ?? "Workout";
      items.Add(new WorkoutProgramItemDto(
        item.Id,
        item.WorkoutRevisionId,
        item.Position,
        name,
        revision.RevisionNumber,
        DurationMinutes(json.RootElement),
        item.WeekNumber,
        item.SessionNumber,
        item.Phase));
    }
    WorkoutProgramProgress? progress = stored.Progress;
    return new WorkoutProgramDto(
      stored.Program.Id,
      stored.Program.IsArchived,
      stored.Program.CurrentRevision.RevisionId,
      stored.Program.CurrentRevision.RevisionNumber,
      stored.Program.CurrentRevision.Name,
      stored.Program.CurrentRevision.Description,
      stored.Program.CurrentRevision.Category,
      items,
      stored.Run is null ? null : ToDto(stored.Run),
      progress?.CompletedItemCount ?? 0,
      progress?.NextItem?.Id,
      progress?.NextItem?.WorkoutRevisionId,
      progress?.IsComplete ?? false,
      stored.Program.CurrentRevision.TemplateId,
      stored.Program.CurrentRevision.TemplateVersion,
      stored.Program.CurrentRevision.OwnerProfileId,
      progress?.SkippedItemCount ?? 0);
  }

  private static double? DurationMinutes(JsonElement root)
  {
    (long ticks, bool distance) = CountDuration(root.GetProperty("blocks"));
    return distance ? null : TimeSpan.FromTicks(ticks).TotalMinutes;
  }

  private static (long Ticks, bool HasDistance) CountDuration(JsonElement blocks)
  {
    long ticks = 0;
    bool distance = false;
    foreach (JsonElement block in blocks.EnumerateArray())
    {
      string kind = block.GetProperty("kind").GetString() ?? string.Empty;
      if (kind == "repeat")
      {
        (long childTicks, bool childDistance) = CountDuration(block.GetProperty("blocks"));
        ticks += childTicks * block.GetProperty("repetitions").GetInt32();
        distance |= childDistance;
      }
      else
      {
        JsonElement goal = block.GetProperty("goal");
        if (goal.GetProperty("kind").GetString() == "time") ticks += goal.GetProperty("durationTicks").GetInt64();
        else distance = true;
      }
    }
    return (ticks, distance);
  }

  private static WorkoutProgramRunDto ToDto(WorkoutProgramRun run) => new(
    run.Id, run.UserProfileId, run.Status.ToString(), run.StartedAtUtc, run.EndedAtUtc, run.Version,
    run.Schedule?.StartDate, (int)(run.Schedule?.Weekdays ?? WeekdayFlags.None), run.Schedule?.TimeZoneId);

  private static void ValidateOperationId(Guid operationId)
  {
    if (operationId == Guid.Empty) throw new ArgumentException("OperationId is required.");
  }

  private static PersistenceWriteOperation WriteOperation(
    Guid operationId, string type, int statusCode, object outcome, DateTimeOffset now, string fingerprint) =>
    new(operationId, type, statusCode, JsonSerializer.Serialize(outcome, WebJsonOptions), now, fingerprint);

  private static IResult Replay(OperationReceipt receipt, string expectedType, string fingerprint) =>
    receipt.OperationType == expectedType && receipt.RequestFingerprint == fingerprint
      ? Results.Content(receipt.OutcomeJson, "application/json", statusCode: receipt.StatusCode)
      : OperationConflict();

  private static IResult Validation(ArgumentException exception) =>
    TypedResults.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [exception.Message] });

  private static IResult OperationConflict() =>
    TypedResults.Conflict(new { message = "That operation ID was already used for another action or request." });
}
