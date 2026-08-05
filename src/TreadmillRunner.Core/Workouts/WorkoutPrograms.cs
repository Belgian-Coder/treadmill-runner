using TreadmillRunner.Core.Sessions;

namespace TreadmillRunner.Core.Workouts;

public enum WorkoutKind
{
  Structured,
  ManualTemplate,
}

public static class WorkoutProgramLimits
{
  public const int MaximumNameLength = 160;
  public const int MaximumDescriptionLength = 2_000;
  public const int MaximumCategoryLength = 40;
  public const int MaximumItems = 100;
}

public sealed record WorkoutProgramItem
{
  public WorkoutProgramItem(Guid id, Guid workoutRevisionId, int position)
  {
    if (id == Guid.Empty) throw new ArgumentException("Program item ID is required.", nameof(id));
    if (workoutRevisionId == Guid.Empty) throw new ArgumentException("Workout revision ID is required.", nameof(workoutRevisionId));
    if (position < 1) throw new ArgumentOutOfRangeException(nameof(position));
    Id = id;
    WorkoutRevisionId = workoutRevisionId;
    Position = position;
  }

  public Guid Id { get; }
  public Guid WorkoutRevisionId { get; }
  public int Position { get; }
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
    IReadOnlyList<WorkoutProgramItem> items)
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
  }

  public Guid ProgramId { get; }
  public Guid RevisionId { get; }
  public int RevisionNumber { get; }
  public string Name { get; }
  public string? Description { get; }
  public string Category { get; }
  public IReadOnlyList<WorkoutProgramItem> Items { get; }
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
  int Version);

public sealed record WorkoutProgramSessionResult(Guid ProgramItemId, SessionState State, DateTimeOffset EndedAtUtc);

public sealed record WorkoutProgramProgress(
  int CompletedItemCount,
  int TotalItemCount,
  WorkoutProgramItem? NextItem,
  bool IsComplete);

public static class WorkoutProgramProgressCalculator
{
  public static WorkoutProgramProgress Calculate(
    WorkoutProgramRevision revision,
    IReadOnlyCollection<WorkoutProgramSessionResult> sessions)
  {
    ArgumentNullException.ThrowIfNull(revision);
    ArgumentNullException.ThrowIfNull(sessions);
    HashSet<Guid> completed = sessions
      .Where(static session => session.State == SessionState.Completed)
      .Select(static session => session.ProgramItemId)
      .ToHashSet();

    int completedCount = 0;
    foreach (WorkoutProgramItem item in revision.Items)
    {
      if (!completed.Contains(item.Id)) break;
      completedCount++;
    }

    WorkoutProgramItem? next = completedCount < revision.Items.Count ? revision.Items[completedCount] : null;
    return new WorkoutProgramProgress(completedCount, revision.Items.Count, next, next is null);
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
