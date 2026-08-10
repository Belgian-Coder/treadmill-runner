using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Planning;

public static class CalendarPlanningEndpoints
{
  private const int MaximumRangeDays = 62;
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public static IEndpointRouteBuilder MapCalendarPlanning(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/calendar");
    group.MapGet("/series", ListSeriesAsync);
    group.MapPost("/series", CreateSeriesAsync);
    group.MapPut("/series/{id:guid}", UpdateSeriesAsync);
    group.MapPost("/series/{id:guid}/occurrences/{date}/move", MoveOccurrenceAsync);
    group.MapPost("/series/{id:guid}/occurrences/{date}/delete", DeleteOccurrenceAsync);
    group.MapPost("/series/{id:guid}/delete-group", DeleteGroupAsync);
    group.MapPost("/program-runs/{runId:guid}/schedule/preview", PreviewProgramScheduleChangeAsync);
    group.MapPost("/program-runs/{runId:guid}/schedule/apply", ApplyProgramScheduleChangeAsync);
    group.MapPost("/program-runs/{runId:guid}/default-days/preview", PreviewDefaultDaysChangeAsync);
    group.MapPost("/program-runs/{runId:guid}/default-days/apply", ApplyDefaultDaysChangeAsync);
    group.MapGet("/{profileId:guid}", GetEffectiveRangeAsync);
    group.MapPost("/{profileId:guid}/days/{date}/selection", SaveSelectionAsync);
    return endpoints;
  }

  private static async Task<IResult> ListSeriesAsync(
    Guid profileId,
    ICalendarStore store,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<VersionedCalendarSeries> series = await store.ListByProfileAsync(profileId, cancellationToken);
    return TypedResults.Ok(series.Select(ToDto).ToArray());
  }

  private static async Task<IResult> CreateSeriesAsync(
    CalendarSeriesSaveRequest request,
    ICalendarStore store,
    IProfileStore profileStore,
    IWorkoutStore workoutStore,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      CalendarSeriesDefinition definition = CreateDefinition(Guid.NewGuid(), request);
      requestFingerprint = SeriesFingerprint(targetSeriesId: null, request);
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.series.create", requestFingerprint);
      }
      if (await ValidateReferencesAsync(definition, profileStore, workoutStore, cancellationToken) is { } invalidReference)
      {
        return invalidReference;
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      var expected = new VersionedCalendarSeries(definition, 1);
      VersionedCalendarSeries saved = await store.CreateAsync(
        definition,
        now,
        WriteOperation(request.OperationId, "calendar.series.create", StatusCodes.Status201Created, ToDto(expected), now, requestFingerprint),
        cancellationToken);
      return TypedResults.Created($"/api/planning/calendar/series/{saved.Series.Id}", ToDto(saved));
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.series.create", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> UpdateSeriesAsync(
    Guid id,
    CalendarSeriesSaveRequest request,
    ICalendarStore store,
    IProfileStore profileStore,
    IWorkoutStore workoutStore,
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
        throw new ArgumentException("ExpectedVersion is required for calendar updates.");
      }

      requestFingerprint = SeriesFingerprint(id, request);
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.series.update", requestFingerprint);
      }
      VersionedCalendarSeries existing = await store.FindAsync(id, cancellationToken)
        ?? throw new KeyNotFoundException($"Calendar series {id} was not found.");
      if (request.ProfileId != existing.Series.UserProfileId)
      {
        throw new ArgumentException("A calendar schedule cannot be transferred to another profile.");
      }
      CalendarSeriesDefinition definition = CreateDefinition(id, request, existing.Series.ScheduleGroupId);
      if (await ValidateReferencesAsync(definition, profileStore, workoutStore, cancellationToken) is { } invalidReference)
      {
        return invalidReference;
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      var expected = new VersionedCalendarSeries(definition, request.ExpectedVersion.Value + 1);
      VersionedCalendarSeries saved = await store.UpdateAsync(
        definition,
        request.ExpectedVersion.Value,
        WriteOperation(request.OperationId, "calendar.series.update", StatusCodes.Status200OK, ToDto(expected), now, requestFingerprint),
        cancellationToken);
      return TypedResults.Ok(ToDto(saved));
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return TypedResults.Conflict(new { message = "The calendar series changed in another client. Reload and try again." });
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.series.update", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> MoveOccurrenceAsync(
    Guid id,
    DateOnly date,
    CalendarOccurrenceMoveRequest request,
    ICalendarStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      ValidateExpectedVersion(request.ExpectedVersion);
      IReadOnlyDictionary<Guid, int>? expectedGroupVersions = null;
      if (request.MoveFollowing)
      {
        ArgumentNullException.ThrowIfNull(request.ExpectedSegments);
        if (request.ExpectedSegments.Count == 0 || request.ExpectedSegments.Any(static segment => segment.SeriesId == Guid.Empty || segment.Version < 0) ||
            request.ExpectedSegments.Select(static segment => segment.SeriesId).Distinct().Count() != request.ExpectedSegments.Count)
        {
          throw new ArgumentException("Every current workout-group segment and version is required for a following-session move.", nameof(request.ExpectedSegments));
        }
        expectedGroupVersions = request.ExpectedSegments.ToDictionary(static segment => segment.SeriesId, static segment => segment.Version);
      }
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        SeriesId = id,
        SourceDate = date,
        request.TargetDate,
        request.MoveFollowing,
        request.ExpectedVersion,
        ExpectedSegments = request.ExpectedSegments?.OrderBy(static segment => segment.SeriesId),
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.occurrence.move", requestFingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      await store.MoveOccurrenceAsync(
        id,
        date,
        request.TargetDate,
        request.MoveFollowing,
        request.ExpectedVersion,
        expectedGroupVersions,
        Guid.NewGuid(),
        WriteOperation(request.OperationId, "calendar.occurrence.move", StatusCodes.Status204NoContent, new { }, now, requestFingerprint),
        cancellationToken);
      return TypedResults.NoContent();
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return CalendarConflict();
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.occurrence.move", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> DeleteOccurrenceAsync(
    Guid id,
    DateOnly date,
    CalendarOccurrenceDeleteRequest request,
    ICalendarStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      ValidateExpectedVersion(request.ExpectedVersion);
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        SeriesId = id,
        Date = date,
        request.ExpectedVersion,
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.occurrence.delete", requestFingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      await store.DeleteOccurrenceAsync(
        id,
        date,
        request.ExpectedVersion,
        WriteOperation(request.OperationId, "calendar.occurrence.delete", StatusCodes.Status204NoContent, new { }, now, requestFingerprint),
        cancellationToken);
      return TypedResults.NoContent();
    }
    catch (KeyNotFoundException)
    {
      return TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return CalendarConflict();
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.occurrence.delete", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> DeleteGroupAsync(
    Guid id,
    CalendarGroupDeleteRequest request,
    ICalendarStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      ArgumentNullException.ThrowIfNull(request.ExpectedSegments);
      if (request.ExpectedSegments.Count == 0 || request.ExpectedSegments.Any(static segment => segment.SeriesId == Guid.Empty || segment.Version < 0) ||
          request.ExpectedSegments.Select(static segment => segment.SeriesId).Distinct().Count() != request.ExpectedSegments.Count)
      {
        throw new ArgumentException("Expected group segments must contain unique series IDs and non-negative versions.");
      }
      var expectedVersions = request.ExpectedSegments.ToDictionary(static segment => segment.SeriesId, static segment => segment.Version);
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        SeriesId = id,
        ExpectedSegments = request.ExpectedSegments.OrderBy(static segment => segment.SeriesId),
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.group.delete", requestFingerprint);
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      bool deleted = await store.DeleteGroupAsync(
        id,
        expectedVersions,
        WriteOperation(
          request.OperationId,
          "calendar.group.delete",
          StatusCodes.Status204NoContent,
          new { },
          now,
          requestFingerprint) with
        { NotFoundStatusCode = StatusCodes.Status404NotFound },
        cancellationToken);
      return deleted ? TypedResults.NoContent() : TypedResults.NotFound();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (DbUpdateConcurrencyException)
    {
      return CalendarConflict();
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.group.delete", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static async Task<IResult> GetEffectiveRangeAsync(
    Guid profileId,
    DateOnly from,
    DateOnly to,
    ICalendarStore calendarStore,
    IWorkoutProgramStore programStore,
    IWorkoutStore workoutStore,
    CancellationToken cancellationToken)
  {
    if (to < from || to.DayNumber - from.DayNumber + 1 > MaximumRangeDays)
    {
      return TypedResults.ValidationProblem(new Dictionary<string, string[]>
      {
        ["range"] = [$"Choose an ordered calendar range no longer than {MaximumRangeDays} days."],
      });
    }

    IReadOnlyList<VersionedCalendarSeries> storedSeries = await calendarStore.ListByProfileAsync(profileId, cancellationToken);
    CalendarSeriesDefinition[] definitions = storedSeries.Select(static item => item.Series).ToArray();
    IReadOnlyDictionary<Guid, VersionedCalendarSeries> seriesById = storedSeries.ToDictionary(static item => item.Series.Id);
    IReadOnlyList<StoredWorkoutProgramProgress> programs = await programStore.ListActiveScheduledAsync(profileId, cancellationToken);
    IReadOnlyDictionary<DateOnly, StoredTrainingDaySelection> selections = (await calendarStore.ListSelectionsAsync(
      profileId, from, to, cancellationToken)).ToDictionary(static selection => selection.Date);
    int dayCount = to.DayNumber - from.DayNumber + 1;
    DateOnly[] rangeDates = Enumerable.Range(0, dayCount).Select(from.AddDays).ToArray();
    IReadOnlyDictionary<DateOnly, TrainingDaySelection> effectiveByDate = rangeDates
      .ToDictionary(date => date, date => TrainingDaySelectionResolver.ResolveDay(definitions, profileId, date));
    var scheduledProgramItems = programs
      .Where(static program => program.Run is { Status: WorkoutProgramRunStatus.Active, Schedule: not null })
      .SelectMany(program => WorkoutProgramScheduleProjector
        .ProjectAll(program.Program.CurrentRevision, program.Run!, program.ScheduleOverrides, program.ExtraOccurrences)
        .Where(item => item.Date >= from && item.Date <= to)
        .Select(item => new { Program = program, Scheduled = item }))
      .GroupBy(static item => item.Scheduled.Date)
      .ToDictionary(static group => group.Key, static group => group.ToArray());

    var revisionIds = effectiveByDate.Values
      .SelectMany(static selection => selection.Options)
      .Select(static option => option.WorkoutRevisionId)
      .Concat(scheduledProgramItems.Values
        .SelectMany(static items => items)
        .SelectMany(static item => item.Scheduled.Item.Alternatives
          .Select(static alternative => alternative.WorkoutRevisionId)
          .Prepend(item.Scheduled.Item.WorkoutRevisionId)))
      .Distinct()
      .ToArray();
    IReadOnlyDictionary<Guid, StoredWorkoutRevision> revisionCache = (await workoutStore.FindRevisionsAsync(
      revisionIds, cancellationToken)).ToDictionary(static revision => revision.Id);
    IReadOnlyDictionary<Guid, string> titleCache = revisionCache.ToDictionary(
      static pair => pair.Key,
      static pair => ReadWorkoutTitle(pair.Value.DefinitionJson));

    List<CalendarDayDto> days = [];
    foreach (DateOnly date in rangeDates)
    {
      TrainingDaySelection effective = effectiveByDate[date];
      scheduledProgramItems.TryGetValue(date, out var programItems);
      if (effective.Options.Count == 0 && programItems is null)
      {
        continue;
      }

      selections.TryGetValue(date, out StoredTrainingDaySelection? selection);
      var options = new List<CalendarOptionDto>(effective.Options.Count + (programItems?.Length ?? 0));
      foreach (TrainingDayOption option in effective.Options)
      {
        if (!revisionCache.TryGetValue(option.WorkoutRevisionId, out StoredWorkoutRevision? revision) ||
            !seriesById.TryGetValue(option.SeriesId, out VersionedCalendarSeries? series))
        {
          continue;
        }

        options.Add(new CalendarOptionDto(
          option.SeriesId,
          series.Series.ScheduleGroupId,
          series.Series.Name,
          option.WorkoutRevisionId,
          titleCache[option.WorkoutRevisionId],
          revision.RevisionNumber,
          option.DisplayOrder,
          selection?.CalendarSeriesId == option.SeriesId && selection.WorkoutRevisionId == option.WorkoutRevisionId));
      }

      foreach (var programItem in (programItems ?? []).Reverse())
      {
        WorkoutProgramRun run = programItem.Program.Run!;
        WorkoutProgramItem item = programItem.Scheduled.Item;
        IEnumerable<(Guid RevisionId, int DisplayOrder)> choices = item.Alternatives
          .Select(static alternative => (alternative.WorkoutRevisionId, alternative.DisplayOrder))
          .Prepend((item.WorkoutRevisionId, 0));
        foreach ((Guid revisionId, int displayOrder) in choices.Reverse())
        {
          if (!revisionCache.TryGetValue(revisionId, out StoredWorkoutRevision? revision))
          {
            continue;
          }
          options.Insert(0, new CalendarOptionDto(
            run.Id,
            run.Id,
            programItem.Program.Program.CurrentRevision.Name,
            revisionId,
            titleCache[revisionId],
            revision.RevisionNumber,
            displayOrder,
            IsSelected: !programItem.Scheduled.IsRepeat && item.Alternatives.Count == 0,
            Source: "Program",
            ProgramRunId: run.Id,
            ProgramItemId: item.Id,
            ProgramPosition: item.Position,
            ProgramTotal: programItem.Program.Program.CurrentRevision.Items.Count,
            WeekNumber: item.WeekNumber,
            Phase: item.Phase,
            ProgramRunVersion: run.Version,
            IsRepeat: programItem.Scheduled.IsRepeat,
            ExtraOccurrenceId: programItem.Scheduled.ExtraOccurrenceId,
            OriginalDate: programItem.Scheduled.OriginalDate,
            IsCompleted: programItem.Program.CompletedItemIds?.Contains(item.Id) == true,
            ProgramWeekdayMask: (int?)run.Schedule?.Weekdays));
        }
      }

      days.Add(new CalendarDayDto(date, options));
    }

    return TypedResults.Ok(new CalendarRangeDto(profileId, from, to, days));
  }

  private static async Task<IResult> PreviewProgramScheduleChangeAsync(
    Guid runId,
    WorkoutProgramScheduleChangeRequest request,
    IWorkoutProgramStore store,
    CancellationToken cancellationToken)
  {
    try
    {
      WorkoutProgramScheduleAction action = ParseScheduleAction(request.Action);
      WorkoutProgramScheduleChangePreview preview = await store.PreviewScheduleChangeAsync(
        request.ProfileId, runId, request.ProgramItemId, action, request.TargetDate, cancellationToken);
      return TypedResults.Ok(ToDto(preview));
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { message = exception.Message }); }
  }

  private static async Task<IResult> ApplyProgramScheduleChangeAsync(
    Guid runId,
    WorkoutProgramScheduleChangeRequest request,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    const string operationType = "calendar.program.schedule.change";
    try
    {
      if (request.OperationId is not { } operationId) throw new ArgumentException("OperationId is required.");
      ValidateOperationId(operationId);
      if (request.ExpectedRunVersion is not > 0) throw new ArgumentException("ExpectedRunVersion must be greater than zero.");
      WorkoutProgramScheduleAction action = ParseScheduleAction(request.Action);
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        RunId = runId,
        request.ProfileId,
        request.ProgramItemId,
        Action = action.ToString(),
        request.TargetDate,
        request.ExpectedRunVersion,
      });
      if (await receiptStore.FindAsync(operationId, cancellationToken) is { } receipt)
        return Replay(receipt, operationType, requestFingerprint);
      DateTimeOffset now = timeProvider.GetUtcNow();
      WorkoutProgramScheduleChangePreview preview = await store.ApplyScheduleChangeAsync(
        request.ProfileId,
        runId,
        request.ProgramItemId,
        action,
        request.TargetDate,
        request.ExpectedRunVersion.Value,
        WriteOperation(operationId, operationType, StatusCodes.Status200OK, new { }, now, requestFingerprint),
        cancellationToken);
      return TypedResults.Ok(ToDto(preview));
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { message = exception.Message }); }
    catch (DbUpdateConcurrencyException) { return CalendarConflict(); }
    catch (OperationReplayException replay) { return Replay(replay, operationType, requestFingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static async Task<IResult> PreviewDefaultDaysChangeAsync(
    Guid runId,
    WorkoutProgramDefaultDaysRequest request,
    IWorkoutProgramStore store,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      DateOnly today = await store.GetScheduleLocalDateAsync(
        request.ProfileId, runId, timeProvider.GetUtcNow(), cancellationToken);
      WorkoutProgramDefaultDaysPreview preview = await store.PreviewDefaultDaysChangeAsync(
        request.ProfileId,
        runId,
        (WeekdayFlags)request.WeekdayMask,
        request.EffectiveDate,
        today,
        cancellationToken);
      return TypedResults.Ok(ToDto(preview));
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { message = exception.Message }); }
  }

  private static async Task<IResult> ApplyDefaultDaysChangeAsync(
    Guid runId,
    WorkoutProgramDefaultDaysRequest request,
    IWorkoutProgramStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    const string operationType = "calendar.program.default-days.change";
    try
    {
      if (request.OperationId is not { } operationId) throw new ArgumentException("OperationId is required.");
      ValidateOperationId(operationId);
      if (request.ExpectedRunVersion is not > 0) throw new ArgumentException("ExpectedRunVersion must be greater than zero.");
      ArgumentException.ThrowIfNullOrWhiteSpace(request.ExpectedRevision);
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        RunId = runId,
        request.ProfileId,
        request.WeekdayMask,
        request.EffectiveDate,
        request.ExpectedRunVersion,
        request.ExpectedRevision,
      });
      if (await receiptStore.FindAsync(operationId, cancellationToken) is { } receipt)
        return Replay(receipt, operationType, requestFingerprint);
      DateTimeOffset now = timeProvider.GetUtcNow();
      DateOnly today = await store.GetScheduleLocalDateAsync(
        request.ProfileId, runId, timeProvider.GetUtcNow(), cancellationToken);
      WorkoutProgramDefaultDaysPreview outcome = await store.ApplyDefaultDaysChangeAsync(
        request.ProfileId,
        runId,
        (WeekdayFlags)request.WeekdayMask,
        request.EffectiveDate,
        today,
        request.ExpectedRunVersion.Value,
        request.ExpectedRevision,
        WriteOperation(operationId, operationType, StatusCodes.Status200OK, new { }, now, requestFingerprint),
        cancellationToken);
      return TypedResults.Ok(ToDto(outcome));
    }
    catch (ArgumentException exception) { return Validation(exception); }
    catch (KeyNotFoundException exception) { return TypedResults.NotFound(new { message = exception.Message }); }
    catch (DbUpdateConcurrencyException) { return CalendarConflict(); }
    catch (OperationReplayException replay) { return Replay(replay, operationType, requestFingerprint); }
    catch (OperationScopeConflictException) { return OperationConflict(); }
  }

  private static async Task<IResult> SaveSelectionAsync(
    Guid profileId,
    DateOnly date,
    CalendarSelectionRequest request,
    ICalendarStore store,
    IOperationReceiptStore receiptStore,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    string requestFingerprint = string.Empty;
    try
    {
      ValidateOperationId(request.OperationId);
      requestFingerprint = PlanningOperationFingerprint.Compute(new
      {
        ProfileId = profileId,
        Date = date,
        request.SeriesId,
        request.WorkoutRevisionId,
      });
      if (await receiptStore.FindAsync(request.OperationId, cancellationToken) is { } receipt)
      {
        return Replay(receipt, "calendar.day.select", requestFingerprint);
      }
      IReadOnlyList<VersionedCalendarSeries> storedSeries = await store.ListByProfileAsync(profileId, cancellationToken);
      TrainingDaySelection effective = TrainingDaySelectionResolver.ResolveDay(
        storedSeries.Select(static item => item.Series),
        profileId,
        date);
      bool isValidOption = effective.Options.Any(option =>
        option.SeriesId == request.SeriesId && option.WorkoutRevisionId == request.WorkoutRevisionId);
      if (!isValidOption)
      {
        return TypedResults.ValidationProblem(new Dictionary<string, string[]>
        {
          ["workoutRevisionId"] = ["Choose an effective workout option for that profile and date."],
        });
      }

      DateTimeOffset now = timeProvider.GetUtcNow();
      await store.SaveSelectionAsync(new StoredTrainingDaySelection(
        profileId,
        date,
        request.SeriesId,
        request.WorkoutRevisionId,
        now), WriteOperation(
          request.OperationId,
          "calendar.day.select",
          StatusCodes.Status204NoContent,
          new { },
          now,
          requestFingerprint), cancellationToken);
      return TypedResults.NoContent();
    }
    catch (ArgumentException exception)
    {
      return Validation(exception);
    }
    catch (OperationReplayException replay)
    {
      return Replay(replay, "calendar.day.select", requestFingerprint);
    }
    catch (OperationScopeConflictException)
    {
      return OperationConflict();
    }
  }

  private static CalendarSeriesDefinition CreateDefinition(
    Guid id,
    CalendarSeriesSaveRequest request,
    Guid? scheduleGroupId = null)
  {
    if (request.Alternatives is null || request.Exceptions is null)
    {
      throw new ArgumentException("Calendar alternatives and exceptions cannot be null.");
    }

    if (request.WeekdayMask == 0 || (request.WeekdayMask & ~127) != 0)
    {
      throw new ArgumentException("Select at least one valid weekday.");
    }

    WorkoutAlternative[] alternatives = request.Alternatives.Select(CreateAlternative).ToArray();
    CalendarExceptionDefinition[] exceptions = request.Exceptions.Select(CreateException).ToArray();
    return new CalendarSeriesDefinition(
      id,
      request.ProfileId,
      request.Name,
      request.TimeZoneId,
      new WeeklyRecurrence(
        request.StartDate,
        request.EndDate,
        request.IntervalWeeks,
        (WeekdayFlags)request.WeekdayMask),
      alternatives,
      exceptions,
      scheduleGroupId);
  }

  private static WorkoutAlternative CreateAlternative(CalendarAlternativeRequest? alternative)
  {
    if (alternative is null)
    {
      throw new ArgumentException("Calendar alternatives cannot contain null entries.");
    }

    return new WorkoutAlternative(alternative.WorkoutRevisionId, alternative.DisplayOrder);
  }

  private static CalendarExceptionDefinition CreateException(CalendarExceptionRequest? exception)
  {
    if (exception is null)
    {
      throw new ArgumentException("Calendar exceptions cannot contain null entries.");
    }

    if (exception.Alternatives is null)
    {
      throw new ArgumentException("Calendar exception alternatives cannot be null.");
    }

    return new CalendarExceptionDefinition(
      exception.Date,
      ParseExceptionKind(exception.Kind),
      exception.Alternatives.Select(CreateAlternative).ToArray());
  }

  private static async Task<IResult?> ValidateReferencesAsync(
    CalendarSeriesDefinition definition,
    IProfileStore profileStore,
    IWorkoutStore workoutStore,
    CancellationToken cancellationToken)
  {
    if (await profileStore.FindAsync(definition.UserProfileId, cancellationToken) is not { IsArchived: false })
    {
      return TypedResults.NotFound(new { message = "The calendar profile does not exist or is archived." });
    }

    IEnumerable<Guid> revisionIds = definition.Alternatives.Select(static option => option.WorkoutRevisionId)
      .Concat(definition.Exceptions.SelectMany(static exception => exception.Alternatives)
        .Select(static option => option.WorkoutRevisionId))
      .Distinct();
    foreach (Guid revisionId in revisionIds)
    {
      if (await workoutStore.FindRevisionAsync(revisionId, cancellationToken) is null)
      {
        return TypedResults.NotFound(new { message = $"Workout revision {revisionId} does not exist." });
      }
    }

    return null;
  }

  private static CalendarSeriesDto ToDto(VersionedCalendarSeries item) => new(
    item.Series.Id,
    item.Series.ScheduleGroupId,
    item.Series.UserProfileId,
    item.Series.Name,
    item.Series.TimeZoneId,
    item.Series.Recurrence.StartDate,
    item.Series.Recurrence.EndDate,
    item.Series.Recurrence.IntervalWeeks,
    (int)item.Series.Recurrence.Weekdays,
    item.Version,
    item.Series.Alternatives.Select(static option => new CalendarAlternativeRequest(
      option.WorkoutRevisionId,
      option.DisplayOrder)).ToArray(),
    item.Series.Exceptions.Select(static exception => new CalendarExceptionRequest(
      exception.Date,
      exception.Kind.ToString(),
      exception.Alternatives.Select(static option => new CalendarAlternativeRequest(
        option.WorkoutRevisionId,
        option.DisplayOrder)).ToArray())).ToArray());

  private static string ReadWorkoutTitle(string definitionJson)
  {
    using JsonDocument document = JsonDocument.Parse(definitionJson);
    return document.RootElement.GetProperty("title").GetString() ?? "Untitled workout";
  }

  private static WorkoutProgramScheduleAction ParseScheduleAction(string? value)
  {
    if (int.TryParse(value, out _) ||
        !Enum.TryParse(value, ignoreCase: true, out WorkoutProgramScheduleAction action) ||
        !Enum.IsDefined(action))
      throw new ArgumentException("Action must be MoveOne, MoveFollowing, Skip, Restore, Repeat, or RepeatAndShift.");
    return action;
  }

  private static WorkoutProgramScheduleChangePreviewDto ToDto(WorkoutProgramScheduleChangePreview preview) => new(
    preview.RunId,
    preview.ProgramItemId,
    preview.Action.ToString(),
    preview.RunVersion,
    preview.CanApply,
    preview.Message,
    preview.Impacts.Select(static impact => new WorkoutProgramScheduleImpactDto(
      impact.ProgramItemId, impact.Position, impact.CurrentDate, impact.NewDate, impact.IsRepeat)).ToArray(),
    preview.CollisionDates);

  private static WorkoutProgramDefaultDaysPreviewDto ToDto(WorkoutProgramDefaultDaysPreview preview) => new(
    preview.RunId,
    preview.RunVersion,
    preview.CurrentWeekdayMask,
    preview.NewWeekdayMask,
    preview.EffectiveDate,
    preview.CanApply,
    preview.Message,
    preview.Revision,
    preview.Impacts.Select(static impact => new WorkoutProgramDefaultDaysImpactDto(
      impact.ProgramItemId, impact.Position, impact.CurrentDate, impact.NewDate)).ToArray(),
    preview.CollisionDates,
    preview.PreservedExceptionCount);

  private static void ValidateOperationId(Guid operationId)
  {
    if (operationId == Guid.Empty)
    {
      throw new ArgumentException("OperationId cannot be empty.");
    }
  }

  private static void ValidateExpectedVersion(int expectedVersion)
  {
    if (expectedVersion <= 0)
    {
      throw new ArgumentException("ExpectedVersion must be greater than zero.");
    }
  }

  private static IResult CalendarConflict() =>
    TypedResults.Conflict(new { message = "The schedule changed in another client. Reload and try again." });

  private static IResult Validation(ArgumentException exception) => TypedResults.ValidationProblem(
    new Dictionary<string, string[]> { ["request"] = [exception.Message] });

  private static string SeriesFingerprint(Guid? targetSeriesId, CalendarSeriesSaveRequest request) =>
    PlanningOperationFingerprint.Compute(new
    {
      TargetSeriesId = targetSeriesId,
      request.ProfileId,
      request.Name,
      request.TimeZoneId,
      request.StartDate,
      request.EndDate,
      request.IntervalWeeks,
      request.WeekdayMask,
      request.Alternatives,
      request.Exceptions,
      request.ExpectedVersion,
    });

  private static CalendarExceptionKind ParseExceptionKind(string? value)
  {
    if (int.TryParse(value, out _) ||
        !Enum.TryParse(value, ignoreCase: true, out CalendarExceptionKind kind) ||
        !Enum.IsDefined(kind))
    {
      throw new ArgumentException("Exception kind must be Skip, Replace, or Add.");
    }

    return kind;
  }

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
