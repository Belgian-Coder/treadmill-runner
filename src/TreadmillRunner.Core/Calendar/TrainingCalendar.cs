namespace TreadmillRunner.Core.Calendar;

[Flags]
public enum WeekdayFlags
{
  None = 0,
  Monday = 1 << 0,
  Tuesday = 1 << 1,
  Wednesday = 1 << 2,
  Thursday = 1 << 3,
  Friday = 1 << 4,
  Saturday = 1 << 5,
  Sunday = 1 << 6,
}

public sealed class WeeklyRecurrence
{
  private const WeekdayFlags AllWeekdays = WeekdayFlags.Monday | WeekdayFlags.Tuesday | WeekdayFlags.Wednesday |
      WeekdayFlags.Thursday | WeekdayFlags.Friday | WeekdayFlags.Saturday | WeekdayFlags.Sunday;

  public WeeklyRecurrence(DateOnly startDate, DateOnly? endDate, int intervalWeeks, WeekdayFlags weekdays)
  {
    if (endDate < startDate)
    {
      throw new ArgumentOutOfRangeException(nameof(endDate), "End date cannot precede start date.");
    }

    if (intervalWeeks is < 1 or > 52)
    {
      throw new ArgumentOutOfRangeException(nameof(intervalWeeks), "Interval must be between 1 and 52 weeks.");
    }

    if (weekdays == WeekdayFlags.None || (weekdays & ~AllWeekdays) != 0)
    {
      throw new ArgumentOutOfRangeException(nameof(weekdays), "At least one valid weekday is required.");
    }

    StartDate = startDate;
    EndDate = endDate;
    IntervalWeeks = intervalWeeks;
    Weekdays = weekdays;
  }

  public DateOnly StartDate { get; }

  public DateOnly? EndDate { get; }

  public int IntervalWeeks { get; }

  public WeekdayFlags Weekdays { get; }

  public bool OccursOn(DateOnly date)
  {
    if (date < StartDate || (EndDate is { } endDate && date > endDate) || !Weekdays.HasFlag(ToFlag(date.DayOfWeek)))
    {
      return false;
    }

    var anchorWeek = StartDate.AddDays(-DaysSinceMonday(StartDate.DayOfWeek));
    var candidateWeek = date.AddDays(-DaysSinceMonday(date.DayOfWeek));
    var weeks = candidateWeek.DayNumber - anchorWeek.DayNumber;
    return (weeks / 7) % IntervalWeeks == 0;
  }

  private static int DaysSinceMonday(DayOfWeek day) => ((int)day + 6) % 7;

  private static WeekdayFlags ToFlag(DayOfWeek day) => day switch
  {
    DayOfWeek.Monday => WeekdayFlags.Monday,
    DayOfWeek.Tuesday => WeekdayFlags.Tuesday,
    DayOfWeek.Wednesday => WeekdayFlags.Wednesday,
    DayOfWeek.Thursday => WeekdayFlags.Thursday,
    DayOfWeek.Friday => WeekdayFlags.Friday,
    DayOfWeek.Saturday => WeekdayFlags.Saturday,
    DayOfWeek.Sunday => WeekdayFlags.Sunday,
    _ => throw new ArgumentOutOfRangeException(nameof(day)),
  };
}

public sealed record WorkoutAlternative
{
  public WorkoutAlternative(Guid workoutRevisionId, int displayOrder)
  {
    if (workoutRevisionId == Guid.Empty)
    {
      throw new ArgumentException("Workout revision ID cannot be empty.", nameof(workoutRevisionId));
    }

    if (displayOrder < 0)
    {
      throw new ArgumentOutOfRangeException(nameof(displayOrder));
    }

    WorkoutRevisionId = workoutRevisionId;
    DisplayOrder = displayOrder;
  }

  public Guid WorkoutRevisionId { get; }

  public int DisplayOrder { get; }
}

public enum CalendarExceptionKind
{
  Skip,
  Replace,
  Add,
}

public sealed class CalendarExceptionDefinition
{
  public CalendarExceptionDefinition(DateOnly date, CalendarExceptionKind kind, IReadOnlyList<WorkoutAlternative> alternatives)
  {
    ArgumentNullException.ThrowIfNull(alternatives);
    if (kind == CalendarExceptionKind.Skip && alternatives.Count != 0)
    {
      throw new ArgumentException("A skip exception cannot contain alternatives.", nameof(alternatives));
    }

    if (kind != CalendarExceptionKind.Skip && alternatives.Count == 0)
    {
      throw new ArgumentException("Add and replace exceptions require at least one alternative.", nameof(alternatives));
    }

    CalendarValidation.ValidateAlternatives(alternatives, nameof(alternatives));
    Date = date;
    Kind = kind;
    Alternatives = Array.AsReadOnly(alternatives.ToArray());
  }

  public DateOnly Date { get; }

  public CalendarExceptionKind Kind { get; }

  public IReadOnlyList<WorkoutAlternative> Alternatives { get; }
}

public sealed class CalendarSeriesDefinition
{
  public CalendarSeriesDefinition(
      Guid id,
      Guid userProfileId,
      string name,
      string timeZoneId,
      WeeklyRecurrence recurrence,
      IReadOnlyList<WorkoutAlternative> alternatives,
      IReadOnlyList<CalendarExceptionDefinition> exceptions,
      Guid? scheduleGroupId = null)
  {
    if (id == Guid.Empty)
    {
      throw new ArgumentException("Series ID cannot be empty.", nameof(id));
    }

    if (userProfileId == Guid.Empty)
    {
      throw new ArgumentException("Profile ID cannot be empty.", nameof(userProfileId));
    }

    if (scheduleGroupId == Guid.Empty)
    {
      throw new ArgumentException("Schedule group ID cannot be empty.", nameof(scheduleGroupId));
    }

    ArgumentException.ThrowIfNullOrWhiteSpace(name);
    ArgumentException.ThrowIfNullOrWhiteSpace(timeZoneId);
    ArgumentNullException.ThrowIfNull(recurrence);
    ArgumentNullException.ThrowIfNull(alternatives);
    ArgumentNullException.ThrowIfNull(exceptions);
    if (alternatives.Count == 0)
    {
      throw new ArgumentException("A calendar series requires at least one alternative.", nameof(alternatives));
    }

    CalendarValidation.ValidateAlternatives(alternatives, nameof(alternatives));
    if (exceptions.GroupBy(static exception => exception.Date).Any(static group => group.Count() != 1))
    {
      throw new ArgumentException("A series can contain at most one exception per date.", nameof(exceptions));
    }

    Id = id;
    ScheduleGroupId = scheduleGroupId ?? id;
    UserProfileId = userProfileId;
    Name = name.Trim();
    TimeZoneId = timeZoneId.Trim();
    Recurrence = recurrence;
    Alternatives = Array.AsReadOnly(alternatives.ToArray());
    Exceptions = Array.AsReadOnly(exceptions.ToArray());
  }

  public Guid Id { get; }

  public Guid ScheduleGroupId { get; }

  public Guid UserProfileId { get; }

  public string Name { get; }

  public string TimeZoneId { get; }

  public WeeklyRecurrence Recurrence { get; }

  public IReadOnlyList<WorkoutAlternative> Alternatives { get; }

  public IReadOnlyList<CalendarExceptionDefinition> Exceptions { get; }
}

public static class CalendarScheduleShift
{
  private const int WeekdayCount = 7;
  private const int AllWeekdaysMask = (1 << WeekdayCount) - 1;

  public static WeekdayFlags RotateWeekdays(WeekdayFlags weekdays, int dayOffset)
  {
    int normalized = ((dayOffset % WeekdayCount) + WeekdayCount) % WeekdayCount;
    int mask = (int)weekdays;
    if (mask <= 0 || (mask & ~AllWeekdaysMask) != 0)
    {
      throw new ArgumentOutOfRangeException(nameof(weekdays), "At least one valid weekday is required.");
    }

    return (WeekdayFlags)(((mask << normalized) | (mask >> (WeekdayCount - normalized))) & AllWeekdaysMask);
  }
}

public sealed record TrainingDayOption(Guid SeriesId, Guid WorkoutRevisionId, int DisplayOrder);

public sealed record TrainingDaySelection(DateOnly Date, IReadOnlyList<TrainingDayOption> Options);

public static class TrainingDaySelectionResolver
{
  public static TrainingDaySelection ResolveDay(
      IEnumerable<CalendarSeriesDefinition> series,
      Guid userProfileId,
      DateOnly date)
  {
    ArgumentNullException.ThrowIfNull(series);
    if (userProfileId == Guid.Empty)
    {
      throw new ArgumentException("Profile ID cannot be empty.", nameof(userProfileId));
    }

    var options = new List<TrainingDayOption>();
    foreach (var item in series.Where(item => item.UserProfileId == userProfileId))
    {
      var exception = item.Exceptions.SingleOrDefault(candidate => candidate.Date == date);
      var baseOccurs = item.Recurrence.OccursOn(date);
      if (exception?.Kind == CalendarExceptionKind.Skip)
      {
        continue;
      }

      if (baseOccurs && exception?.Kind != CalendarExceptionKind.Replace)
      {
        AddOptions(options, item.Id, item.Alternatives);
      }

      if (exception?.Kind == CalendarExceptionKind.Add ||
          (baseOccurs && exception?.Kind == CalendarExceptionKind.Replace))
      {
        AddOptions(options, item.Id, exception.Alternatives);
      }
    }

    var ordered = options
        .GroupBy(static option => (option.SeriesId, option.WorkoutRevisionId))
        .Select(static group => group.OrderBy(static option => option.DisplayOrder).First())
        .OrderBy(static option => option.DisplayOrder)
        .ThenBy(static option => option.SeriesId)
        .ThenBy(static option => option.WorkoutRevisionId)
        .ToArray();
    return new TrainingDaySelection(date, Array.AsReadOnly(ordered));
  }

  public static IReadOnlyList<TrainingDaySelection> ResolveRange(
      IEnumerable<CalendarSeriesDefinition> series,
      Guid userProfileId,
      DateOnly from,
      DateOnly through)
  {
    ArgumentNullException.ThrowIfNull(series);
    if (through < from)
    {
      throw new ArgumentOutOfRangeException(nameof(through), "Range end cannot precede its start.");
    }

    var materialized = series.ToArray();
    var days = new List<TrainingDaySelection>();
    for (var date = from; ; date = date.AddDays(1))
    {
      var day = ResolveDay(materialized, userProfileId, date);
      if (day.Options.Count != 0)
      {
        days.Add(day);
      }

      if (date == through)
      {
        break;
      }
    }

    return days.AsReadOnly();
  }

  private static void AddOptions(List<TrainingDayOption> target, Guid seriesId, IReadOnlyList<WorkoutAlternative> alternatives)
  {
    target.AddRange(alternatives.Select(alternative => new TrainingDayOption(seriesId, alternative.WorkoutRevisionId, alternative.DisplayOrder)));
  }
}

internal static class CalendarValidation
{
  public static void ValidateAlternatives(IReadOnlyList<WorkoutAlternative> alternatives, string parameterName)
  {
    if (alternatives.Any(static alternative => alternative is null) ||
        alternatives.GroupBy(static alternative => alternative.WorkoutRevisionId).Any(static group => group.Count() != 1) ||
        alternatives.GroupBy(static alternative => alternative.DisplayOrder).Any(static group => group.Count() != 1))
    {
      throw new ArgumentException("Alternatives must have unique workout revisions and display orders.", parameterName);
    }
  }
}
