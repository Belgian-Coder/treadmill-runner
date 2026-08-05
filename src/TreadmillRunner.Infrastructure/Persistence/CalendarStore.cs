using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Calendar;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record VersionedCalendarSeries(CalendarSeriesDefinition Series, int Version);
public sealed record StoredTrainingDaySelection(Guid UserProfileId, DateOnly Date, Guid CalendarSeriesId, Guid WorkoutRevisionId, DateTimeOffset SelectedAtUtc);

public interface ICalendarStore
{
  Task<IReadOnlyList<VersionedCalendarSeries>> ListByProfileAsync(Guid userProfileId, CancellationToken cancellationToken = default);
  Task<VersionedCalendarSeries?> FindAsync(Guid id, CancellationToken cancellationToken = default);
  Task<VersionedCalendarSeries> CreateAsync(CalendarSeriesDefinition series, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<VersionedCalendarSeries> UpdateAsync(CalendarSeriesDefinition series, int expectedVersion, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> DeleteAsync(Guid id, int expectedVersion, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task MoveOccurrenceAsync(Guid seriesId, DateOnly sourceDate, DateOnly targetDate, bool moveFollowing, int expectedVersion, IReadOnlyDictionary<Guid, int>? expectedGroupVersions, Guid continuationSeriesId, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task DeleteOccurrenceAsync(Guid seriesId, DateOnly date, int expectedVersion, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> DeleteGroupAsync(Guid seriesId, IReadOnlyDictionary<Guid, int> expectedVersions, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task SaveSelectionAsync(StoredTrainingDaySelection selection, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<StoredTrainingDaySelection?> FindSelectionAsync(Guid userProfileId, DateOnly date, CancellationToken cancellationToken = default);
}

public sealed class CalendarStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : ICalendarStore
{
  public async Task<IReadOnlyList<VersionedCalendarSeries>> ListByProfileAsync(Guid userProfileId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var entities = await Query(context).AsNoTracking().Where(series => series.UserProfileId == userProfileId)
      .OrderBy(series => series.Name)
      .ToListAsync(cancellationToken);
    return entities.Select(Map).ToArray();
  }

  public async Task<VersionedCalendarSeries?> FindAsync(Guid id, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var entity = await Query(context).AsNoTracking().SingleOrDefaultAsync(series => series.Id == id, cancellationToken);
    return entity is null ? null : Map(entity);
  }

  public async Task<VersionedCalendarSeries> CreateAsync(
    CalendarSeriesDefinition series,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(series);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var entity = CreateEntity(series, nowUtc, version: 1);
    context.CalendarSeries.Add(entity);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return Map(entity);
  }

  public async Task<VersionedCalendarSeries> UpdateAsync(
    CalendarSeriesDefinition series,
    int expectedVersion,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(series);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    var entity = await context.CalendarSeries.AsNoTracking()
      .SingleOrDefaultAsync(candidate => candidate.Id == series.Id, cancellationToken)
      ?? throw new KeyNotFoundException($"Calendar series {series.Id} was not found.");
    RequireVersion(entity.Version, expectedVersion);

    await context.CalendarSeriesOptions
      .Where(option => option.CalendarSeriesId == entity.Id)
      .ExecuteDeleteAsync(cancellationToken);
    await context.CalendarExceptionOptions
      .Where(option => option.CalendarException.CalendarSeriesId == entity.Id)
      .ExecuteDeleteAsync(cancellationToken);
    await context.CalendarExceptions
      .Where(exception => exception.CalendarSeriesId == entity.Id)
      .ExecuteDeleteAsync(cancellationToken);
    var updated = await context.CalendarSeries
      .Where(candidate => candidate.Id == series.Id && candidate.Version == expectedVersion)
      .ExecuteUpdateAsync(setters => setters
        .SetProperty(candidate => candidate.Name, series.Name)
        .SetProperty(candidate => candidate.TimeZoneId, series.TimeZoneId)
        .SetProperty(candidate => candidate.StartDate, series.Recurrence.StartDate)
        .SetProperty(candidate => candidate.EndDate, series.Recurrence.EndDate)
        .SetProperty(candidate => candidate.IntervalWeeks, series.Recurrence.IntervalWeeks)
        .SetProperty(candidate => candidate.WeekdayMask, (int)series.Recurrence.Weekdays)
        .SetProperty(candidate => candidate.Version, expectedVersion + 1),
        cancellationToken);
    if (updated != 1)
    {
      throw new DbUpdateConcurrencyException($"Expected version {expectedVersion}, but the calendar series changed concurrently.");
    }

    context.CalendarSeriesOptions.AddRange(CreateOptions(series.Id, series.Alternatives));
    context.CalendarExceptions.AddRange(CreateExceptions(series.Id, series.Exceptions));
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return new VersionedCalendarSeries(series, expectedVersion + 1);
  }

  public async Task<bool> DeleteAsync(Guid id, int expectedVersion, PersistenceWriteOperation operation, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var entity = await context.CalendarSeries.SingleOrDefaultAsync(candidate => candidate.Id == id, cancellationToken);
    if (entity is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }

    RequireVersion(entity.Version, expectedVersion);
    context.CalendarSeries.Remove(entity);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  public async Task MoveOccurrenceAsync(
    Guid seriesId,
    DateOnly sourceDate,
    DateOnly targetDate,
    bool moveFollowing,
    int expectedVersion,
    IReadOnlyDictionary<Guid, int>? expectedGroupVersions,
    Guid continuationSeriesId,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    if (seriesId == Guid.Empty) throw new ArgumentException("Series ID is required.", nameof(seriesId));
    if (continuationSeriesId == Guid.Empty) throw new ArgumentException("Continuation series ID is required.", nameof(continuationSeriesId));
    if (sourceDate == targetDate) throw new ArgumentException("Choose a different date for the moved session.", nameof(targetDate));
    int dayOffset = targetDate.DayNumber - sourceDate.DayNumber;
    if (Math.Abs(dayOffset) > 365) throw new ArgumentOutOfRangeException(nameof(targetDate), "A session can move by at most 365 days.");

    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    CalendarSeriesEntity selected = await Query(context)
      .SingleOrDefaultAsync(candidate => candidate.Id == seriesId, cancellationToken)
      ?? throw new KeyNotFoundException($"Calendar series {seriesId} was not found.");
    RequireVersion(selected.Version, expectedVersion);
    List<CalendarSeriesEntity> group = await Query(context)
      .Where(candidate => candidate.UserProfileId == selected.UserProfileId && candidate.ScheduleGroupId == selected.ScheduleGroupId)
      .OrderBy(candidate => candidate.StartDate)
      .ToListAsync(cancellationToken);
    if (moveFollowing && (expectedGroupVersions is null || expectedGroupVersions.Count != group.Count ||
        group.Any(segment => !expectedGroupVersions.TryGetValue(segment.Id, out int version) || version != segment.Version)))
    {
      throw new DbUpdateConcurrencyException("The workout group changed after it was loaded.");
    }
    WorkoutAlternative[] effectiveAlternatives = EffectiveAlternatives(selected, sourceDate);
    if (effectiveAlternatives.Length == 0)
    {
      throw new ArgumentException("The selected schedule has no session on the source date.", nameof(sourceDate));
    }
    if (moveFollowing && !Map(selected).Series.Recurrence.OccursOn(sourceDate))
    {
      throw new ArgumentException(
        "An individually added session can only be moved by itself; it cannot shift the recurring workout group.",
        nameof(sourceDate));
    }

    bool targetCollision = HasOccurrence(group, selected.UserProfileId, targetDate);
    if ((!moveFollowing && targetCollision) || (moveFollowing && targetDate < sourceDate && targetCollision))
    {
      throw new ArgumentException("The target date already contains a session from this workout group.", nameof(targetDate));
    }
    if (moveFollowing && targetDate < sourceDate && HasBackwardContinuationCollision(group, selected, sourceDate, targetDate, dayOffset, continuationSeriesId))
    {
      throw new ArgumentException(
        "Moving this and later sessions to that earlier date would overlap existing sessions in the workout group. Move only this session or choose a later date.",
        nameof(targetDate));
    }

    if (moveFollowing)
    {
      MoveFollowingOccurrences(context, selected, group, sourceDate, targetDate, dayOffset, continuationSeriesId, operation.CreatedAtUtc);
      Guid[] groupIds = group.Select(static item => item.Id).Append(continuationSeriesId).Distinct().ToArray();
      await context.TrainingDaySelections
        .Where(selection => selection.UserProfileId == selected.UserProfileId &&
          groupIds.Contains(selection.CalendarSeriesId) && selection.LocalDate >= sourceDate)
        .ExecuteDeleteAsync(cancellationToken);
    }
    else
    {
      UpsertException(context, selected, sourceDate, CalendarExceptionKind.Skip, []);
      UpsertException(context, selected, targetDate, CalendarExceptionKind.Add, effectiveAlternatives);
      selected.Version++;
      await context.TrainingDaySelections
        .Where(selection => selection.UserProfileId == selected.UserProfileId &&
          selection.CalendarSeriesId == selected.Id &&
          (selection.LocalDate == sourceDate || selection.LocalDate == targetDate))
        .ExecuteDeleteAsync(cancellationToken);
    }

    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
  }

  public async Task DeleteOccurrenceAsync(
    Guid seriesId,
    DateOnly date,
    int expectedVersion,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    CalendarSeriesEntity selected = await Query(context)
      .SingleOrDefaultAsync(candidate => candidate.Id == seriesId, cancellationToken)
      ?? throw new KeyNotFoundException($"Calendar series {seriesId} was not found.");
    RequireVersion(selected.Version, expectedVersion);
    if (EffectiveAlternatives(selected, date).Length == 0)
    {
      throw new ArgumentException("The selected schedule has no session on that date.", nameof(date));
    }

    UpsertException(context, selected, date, CalendarExceptionKind.Skip, []);
    selected.Version++;
    await context.TrainingDaySelections
      .Where(selection => selection.UserProfileId == selected.UserProfileId &&
        selection.CalendarSeriesId == selected.Id && selection.LocalDate == date)
      .ExecuteDeleteAsync(cancellationToken);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
  }

  public async Task<bool> DeleteGroupAsync(
    Guid seriesId,
    IReadOnlyDictionary<Guid, int> expectedVersions,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    CalendarSeriesEntity? selected = await context.CalendarSeries
      .SingleOrDefaultAsync(candidate => candidate.Id == seriesId, cancellationToken);
    if (selected is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return false;
    }

    CalendarSeriesEntity[] group = await context.CalendarSeries
      .Where(candidate => candidate.UserProfileId == selected.UserProfileId && candidate.ScheduleGroupId == selected.ScheduleGroupId)
      .ToArrayAsync(cancellationToken);
    if (expectedVersions.Count != group.Length || group.Any(segment => !expectedVersions.TryGetValue(segment.Id, out int version) || version != segment.Version))
    {
      throw new DbUpdateConcurrencyException("The workout group changed after it was loaded.");
    }
    Guid[] groupIds = group.Select(static item => item.Id).ToArray();
    await context.TrainingDaySelections
      .Where(selection => groupIds.Contains(selection.CalendarSeriesId))
      .ExecuteDeleteAsync(cancellationToken);
    context.CalendarSeries.RemoveRange(group);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return true;
  }

  public async Task SaveSelectionAsync(StoredTrainingDaySelection selection, PersistenceWriteOperation operation, CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(selection);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var storedSeries = await Query(context).AsNoTracking().SingleOrDefaultAsync(
      candidate => candidate.Id == selection.CalendarSeriesId && candidate.UserProfileId == selection.UserProfileId,
      cancellationToken);
    var effectiveOptions = storedSeries is null
      ? []
      : TrainingDaySelectionResolver.ResolveDay([Map(storedSeries).Series], selection.UserProfileId, selection.Date).Options;
    if (!effectiveOptions.Any(option =>
      option.SeriesId == selection.CalendarSeriesId && option.WorkoutRevisionId == selection.WorkoutRevisionId))
    {
      throw new InvalidOperationException("The selected workout is not an option for this profile, series, and date.");
    }
    var entity = await context.TrainingDaySelections.SingleOrDefaultAsync(
      candidate => candidate.UserProfileId == selection.UserProfileId && candidate.LocalDate == selection.Date,
      cancellationToken);
    if (entity is null)
    {
      context.TrainingDaySelections.Add(new TrainingDaySelectionEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = selection.UserProfileId,
        LocalDate = selection.Date,
        CalendarSeriesId = selection.CalendarSeriesId,
        WorkoutRevisionId = selection.WorkoutRevisionId,
        SelectedAtUtc = selection.SelectedAtUtc,
      });
    }
    else
    {
      entity.CalendarSeriesId = selection.CalendarSeriesId;
      entity.WorkoutRevisionId = selection.WorkoutRevisionId;
      entity.SelectedAtUtc = selection.SelectedAtUtc;
    }

    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
  }

  public async Task<StoredTrainingDaySelection?> FindSelectionAsync(
    Guid userProfileId,
    DateOnly date,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.TrainingDaySelections.AsNoTracking()
      .Where(selection => selection.UserProfileId == userProfileId && selection.LocalDate == date)
      .Select(selection => new StoredTrainingDaySelection(
        selection.UserProfileId,
        selection.LocalDate,
        selection.CalendarSeriesId,
        selection.WorkoutRevisionId,
        selection.SelectedAtUtc))
      .SingleOrDefaultAsync(cancellationToken);
  }

  private static IQueryable<CalendarSeriesEntity> Query(TreadmillRunnerDbContext context) =>
    context.CalendarSeries
      .Include(series => series.Options)
      .Include(series => series.Exceptions)
      .ThenInclude(exception => exception.Options);

  private static CalendarSeriesEntity CreateEntity(CalendarSeriesDefinition series, DateTimeOffset nowUtc, int version) => new()
  {
    Id = series.Id,
    UserProfileId = series.UserProfileId,
    ScheduleGroupId = series.ScheduleGroupId,
    Name = series.Name,
    TimeZoneId = series.TimeZoneId,
    StartDate = series.Recurrence.StartDate,
    EndDate = series.Recurrence.EndDate,
    IntervalWeeks = series.Recurrence.IntervalWeeks,
    WeekdayMask = (int)series.Recurrence.Weekdays,
    CreatedAtUtc = nowUtc,
    Version = version,
    Options = CreateOptions(series.Id, series.Alternatives),
    Exceptions = CreateExceptions(series.Id, series.Exceptions),
  };

  private static List<CalendarSeriesOptionEntity> CreateOptions(Guid seriesId, IReadOnlyList<WorkoutAlternative> alternatives) =>
    alternatives.Select(alternative => new CalendarSeriesOptionEntity
    {
      Id = Guid.NewGuid(),
      CalendarSeriesId = seriesId,
      WorkoutRevisionId = alternative.WorkoutRevisionId,
      DisplayOrder = alternative.DisplayOrder,
    }).ToList();

  private static List<CalendarExceptionEntity> CreateExceptions(Guid seriesId, IReadOnlyList<CalendarExceptionDefinition> exceptions) =>
    exceptions.Select(exception => new CalendarExceptionEntity
    {
      Id = Guid.NewGuid(),
      CalendarSeriesId = seriesId,
      LocalDate = exception.Date,
      Kind = exception.Kind.ToString(),
      Options = exception.Alternatives.Select(alternative => new CalendarExceptionOptionEntity
      {
        Id = Guid.NewGuid(),
        WorkoutRevisionId = alternative.WorkoutRevisionId,
        DisplayOrder = alternative.DisplayOrder,
      }).ToList(),
    }).ToList();

  private static WorkoutAlternative[] EffectiveAlternatives(CalendarSeriesEntity entity, DateOnly date) =>
    TrainingDaySelectionResolver.ResolveDay([Map(entity).Series], entity.UserProfileId, date).Options
      .Select(static option => new WorkoutAlternative(option.WorkoutRevisionId, option.DisplayOrder))
      .ToArray();

  private static bool HasOccurrence(IReadOnlyList<CalendarSeriesEntity> group, Guid profileId, DateOnly date) =>
    TrainingDaySelectionResolver.ResolveDay(group.Select(entity => Map(entity).Series), profileId, date).Options.Count > 0;

  private static bool HasBackwardContinuationCollision(
    IReadOnlyList<CalendarSeriesEntity> group,
    CalendarSeriesEntity selected,
    DateOnly sourceDate,
    DateOnly targetDate,
    int dayOffset,
    Guid continuationSeriesId)
  {
    var preserved = group
      .Where(candidate => candidate.Id != selected.Id && candidate.StartDate < sourceDate)
      .Select(candidate => Map(candidate).Series)
      .ToList();
    if (selected.StartDate < sourceDate)
    {
      CalendarSeriesDefinition definition = Map(selected).Series;
      preserved.Add(new CalendarSeriesDefinition(
        definition.Id,
        definition.UserProfileId,
        definition.Name,
        definition.TimeZoneId,
        new WeeklyRecurrence(
          definition.Recurrence.StartDate,
          sourceDate.AddDays(-1),
          definition.Recurrence.IntervalWeeks,
          definition.Recurrence.Weekdays),
        definition.Alternatives,
        definition.Exceptions.Where(exception => exception.Date < sourceDate).ToArray(),
        definition.ScheduleGroupId));
    }

    var shifted = new List<CalendarSeriesDefinition>
    {
      ShiftedContinuationDefinition(selected, sourceDate, targetDate, dayOffset, continuationSeriesId),
    };
    shifted.AddRange(group
      .Where(candidate => candidate.Id != selected.Id && candidate.StartDate >= sourceDate)
      .Select(candidate => ShiftedDefinition(candidate, dayOffset)));

    HashSet<DateOnly> preservedDates = TrainingDaySelectionResolver
      .ResolveRange(preserved, selected.UserProfileId, targetDate, sourceDate.AddDays(-1))
      .Select(static day => day.Date)
      .ToHashSet();
    return TrainingDaySelectionResolver
      .ResolveRange(shifted, selected.UserProfileId, targetDate, sourceDate.AddDays(-1))
      .Any(day => preservedDates.Contains(day.Date));
  }

  private static CalendarSeriesDefinition ShiftedContinuationDefinition(
    CalendarSeriesEntity selected,
    DateOnly sourceDate,
    DateOnly targetDate,
    int dayOffset,
    Guid continuationSeriesId)
  {
    CalendarSeriesDefinition definition = Map(selected).Series;
    return new CalendarSeriesDefinition(
      selected.StartDate < sourceDate ? continuationSeriesId : definition.Id,
      definition.UserProfileId,
      definition.Name,
      definition.TimeZoneId,
      new WeeklyRecurrence(
        targetDate,
        definition.Recurrence.EndDate?.AddDays(dayOffset),
        definition.Recurrence.IntervalWeeks,
        CalendarScheduleShift.RotateWeekdays(definition.Recurrence.Weekdays, dayOffset)),
      definition.Alternatives,
      definition.Exceptions
        .Where(exception => exception.Date >= sourceDate)
        .Select(exception => new CalendarExceptionDefinition(
          exception.Date.AddDays(dayOffset), exception.Kind, exception.Alternatives))
        .ToArray(),
      definition.ScheduleGroupId);
  }

  private static CalendarSeriesDefinition ShiftedDefinition(CalendarSeriesEntity entity, int dayOffset)
  {
    CalendarSeriesDefinition definition = Map(entity).Series;
    return new CalendarSeriesDefinition(
      definition.Id,
      definition.UserProfileId,
      definition.Name,
      definition.TimeZoneId,
      new WeeklyRecurrence(
        definition.Recurrence.StartDate.AddDays(dayOffset),
        definition.Recurrence.EndDate?.AddDays(dayOffset),
        definition.Recurrence.IntervalWeeks,
        CalendarScheduleShift.RotateWeekdays(definition.Recurrence.Weekdays, dayOffset)),
      definition.Alternatives,
      definition.Exceptions.Select(exception => new CalendarExceptionDefinition(
        exception.Date.AddDays(dayOffset), exception.Kind, exception.Alternatives)).ToArray(),
      definition.ScheduleGroupId);
  }

  private static void MoveFollowingOccurrences(
    TreadmillRunnerDbContext context,
    CalendarSeriesEntity selected,
    IReadOnlyList<CalendarSeriesEntity> group,
    DateOnly sourceDate,
    DateOnly targetDate,
    int dayOffset,
    Guid continuationSeriesId,
    DateTimeOffset nowUtc)
  {
    CalendarExceptionDefinition[] shiftedExceptions = selected.Exceptions
      .Where(exception => exception.LocalDate >= sourceDate)
      .OrderBy(exception => exception.LocalDate)
      .Select(exception => new CalendarExceptionDefinition(
        exception.LocalDate.AddDays(dayOffset),
        ParseExceptionKind(exception.Kind),
        exception.Options.OrderBy(option => option.DisplayOrder)
          .Select(option => new WorkoutAlternative(option.WorkoutRevisionId, option.DisplayOrder)).ToArray()))
      .ToArray();
    DateOnly? shiftedEndDate = selected.EndDate?.AddDays(dayOffset);
    WeekdayFlags shiftedWeekdays = CalendarScheduleShift.RotateWeekdays((WeekdayFlags)selected.WeekdayMask, dayOffset);

    if (selected.StartDate < sourceDate)
    {
      CalendarExceptionEntity[] movedExceptions = selected.Exceptions
        .Where(exception => exception.LocalDate >= sourceDate)
        .ToArray();
      context.CalendarExceptions.RemoveRange(movedExceptions);
      selected.EndDate = sourceDate.AddDays(-1);
      selected.Version++;
      var continuation = new CalendarSeriesDefinition(
        continuationSeriesId,
        selected.UserProfileId,
        selected.Name,
        selected.TimeZoneId,
        new WeeklyRecurrence(targetDate, shiftedEndDate, selected.IntervalWeeks, shiftedWeekdays),
        selected.Options.OrderBy(option => option.DisplayOrder)
          .Select(option => new WorkoutAlternative(option.WorkoutRevisionId, option.DisplayOrder)).ToArray(),
        shiftedExceptions,
        selected.ScheduleGroupId);
      context.CalendarSeries.Add(CreateEntity(continuation, nowUtc, version: 1));
    }
    else
    {
      ShiftSegment(selected, dayOffset);
    }

    foreach (CalendarSeriesEntity later in group.Where(candidate =>
      candidate.Id != selected.Id && candidate.StartDate >= sourceDate))
    {
      ShiftSegment(later, dayOffset);
    }
  }

  private static void ShiftSegment(CalendarSeriesEntity entity, int dayOffset)
  {
    entity.StartDate = entity.StartDate.AddDays(dayOffset);
    entity.EndDate = entity.EndDate?.AddDays(dayOffset);
    entity.WeekdayMask = (int)CalendarScheduleShift.RotateWeekdays((WeekdayFlags)entity.WeekdayMask, dayOffset);
    foreach (CalendarExceptionEntity exception in entity.Exceptions)
    {
      exception.LocalDate = exception.LocalDate.AddDays(dayOffset);
    }
    entity.Version++;
  }

  private static void UpsertException(
    TreadmillRunnerDbContext context,
    CalendarSeriesEntity series,
    DateOnly date,
    CalendarExceptionKind kind,
    IReadOnlyList<WorkoutAlternative> alternatives)
  {
    CalendarExceptionEntity? exception = series.Exceptions.SingleOrDefault(candidate => candidate.LocalDate == date);
    if (exception is null)
    {
      exception = new CalendarExceptionEntity
      {
        Id = Guid.NewGuid(),
        CalendarSeriesId = series.Id,
        LocalDate = date,
        Kind = kind.ToString(),
      };
      series.Exceptions.Add(exception);
      context.Entry(exception).State = EntityState.Added;
    }
    else
    {
      context.CalendarExceptionOptions.RemoveRange(exception.Options);
      exception.Options.Clear();
      exception.Kind = kind.ToString();
    }

    foreach (WorkoutAlternative alternative in alternatives)
    {
      var option = new CalendarExceptionOptionEntity
      {
        Id = Guid.NewGuid(),
        CalendarExceptionId = exception.Id,
        WorkoutRevisionId = alternative.WorkoutRevisionId,
        DisplayOrder = alternative.DisplayOrder,
      };
      exception.Options.Add(option);
      context.Entry(option).State = EntityState.Added;
    }
  }

  private static VersionedCalendarSeries Map(CalendarSeriesEntity entity) => new(
    new CalendarSeriesDefinition(
      entity.Id,
      entity.UserProfileId,
      entity.Name,
      entity.TimeZoneId,
      new WeeklyRecurrence(entity.StartDate, entity.EndDate, entity.IntervalWeeks, (WeekdayFlags)entity.WeekdayMask),
      entity.Options.OrderBy(option => option.DisplayOrder)
        .Select(option => new WorkoutAlternative(option.WorkoutRevisionId, option.DisplayOrder)).ToArray(),
      entity.Exceptions.OrderBy(exception => exception.LocalDate)
        .Select(exception => new CalendarExceptionDefinition(
          exception.LocalDate,
          ParseExceptionKind(exception.Kind),
          exception.Options.OrderBy(option => option.DisplayOrder)
            .Select(option => new WorkoutAlternative(option.WorkoutRevisionId, option.DisplayOrder)).ToArray()))
        .ToArray(),
      entity.ScheduleGroupId),
    entity.Version);

  private static void RequireVersion(int actual, int expected)
  {
    if (actual != expected)
    {
      throw new DbUpdateConcurrencyException($"Expected version {expected}, but stored version is {actual}.");
    }
  }

  private static CalendarExceptionKind ParseExceptionKind(string value)
  {
    if (!Enum.TryParse<CalendarExceptionKind>(value, ignoreCase: false, out var kind) || !Enum.IsDefined(kind))
    {
      throw new InvalidOperationException($"Stored calendar exception kind '{value}' is invalid.");
    }

    return kind;
  }
}
