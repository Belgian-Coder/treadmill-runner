using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Core.Tests;

public sealed class WorkoutProgramTests
{
  private static readonly DateTimeOffset EndedAt = DateTimeOffset.Parse("2026-08-04T08:30:00Z");

  [Fact]
  public void Revision_normalizes_metadata_and_orders_contiguous_items()
  {
    Guid programId = Guid.NewGuid();
    Guid revisionId = Guid.NewGuid();
    var second = new WorkoutProgramItem(Guid.NewGuid(), Guid.NewGuid(), 2);
    var first = new WorkoutProgramItem(Guid.NewGuid(), Guid.NewGuid(), 1);

    var revision = new WorkoutProgramRevision(
      programId,
      revisionId,
      1,
      "  First 5K  ",
      "  Build safely.  ",
      "  5K  ",
      [second, first]);

    Assert.Equal("First 5K", revision.Name);
    Assert.Equal("Build safely.", revision.Description);
    Assert.Equal("5K", revision.Category);
    Assert.Equal([first.Id, second.Id], revision.Items.Select(item => item.Id));
  }

  [Fact]
  public void Revision_rejects_duplicate_items_and_non_contiguous_positions()
  {
    Guid duplicateId = Guid.NewGuid();

    Assert.Throws<ArgumentException>(() => Revision(
      new WorkoutProgramItem(duplicateId, Guid.NewGuid(), 1),
      new WorkoutProgramItem(duplicateId, Guid.NewGuid(), 2)));
    Assert.Throws<ArgumentException>(() => Revision(
      new WorkoutProgramItem(Guid.NewGuid(), Guid.NewGuid(), 1),
      new WorkoutProgramItem(Guid.NewGuid(), Guid.NewGuid(), 3)));
  }

  [Fact]
  public void Program_item_accepts_distinct_alternatives_for_the_same_progression_slot()
  {
    Guid primary = Guid.NewGuid();
    Guid heartRate = Guid.NewGuid();
    var item = new WorkoutProgramItem(
      Guid.NewGuid(), primary, 1, 11, 1, "Foundation",
      [new WorkoutProgramAlternative(heartRate, 1, "hr-alternative")]);

    Assert.True(item.AllowsWorkoutRevision(primary));
    Assert.True(item.AllowsWorkoutRevision(heartRate));
    Assert.False(item.AllowsWorkoutRevision(Guid.NewGuid()));
    Assert.Throws<ArgumentException>(() => new WorkoutProgramItem(
      Guid.NewGuid(), primary, 1, alternatives: [new WorkoutProgramAlternative(primary, 1, "duplicate")]));
  }

  [Fact]
  public void Program_item_rejects_an_unbounded_alternative_collection()
  {
    WorkoutProgramAlternative[] alternatives = Enumerable.Range(0, WorkoutProgramLimits.MaximumAlternativesPerItem + 1)
      .Select(index => new WorkoutProgramAlternative(Guid.NewGuid(), index + 1, $"variant-{index}"))
      .ToArray();

    Assert.Throws<ArgumentOutOfRangeException>(() => new WorkoutProgramItem(
      Guid.NewGuid(), Guid.NewGuid(), 1, alternatives: alternatives));
  }

  [Fact]
  public void Progress_counts_only_consecutive_completed_items()
  {
    WorkoutProgramRevision revision = Revision(
      Item(1),
      Item(2),
      Item(3));

    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(
      revision,
      [
        Result(revision.Items[0], SessionState.Completed),
        Result(revision.Items[2], SessionState.Completed),
      ]);

    Assert.Equal(1, progress.CompletedItemCount);
    Assert.Equal(revision.Items[1].Id, progress.NextItem?.Id);
    Assert.False(progress.IsComplete);
  }

  [Theory]
  [InlineData(SessionState.Stopped)]
  [InlineData(SessionState.Interrupted)]
  [InlineData(SessionState.Faulted)]
  public void Non_completed_terminal_sessions_do_not_advance(SessionState state)
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2));

    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(
      revision,
      [Result(revision.Items[0], state)]);

    Assert.Equal(0, progress.CompletedItemCount);
    Assert.Equal(revision.Items[0].Id, progress.NextItem?.Id);
    Assert.False(progress.IsComplete);
  }

  [Fact]
  public void All_completed_items_finish_the_program()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2));

    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(
      revision,
      revision.Items.Select(item => Result(item, SessionState.Completed)).ToArray());

    Assert.Equal(2, progress.CompletedItemCount);
    Assert.Null(progress.NextItem);
    Assert.True(progress.IsComplete);
  }

  [Fact]
  public void Skipped_items_advance_order_without_becoming_completed()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2), Item(3));

    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(
      revision,
      [Result(revision.Items[0], SessionState.Completed)],
      [revision.Items[1].Id]);

    Assert.Equal(1, progress.CompletedItemCount);
    Assert.Equal(1, progress.SkippedItemCount);
    Assert.Equal(revision.Items[2].Id, progress.NextItem?.Id);
  }

  [Fact]
  public void Single_calendar_workout_takes_priority_over_active_program()
  {
    Guid calendarRevisionId = Guid.NewGuid();
    WorkoutProgramRevision revision = Revision(Item(1));
    WorkoutProgramRun run = ActiveRun(revision);
    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(revision, []);

    WorkoutRecommendation recommendation = WorkoutRecommendationResolver.Resolve(
      [calendarRevisionId],
      run,
      progress);

    Assert.Equal(WorkoutRecommendationKind.Calendar, recommendation.Kind);
    Assert.Equal(calendarRevisionId, recommendation.WorkoutRevisionId);
    Assert.Null(recommendation.ProgramRunId);
    Assert.Null(recommendation.ProgramItemId);
  }

  [Fact]
  public void Multiple_calendar_workouts_require_an_explicit_choice()
  {
    WorkoutRecommendation recommendation = WorkoutRecommendationResolver.Resolve(
      [Guid.NewGuid(), Guid.NewGuid()],
      null,
      null);

    Assert.Equal(WorkoutRecommendationKind.CalendarChoiceRequired, recommendation.Kind);
    Assert.Null(recommendation.WorkoutRevisionId);
  }

  [Fact]
  public void Active_program_recommends_its_exact_next_pinned_revision()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2));
    WorkoutProgramRun run = ActiveRun(revision);
    WorkoutProgramProgress progress = WorkoutProgramProgressCalculator.Calculate(
      revision,
      [Result(revision.Items[0], SessionState.Completed)]);

    WorkoutRecommendation recommendation = WorkoutRecommendationResolver.Resolve([], run, progress);

    Assert.Equal(WorkoutRecommendationKind.Program, recommendation.Kind);
    Assert.Equal(revision.Items[1].WorkoutRevisionId, recommendation.WorkoutRevisionId);
    Assert.Equal(run.Id, recommendation.ProgramRunId);
    Assert.Equal(revision.Items[1].Id, recommendation.ProgramItemId);
  }

  [Fact]
  public void No_calendar_or_active_program_falls_back_to_manual()
  {
    WorkoutRecommendation recommendation = WorkoutRecommendationResolver.Resolve([], null, null);

    Assert.Equal(WorkoutRecommendationKind.Manual, recommendation.Kind);
    Assert.Null(recommendation.WorkoutRevisionId);
  }

  [Fact]
  public void Schedule_projects_items_in_exact_order_on_selected_days()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2), Item(3), Item(4));
    var run = new WorkoutProgramRun(
      Guid.NewGuid(), Guid.NewGuid(), revision.RevisionId, WorkoutProgramRunStatus.Active,
      EndedAt, null, 1,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"));

    IReadOnlyList<ScheduledWorkoutProgramItem> scheduled = WorkoutProgramScheduleProjector.Project(
      revision, run, new DateOnly(2026, 8, 10), new DateOnly(2026, 8, 17));

    Assert.Equal(
      [new DateOnly(2026, 8, 10), new DateOnly(2026, 8, 12), new DateOnly(2026, 8, 15), new DateOnly(2026, 8, 17)],
      scheduled.Select(static item => item.Date));
    Assert.Equal(revision.Items.Select(static item => item.Id), scheduled.Select(static item => item.Item.Id));
  }

  [Fact]
  public void Schedule_range_does_not_rebase_item_positions()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2), Item(3), Item(4));
    var run = new WorkoutProgramRun(
      Guid.NewGuid(), Guid.NewGuid(), revision.RevisionId, WorkoutProgramRunStatus.Active,
      EndedAt, null, 1,
      new WorkoutProgramSchedule(new DateOnly(2026, 8, 10), WeekdayFlags.Monday | WeekdayFlags.Wednesday, "Europe/Brussels"));

    IReadOnlyList<ScheduledWorkoutProgramItem> scheduled = WorkoutProgramScheduleProjector.Project(
      revision, run, new DateOnly(2026, 8, 17), new DateOnly(2026, 8, 17));

    ScheduledWorkoutProgramItem item = Assert.Single(scheduled);
    Assert.Equal(3, item.Item.Position);
  }

  [Fact]
  public void Full_schedule_applies_sparse_moves_skips_and_extra_attempts_without_reordering_items()
  {
    WorkoutProgramRevision revision = Revision(Item(1), Item(2), Item(3));
    var run = new WorkoutProgramRun(
      Guid.NewGuid(), Guid.NewGuid(), revision.RevisionId, WorkoutProgramRunStatus.Active,
      EndedAt, null, 3,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"));
    Guid extraId = Guid.NewGuid();

    IReadOnlyList<ScheduledWorkoutProgramItem> scheduled = WorkoutProgramScheduleProjector.ProjectAll(
      revision,
      run,
      [
        new WorkoutProgramScheduleOverride(revision.Items[0].Id, new DateOnly(2026, 8, 11), false),
        new WorkoutProgramScheduleOverride(revision.Items[1].Id, null, true),
      ],
      [new WorkoutProgramExtraOccurrence(extraId, revision.Items[0].Id, new DateOnly(2026, 8, 13))]);

    Assert.Equal([new DateOnly(2026, 8, 11), new DateOnly(2026, 8, 13), new DateOnly(2026, 8, 15)],
      scheduled.Select(static item => item.Date));
    Assert.DoesNotContain(scheduled, item => item.Item.Id == revision.Items[1].Id);
    ScheduledWorkoutProgramItem extra = Assert.Single(scheduled, static item => item.IsRepeat);
    Assert.Equal(extraId, extra.ExtraOccurrenceId);
  }

  [Fact]
  public void Schedule_rejects_a_first_date_that_is_not_a_training_day()
  {
    ArgumentException error = Assert.Throws<ArgumentException>(() => new WorkoutProgramSchedule(
      new DateOnly(2026, 8, 6),
      WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
      "Europe/Brussels"));

    Assert.Contains("first training date", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Schedule_rejects_an_unavailable_time_zone_before_a_run_can_be_stored()
  {
    ArgumentException error = Assert.Throws<ArgumentException>(() => new WorkoutProgramSchedule(
      new DateOnly(2026, 8, 10),
      WeekdayFlags.Monday,
      "Definitely/Not-A-TimeZone"));

    Assert.Contains("time zone", error.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public void Full_schedule_projects_a_single_item_on_the_maximum_supported_date()
  {
    WorkoutProgramRevision revision = Revision(Item(1));
    var run = new WorkoutProgramRun(
      Guid.NewGuid(), Guid.NewGuid(), revision.RevisionId, WorkoutProgramRunStatus.Active,
      EndedAt, null, 1,
      new WorkoutProgramSchedule(DateOnly.MaxValue, WeekdayFlags.Friday, "UTC"));

    IReadOnlyList<ScheduledWorkoutProgramItem> scheduled = WorkoutProgramScheduleProjector.ProjectAll(revision, run);

    ScheduledWorkoutProgramItem item = Assert.Single(scheduled);
    Assert.Equal(DateOnly.MaxValue, item.Date);
    Assert.Equal(revision.Items[0].Id, item.Item.Id);
  }

  private static WorkoutProgramRevision Revision(params WorkoutProgramItem[] items) => new(
    Guid.NewGuid(),
    Guid.NewGuid(),
    1,
    "First 5K",
    "Build safely.",
    "5K",
    items);

  private static WorkoutProgramItem Item(int position) => new(Guid.NewGuid(), Guid.NewGuid(), position);

  private static WorkoutProgramSessionResult Result(WorkoutProgramItem item, SessionState state) =>
    new(item.Id, state, EndedAt);

  private static WorkoutProgramRun ActiveRun(WorkoutProgramRevision revision) => new(
    Guid.NewGuid(),
    Guid.NewGuid(),
    revision.RevisionId,
    WorkoutProgramRunStatus.Active,
    EndedAt.AddHours(-1),
    null,
    1);
}
