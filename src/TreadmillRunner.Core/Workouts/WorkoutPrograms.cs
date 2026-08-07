using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Workouts;

public enum WorkoutKind
{
  Structured,
  ManualTemplate,
  PlanInternal,
}

public static class WorkoutProgramLimits
{
  public const int MaximumNameLength = 160;
  public const int MaximumDescriptionLength = 2_000;
  public const int MaximumCategoryLength = 40;
  public const int MaximumItems = 1_000;
}

public sealed record WorkoutProgramAlternative
{
  public WorkoutProgramAlternative(Guid workoutRevisionId, int displayOrder, string variant)
  {
    if (workoutRevisionId == Guid.Empty) throw new ArgumentException("Workout revision ID is required.", nameof(workoutRevisionId));
    if (displayOrder < 1) throw new ArgumentOutOfRangeException(nameof(displayOrder));
    ArgumentException.ThrowIfNullOrWhiteSpace(variant);
    if (variant.Trim().Length > 40) throw new ArgumentException("Variant label is too long.", nameof(variant));
    WorkoutRevisionId = workoutRevisionId;
    DisplayOrder = displayOrder;
    Variant = variant.Trim();
  }

  public Guid WorkoutRevisionId { get; }
  public int DisplayOrder { get; }
  public string Variant { get; }
}

public sealed record WorkoutProgramItem
{
  public WorkoutProgramItem(
    Guid id,
    Guid workoutRevisionId,
    int position,
    int? weekNumber = null,
    int? sessionNumber = null,
    string? phase = null,
    IReadOnlyList<WorkoutProgramAlternative>? alternatives = null)
  {
    if (id == Guid.Empty) throw new ArgumentException("Program item ID is required.", nameof(id));
    if (workoutRevisionId == Guid.Empty) throw new ArgumentException("Workout revision ID is required.", nameof(workoutRevisionId));
    if (position < 1) throw new ArgumentOutOfRangeException(nameof(position));
    if (weekNumber is < 1) throw new ArgumentOutOfRangeException(nameof(weekNumber));
    if (sessionNumber is < 1) throw new ArgumentOutOfRangeException(nameof(sessionNumber));
    if (phase?.Trim().Length > 80) throw new ArgumentException("Program phase is too long.", nameof(phase));
    WorkoutProgramAlternative[] normalizedAlternatives = (alternatives ?? []).OrderBy(static option => option.DisplayOrder).ToArray();
    if (normalizedAlternatives.Any(option => option.WorkoutRevisionId == workoutRevisionId) ||
        normalizedAlternatives.Select(static option => option.WorkoutRevisionId).Distinct().Count() != normalizedAlternatives.Length ||
        normalizedAlternatives.Select(static option => option.DisplayOrder).Distinct().Count() != normalizedAlternatives.Length)
      throw new ArgumentException("Program alternatives must have unique revisions/orders and cannot repeat the primary revision.", nameof(alternatives));
    Id = id;
    WorkoutRevisionId = workoutRevisionId;
    Position = position;
    WeekNumber = weekNumber;
    SessionNumber = sessionNumber;
    Phase = string.IsNullOrWhiteSpace(phase) ? null : phase.Trim();
    Alternatives = normalizedAlternatives;
  }

  public Guid Id { get; }
  public Guid WorkoutRevisionId { get; }
  public int Position { get; }
  public int? WeekNumber { get; }
  public int? SessionNumber { get; }
  public string? Phase { get; }
  public IReadOnlyList<WorkoutProgramAlternative> Alternatives { get; }

  public bool AllowsWorkoutRevision(Guid workoutRevisionId) =>
    WorkoutRevisionId == workoutRevisionId || Alternatives.Any(option => option.WorkoutRevisionId == workoutRevisionId);
}

public sealed class WorkoutProgramRevision
{
  public WorkoutProgramRevision(
    Guid programId,
    Guid revisionId,
    int revisionNumber,
    string name,
    string? description,
    string category,
    IReadOnlyList<WorkoutProgramItem> items,
    string? templateId = null,
    string? templateVersion = null,
    Guid? ownerProfileId = null)
  {
    if (programId == Guid.Empty) throw new ArgumentException("Program ID is required.", nameof(programId));
    if (revisionId == Guid.Empty) throw new ArgumentException("Program revision ID is required.", nameof(revisionId));
    if (revisionNumber < 1) throw new ArgumentOutOfRangeException(nameof(revisionNumber));
    ArgumentException.ThrowIfNullOrWhiteSpace(name);
    ArgumentException.ThrowIfNullOrWhiteSpace(category);
    if (name.Trim().Length > WorkoutProgramLimits.MaximumNameLength) throw new ArgumentException("Program name is too long.", nameof(name));
    if (description?.Trim().Length > WorkoutProgramLimits.MaximumDescriptionLength) throw new ArgumentException("Program description is too long.", nameof(description));
    if (category.Trim().Length > WorkoutProgramLimits.MaximumCategoryLength) throw new ArgumentException("Program category is too long.", nameof(category));
    ArgumentNullException.ThrowIfNull(items);
    if (items.Count is < 1 or > WorkoutProgramLimits.MaximumItems) throw new ArgumentOutOfRangeException(nameof(items));
    if (templateId?.Trim().Length > 100) throw new ArgumentException("Template ID is too long.", nameof(templateId));
    if (templateVersion?.Trim().Length > 40) throw new ArgumentException("Template version is too long.", nameof(templateVersion));
    if (ownerProfileId == Guid.Empty) throw new ArgumentException("Owner profile ID cannot be empty.", nameof(ownerProfileId));
    if (items.Select(static item => item.Id).Distinct().Count() != items.Count) throw new ArgumentException("Program item IDs must be unique.", nameof(items));
    if (!items.Select(static item => item.Position).Order().SequenceEqual(Enumerable.Range(1, items.Count)))
    {
      throw new ArgumentException("Program item positions must be contiguous and start at one.", nameof(items));
    }

    ProgramId = programId;
    RevisionId = revisionId;
    RevisionNumber = revisionNumber;
    Name = name.Trim();
    Description = string.IsNullOrWhiteSpace(description) ? null : description.Trim();
    Category = category.Trim();
    Items = items.OrderBy(static item => item.Position).ToArray();
    TemplateId = string.IsNullOrWhiteSpace(templateId) ? null : templateId.Trim();
    TemplateVersion = string.IsNullOrWhiteSpace(templateVersion) ? null : templateVersion.Trim();
    OwnerProfileId = ownerProfileId;
  }

  public Guid ProgramId { get; }
  public Guid RevisionId { get; }
  public int RevisionNumber { get; }
  public string Name { get; }
  public string? Description { get; }
  public string Category { get; }
  public IReadOnlyList<WorkoutProgramItem> Items { get; }
  public string? TemplateId { get; }
  public string? TemplateVersion { get; }
  public Guid? OwnerProfileId { get; }
}

public enum WorkoutProgramRunStatus
{
  Active,
  Completed,
  Abandoned,
}

public sealed record WorkoutProgramRun(
  Guid Id,
  Guid UserProfileId,
  Guid ProgramRevisionId,
  WorkoutProgramRunStatus Status,
  DateTimeOffset StartedAtUtc,
  DateTimeOffset? EndedAtUtc,
  int Version,
  WorkoutProgramSchedule? Schedule = null);

public sealed record WorkoutProgramSchedule
{
  private const WeekdayFlags AllWeekdays = WeekdayFlags.Monday | WeekdayFlags.Tuesday | WeekdayFlags.Wednesday |
    WeekdayFlags.Thursday | WeekdayFlags.Friday | WeekdayFlags.Saturday | WeekdayFlags.Sunday;

  public WorkoutProgramSchedule(DateOnly startDate, WeekdayFlags weekdays, string timeZoneId)
  {
    if (weekdays == WeekdayFlags.None || (weekdays & ~AllWeekdays) != 0)
      throw new ArgumentOutOfRangeException(nameof(weekdays), "Select at least one valid training day.");
    if (!weekdays.HasFlag(WorkoutProgramScheduleProjector.ToFlag(startDate.DayOfWeek)))
      throw new ArgumentException("The first training date must be one of the selected training days.", nameof(startDate));
    ArgumentException.ThrowIfNullOrWhiteSpace(timeZoneId);
    if (timeZoneId.Trim().Length > 100) throw new ArgumentException("Time zone ID is too long.", nameof(timeZoneId));
    StartDate = startDate;
    Weekdays = weekdays;
    TimeZoneId = timeZoneId.Trim();
  }

  public DateOnly StartDate { get; }
  public WeekdayFlags Weekdays { get; }
  public string TimeZoneId { get; }
}

public sealed record ScheduledWorkoutProgramItem(
  DateOnly Date,
  WorkoutProgramItem Item,
  DateOnly? OriginalDate = null,
  bool IsRepeat = false,
  Guid? ExtraOccurrenceId = null);

public sealed record WorkoutProgramScheduleOverride(Guid ProgramItemId, DateOnly? TargetDate, bool IsSkipped);
public sealed record WorkoutProgramExtraOccurrence(Guid Id, Guid ProgramItemId, DateOnly Date);

public enum WorkoutProgramScheduleAction
{
  MoveOne,
  MoveFollowing,
  Skip,
  Restore,
  Repeat,
  RepeatAndShift,
}

public static class WorkoutProgramScheduleProjector
{
  public static IReadOnlyList<ScheduledWorkoutProgramItem> Project(
    WorkoutProgramRevision revision,
    WorkoutProgramRun run,
    DateOnly from,
    DateOnly to)
  {
    ArgumentNullException.ThrowIfNull(revision);
    ArgumentNullException.ThrowIfNull(run);
    if (to < from) throw new ArgumentOutOfRangeException(nameof(to));
    if (run.Schedule is not { } schedule) return [];

    var result = new List<ScheduledWorkoutProgramItem>();
    DateOnly cursor = schedule.StartDate;
    foreach (WorkoutProgramItem item in revision.Items.OrderBy(static item => item.Position))
    {
      while (!schedule.Weekdays.HasFlag(ToFlag(cursor.DayOfWeek))) cursor = cursor.AddDays(1);
      if (cursor > to) break;
      if (cursor >= from) result.Add(new ScheduledWorkoutProgramItem(cursor, item));
      cursor = cursor.AddDays(1);
    }
    return result;
  }

  public static IReadOnlyList<ScheduledWorkoutProgramItem> ProjectAll(
    WorkoutProgramRevision revision,
    WorkoutProgramRun run,
    IReadOnlyCollection<WorkoutProgramScheduleOverride>? overrides = null,
    IReadOnlyCollection<WorkoutProgramExtraOccurrence>? extras = null)
  {
    ArgumentNullException.ThrowIfNull(revision);
    ArgumentNullException.ThrowIfNull(run);
    if (run.Schedule is not { } schedule) return [];
    Dictionary<Guid, WorkoutProgramScheduleOverride> overrideMap = (overrides ?? [])
      .ToDictionary(static item => item.ProgramItemId);
    var items = new List<ScheduledWorkoutProgramItem>(revision.Items.Count + (extras?.Count ?? 0));
    DateOnly cursor = schedule.StartDate;
    foreach (WorkoutProgramItem item in revision.Items.OrderBy(static item => item.Position))
    {
      while (!schedule.Weekdays.HasFlag(ToFlag(cursor.DayOfWeek))) cursor = cursor.AddDays(1);
      DateOnly original = cursor;
      cursor = cursor.AddDays(1);
      if (overrideMap.TryGetValue(item.Id, out WorkoutProgramScheduleOverride? scheduleOverride))
      {
        if (scheduleOverride.IsSkipped) continue;
        items.Add(new ScheduledWorkoutProgramItem(scheduleOverride.TargetDate ?? original, item, original));
      }
      else
      {
        items.Add(new ScheduledWorkoutProgramItem(original, item, original));
      }
    }

    Dictionary<Guid, WorkoutProgramItem> itemMap = revision.Items.ToDictionary(static item => item.Id);
    foreach (WorkoutProgramExtraOccurrence extra in extras ?? [])
    {
      if (itemMap.TryGetValue(extra.ProgramItemId, out WorkoutProgramItem? item))
        items.Add(new ScheduledWorkoutProgramItem(extra.Date, item, null, IsRepeat: true, extra.Id));
    }

    return items
      .OrderBy(static item => item.Date)
      .ThenBy(static item => item.Item.Position)
      .ThenBy(static item => item.IsRepeat)
      .ToArray();
  }

  public static int CountSelectedDays(WeekdayFlags weekdays) =>
    Enumerable.Range(0, 7).Count(bit => (((int)weekdays >> bit) & 1) == 1);

  public static WeekdayFlags ToFlag(DayOfWeek day) => day switch
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

public sealed record WorkoutProgramSessionResult(Guid ProgramItemId, SessionState State, DateTimeOffset EndedAtUtc);

public sealed record WorkoutProgramProgress(
  int CompletedItemCount,
  int TotalItemCount,
  WorkoutProgramItem? NextItem,
  bool IsComplete,
  int SkippedItemCount = 0);

public static class WorkoutProgramProgressCalculator
{
  public static WorkoutProgramProgress Calculate(
    WorkoutProgramRevision revision,
    IReadOnlyCollection<WorkoutProgramSessionResult> sessions,
    IReadOnlyCollection<Guid>? skippedItemIds = null)
  {
    ArgumentNullException.ThrowIfNull(revision);
    ArgumentNullException.ThrowIfNull(sessions);
    HashSet<Guid> completed = sessions
      .Where(static session => session.State == SessionState.Completed)
      .Select(static session => session.ProgramItemId)
      .ToHashSet();
    HashSet<Guid> skipped = (skippedItemIds ?? []).ToHashSet();

    int completedCount = 0;
    foreach (WorkoutProgramItem item in revision.Items)
    {
      if (!completed.Contains(item.Id) && !skipped.Contains(item.Id)) break;
      completedCount++;
    }

    WorkoutProgramItem? next = completedCount < revision.Items.Count ? revision.Items[completedCount] : null;
    return new WorkoutProgramProgress(
      completedCount - revision.Items.Take(completedCount).Count(item => skipped.Contains(item.Id)),
      revision.Items.Count,
      next,
      next is null,
      revision.Items.Take(completedCount).Count(item => skipped.Contains(item.Id)));
  }
}

public enum WorkoutRecommendationKind
{
  Calendar,
  CalendarChoiceRequired,
  Program,
  Manual,
}

public sealed record WorkoutRecommendation(
  WorkoutRecommendationKind Kind,
  Guid? WorkoutRevisionId,
  Guid? ProgramRunId,
  Guid? ProgramItemId);

public static class WorkoutRecommendationResolver
{
  public static WorkoutRecommendation Resolve(
    IReadOnlyList<Guid> calendarRevisionIds,
    WorkoutProgramRun? activeRun,
    WorkoutProgramProgress? programProgress)
  {
    ArgumentNullException.ThrowIfNull(calendarRevisionIds);
    Guid[] calendarChoices = calendarRevisionIds.Where(static id => id != Guid.Empty).Distinct().ToArray();
    if (calendarChoices.Length == 1)
    {
      return new WorkoutRecommendation(WorkoutRecommendationKind.Calendar, calendarChoices[0], null, null);
    }

    if (calendarChoices.Length > 1)
    {
      return new WorkoutRecommendation(WorkoutRecommendationKind.CalendarChoiceRequired, null, null, null);
    }

    if (activeRun?.Status == WorkoutProgramRunStatus.Active && programProgress?.NextItem is { } next)
    {
      return new WorkoutRecommendation(WorkoutRecommendationKind.Program, next.WorkoutRevisionId, activeRun.Id, next.Id);
    }

    return new WorkoutRecommendation(WorkoutRecommendationKind.Manual, null, null, null);
  }
}
