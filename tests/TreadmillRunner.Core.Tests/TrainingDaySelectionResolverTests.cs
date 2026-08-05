using TreadmillRunner.Core.Calendar;

namespace TreadmillRunner.Core.Tests;

public sealed class TrainingDaySelectionResolverTests
{
  [Theory]
  [InlineData(1, 1, 2)]
  [InlineData(64, 1, 1)]
  [InlineData(1, -1, 64)]
  [InlineData(5, 2, 20)]
  [InlineData(127, 12, 127)]
  public void Weekday_rotation_preserves_the_schedule_pattern(int source, int offset, int expected)
  {
    Assert.Equal((WeekdayFlags)expected, CalendarScheduleShift.RotateWeekdays((WeekdayFlags)source, offset));
  }

  [Fact]
  public void Calendar_series_defaults_its_logical_group_to_its_own_id()
  {
    Guid id = Guid.NewGuid();
    Guid profileId = Guid.NewGuid();
    var series = new CalendarSeriesDefinition(
      id,
      profileId,
      "Default group",
      "Europe/Brussels",
      new WeeklyRecurrence(new DateOnly(2026, 8, 3), null, 1, WeekdayFlags.Monday),
      [new WorkoutAlternative(Guid.NewGuid(), 0)],
      []);

    Assert.Equal(id, series.ScheduleGroupId);
  }

  [Fact]
  public void Weekly_recurrence_stays_on_local_calendar_dates_across_brussels_dst_start()
  {
    var profileId = Guid.NewGuid();
    var series = CreateSeries(
        profileId,
        new WeeklyRecurrence(new DateOnly(2026, 3, 22), null, 1, WeekdayFlags.Sunday),
        [Alternative(1)]);

    var days = TrainingDaySelectionResolver.ResolveRange(
        [series],
        profileId,
        new DateOnly(2026, 3, 22),
        new DateOnly(2026, 4, 5));

    Assert.Equal(
        [new DateOnly(2026, 3, 22), new DateOnly(2026, 3, 29), new DateOnly(2026, 4, 5)],
        days.Select(static day => day.Date));
    Assert.All(days, static day => Assert.Single(day.Options));
  }

  [Fact]
  public void Weekly_recurrence_stays_on_local_calendar_dates_across_brussels_dst_end()
  {
    var profileId = Guid.NewGuid();
    var series = CreateSeries(
        profileId,
        new WeeklyRecurrence(new DateOnly(2026, 10, 18), null, 1, WeekdayFlags.Sunday),
        [Alternative(1)]);

    var days = TrainingDaySelectionResolver.ResolveRange(
        [series],
        profileId,
        new DateOnly(2026, 10, 18),
        new DateOnly(2026, 11, 1));

    Assert.Equal(
        [new DateOnly(2026, 10, 18), new DateOnly(2026, 10, 25), new DateOnly(2026, 11, 1)],
        days.Select(static day => day.Date));
  }

  [Fact]
  public void Interval_weeks_are_anchored_to_start_week()
  {
    var recurrence = new WeeklyRecurrence(new DateOnly(2026, 1, 5), null, 2, WeekdayFlags.Monday | WeekdayFlags.Wednesday);

    Assert.True(recurrence.OccursOn(new DateOnly(2026, 1, 5)));
    Assert.True(recurrence.OccursOn(new DateOnly(2026, 1, 7)));
    Assert.False(recurrence.OccursOn(new DateOnly(2026, 1, 12)));
    Assert.True(recurrence.OccursOn(new DateOnly(2026, 1, 19)));
  }

  [Fact]
  public void Skip_replace_and_add_exceptions_change_only_the_selected_day()
  {
    var profileId = Guid.NewGuid();
    var baseOption = Alternative(1);
    var replacement = Alternative(2);
    var addition = Alternative(3);
    var series = new CalendarSeriesDefinition(
        Guid.NewGuid(),
        profileId,
        "Sundays",
        "Europe/Brussels",
        new WeeklyRecurrence(new DateOnly(2026, 3, 1), null, 1, WeekdayFlags.Sunday),
        [baseOption],
        [
          new CalendarExceptionDefinition(new DateOnly(2026, 3, 8), CalendarExceptionKind.Skip, []),
          new CalendarExceptionDefinition(new DateOnly(2026, 3, 15), CalendarExceptionKind.Replace, [replacement]),
          new CalendarExceptionDefinition(new DateOnly(2026, 3, 22), CalendarExceptionKind.Add, [addition])
        ]);

    Assert.Empty(TrainingDaySelectionResolver.ResolveDay([series], profileId, new DateOnly(2026, 3, 8)).Options);
    Assert.Equal(replacement.WorkoutRevisionId, Assert.Single(TrainingDaySelectionResolver.ResolveDay([series], profileId, new DateOnly(2026, 3, 15)).Options).WorkoutRevisionId);
    Assert.Equal(2, TrainingDaySelectionResolver.ResolveDay([series], profileId, new DateOnly(2026, 3, 22)).Options.Count);
    Assert.Equal(baseOption.WorkoutRevisionId, Assert.Single(TrainingDaySelectionResolver.ResolveDay([series], profileId, new DateOnly(2026, 3, 29)).Options).WorkoutRevisionId);
  }

  [Fact]
  public void Resolver_filters_profiles_and_orders_alternatives()
  {
    var selectedProfile = Guid.NewGuid();
    var series = CreateSeries(
        selectedProfile,
        new WeeklyRecurrence(new DateOnly(2026, 1, 1), null, 1, WeekdayFlags.Thursday),
        [Alternative(20), Alternative(10)]);
    var other = CreateSeries(
        Guid.NewGuid(),
        new WeeklyRecurrence(new DateOnly(2026, 1, 1), null, 1, WeekdayFlags.Thursday),
        [Alternative(1)]);

    var day = TrainingDaySelectionResolver.ResolveDay([series, other], selectedProfile, new DateOnly(2026, 1, 1));

    Assert.Equal([10, 20], day.Options.Select(static option => option.DisplayOrder));
  }

  [Fact]
  public void Replace_does_not_create_an_off_day_but_add_does()
  {
    var profileId = Guid.NewGuid();
    var offDay = new DateOnly(2026, 3, 2);
    var replacement = Alternative(1);
    var addition = Alternative(2);
    var recurrence = new WeeklyRecurrence(
        new DateOnly(2026, 3, 1),
        null,
        1,
        WeekdayFlags.Sunday);
    var replaceSeries = new CalendarSeriesDefinition(
        Guid.NewGuid(),
        profileId,
        "Replace",
        "Europe/Brussels",
        recurrence,
        [Alternative(0)],
        [new CalendarExceptionDefinition(offDay, CalendarExceptionKind.Replace, [replacement])]);
    var addSeries = new CalendarSeriesDefinition(
        Guid.NewGuid(),
        profileId,
        "Add",
        "Europe/Brussels",
        recurrence,
        [Alternative(0)],
        [new CalendarExceptionDefinition(offDay, CalendarExceptionKind.Add, [addition])]);

    Assert.Empty(TrainingDaySelectionResolver.ResolveDay([replaceSeries], profileId, offDay).Options);
    Assert.Equal(
        addition.WorkoutRevisionId,
        Assert.Single(TrainingDaySelectionResolver.ResolveDay([addSeries], profileId, offDay).Options).WorkoutRevisionId);
  }

  private static CalendarSeriesDefinition CreateSeries(
      Guid profileId,
      WeeklyRecurrence recurrence,
      IReadOnlyList<WorkoutAlternative> alternatives) => new(
          Guid.NewGuid(),
          profileId,
          "Plan",
          "Europe/Brussels",
          recurrence,
          alternatives,
          []);

  private static WorkoutAlternative Alternative(int order) => new(Guid.NewGuid(), order);
}
