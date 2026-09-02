using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Diagnostics;
using System.Data.Common;
using System.Text.Json;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class WorkoutProgramStoreTests : IAsyncLifetime
{
  private static readonly DateTimeOffset Now = DateTimeOffset.Parse("2026-08-04T08:00:00Z");
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));
  private string _databasePath = null!;
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _databasePath = Path.Combine(_directory, "programs.db");
    _factory = TreadmillRunnerDatabase.CreateFactory(_databasePath);
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.EnsureCreatedAsync();
  }

  [Fact]
  public async Task Program_summary_query_count_is_constant_as_the_library_grows()
  {
    UserProfile runner = await CreateProfileAsync("Summary query runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Summary workout", 6.2);
    var store = new WorkoutProgramStore(_factory);
    for (int index = 0; index < 8; index++)
    {
      WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id);
      await store.CreateAsync(revision, Now.AddSeconds(index), Op("program.create"));
      if (index == 0)
      {
        await store.StartAsync(
          Guid.NewGuid(), runner.Id, revision.RevisionId, null, null, null,
          Now.AddMinutes(1), Op("program.start"));
      }
    }

    var counter = new CommandCounterInterceptor();
    var countingFactory = new CountingDbContextFactory(_databasePath, counter);

    IReadOnlyList<StoredWorkoutProgramSummary> summaries = await new WorkoutProgramStore(countingFactory)
      .ListSummariesAsync(runner.Id);

    Assert.Equal(8, summaries.Count);
    Assert.Equal(6, counter.ReaderCount);
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory)) Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Bulk_revision_lookup_deduplicates_ids_and_ignores_missing_revisions()
  {
    StoredWorkoutRevision first = await CreateWorkoutAsync("Bulk first", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Bulk second", 7);
    var store = new WorkoutStore(_factory);

    IReadOnlyList<StoredWorkoutRevision> revisions = await store.FindRevisionsAsync(
      [first.Id, first.Id, second.Id, Guid.NewGuid()]);

    Assert.Equal(2, revisions.Count);
    Assert.Equal(
      new[] { first.Id, second.Id }.OrderBy(static id => id),
      revisions.Select(static revision => revision.Id).OrderBy(static id => id));
    Assert.Empty(await store.FindRevisionsAsync([]));
  }

  [Fact]
  public async Task Schedule_local_date_uses_the_run_timezone_at_a_utc_day_boundary()
  {
    UserProfile runner = await CreateProfileAsync("Time-zone boundary runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Boundary workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(),
      runner.Id,
      revision.RevisionId,
      null,
      null,
      new WorkoutProgramSchedule(new DateOnly(2026, 8, 10), WeekdayFlags.Monday, "Europe/Brussels"),
      Now,
      Op("program.start"));

    DateOnly localDate = await store.GetScheduleLocalDateAsync(
      runner.Id,
      run.Id,
      DateTimeOffset.Parse("2026-08-09T22:30:00Z"));

    Assert.Equal(new DateOnly(2026, 8, 10), localDate);
  }

  [Fact]
  public async Task Completed_sessions_advance_only_the_linked_runner_while_other_terminal_and_manual_sessions_do_not()
  {
    UserProfile firstRunner = await CreateProfileAsync("First runner");
    UserProfile secondRunner = await CreateProfileAsync("Second runner");
    StoredWorkoutRevision firstWorkout = await CreateWorkoutAsync("Easy foundation", 6.5);
    StoredWorkoutRevision secondWorkout = await CreateWorkoutAsync("Steady finish", 7.0);
    WorkoutProgramRevision program = ProgramRevision(
      Guid.NewGuid(),
      Guid.NewGuid(),
      1,
      firstWorkout.Id,
      secondWorkout.Id);
    var programStore = new WorkoutProgramStore(_factory);
    await programStore.CreateAsync(program, Now, Op("program.create"));
    WorkoutProgramRun firstRun = await programStore.StartAsync(
      Guid.NewGuid(), firstRunner.Id, program.RevisionId, null, null, null, Now.AddMinutes(1), Op("program.start"));
    WorkoutProgramRun secondRun = await programStore.StartAsync(
      Guid.NewGuid(), secondRunner.Id, program.RevisionId, null, null, null, Now.AddMinutes(1), Op("program.start"));

    foreach (SessionState state in new[] { SessionState.Stopped, SessionState.Interrupted, SessionState.Faulted })
    {
      await SaveTerminalSessionAsync(
        firstRunner,
        firstWorkout,
        state,
        new WorkoutSessionSelection(WorkoutSelectionSource.Program, firstRun.Id, program.Items[0].Id));
    }
    await SaveTerminalSessionAsync(
      secondRunner,
      firstWorkout,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Manual));

    StoredWorkoutProgramProgress firstBeforeCompletion = Assert.Single(await programStore.ListAsync(firstRunner.Id));
    StoredWorkoutProgramProgress secondBeforeCompletion = Assert.Single(await programStore.ListAsync(secondRunner.Id));
    Assert.Equal(0, firstBeforeCompletion.Progress?.CompletedItemCount);
    Assert.Equal(program.Items[0].Id, firstBeforeCompletion.Progress?.NextItem?.Id);
    Assert.Equal(0, secondBeforeCompletion.Progress?.CompletedItemCount);
    Assert.Equal(program.Items[0].Id, secondBeforeCompletion.Progress?.NextItem?.Id);

    await SaveTerminalSessionAsync(
      firstRunner,
      firstWorkout,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, firstRun.Id, program.Items[0].Id));

    StoredWorkoutProgramProgress firstAfterCompletion = Assert.Single(await programStore.ListAsync(firstRunner.Id));
    StoredWorkoutProgramProgress secondAfterCompletion = Assert.Single(await programStore.ListAsync(secondRunner.Id));
    Assert.Equal(1, firstAfterCompletion.Progress?.CompletedItemCount);
    Assert.Equal(program.Items[1].Id, firstAfterCompletion.Progress?.NextItem?.Id);
    Assert.Equal(0, secondAfterCompletion.Progress?.CompletedItemCount);
    Assert.Equal(program.Items[0].Id, secondAfterCompletion.Progress?.NextItem?.Id);
    Assert.NotNull(await programStore.ValidateSelectionAsync(
      firstRunner.Id,
      firstRun.Id,
      program.Items[1].Id,
      secondWorkout.Id));

    await SaveTerminalSessionAsync(
      firstRunner,
      secondWorkout,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, firstRun.Id, program.Items[1].Id));

    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    WorkoutProgramRunEntity persistedRun = await context.WorkoutProgramRuns.SingleAsync(run => run.Id == firstRun.Id);
    Assert.Equal(nameof(WorkoutProgramRunStatus.Completed), persistedRun.Status);
    Assert.NotNull(persistedRun.EndedAtUtc);
    Assert.Null(await programStore.ValidateSelectionAsync(
      firstRunner.Id,
      firstRun.Id,
      program.Items[1].Id,
      secondWorkout.Id));
  }

  [Fact]
  public async Task Active_run_remains_pinned_to_its_revision_and_restart_uses_the_latest_revision()
  {
    UserProfile runner = await CreateProfileAsync("Revision runner");
    var workoutStore = new WorkoutStore(_factory);
    Guid workoutId = Guid.NewGuid();
    StoredWorkoutRevision workoutRevisionOne = await workoutStore.CreateAsync(
      workoutId,
      Definition("Foundation v1", 6.0),
      Now,
      Op("workout.create"));
    StoredWorkoutRevision workoutRevisionTwo = await workoutStore.AppendRevisionAsync(
      workoutId,
      Definition("Foundation v2", 6.5),
      Now.AddMinutes(1),
      Op("workout.revision.append"));
    Guid programId = Guid.NewGuid();
    WorkoutProgramRevision programRevisionOne = ProgramRevision(
      programId,
      Guid.NewGuid(),
      1,
      workoutRevisionOne.Id);
    var programStore = new WorkoutProgramStore(_factory);
    await programStore.CreateAsync(programRevisionOne, Now.AddMinutes(2), Op("program.create"));
    WorkoutProgramRun pinnedRun = await programStore.StartAsync(
      Guid.NewGuid(),
      runner.Id,
      programRevisionOne.RevisionId,
      null,
      null,
      null,
      Now.AddMinutes(3),
      Op("program.start"));
    WorkoutProgramRevision programRevisionTwo = ProgramRevision(
      programId,
      Guid.NewGuid(),
      2,
      workoutRevisionTwo.Id);
    await programStore.AppendRevisionAsync(programRevisionTwo, Op("program.revision.append"));

    Assert.NotNull(await programStore.ValidateSelectionAsync(
      runner.Id,
      pinnedRun.Id,
      programRevisionOne.Items[0].Id,
      workoutRevisionOne.Id));
    Assert.Null(await programStore.ValidateSelectionAsync(
      runner.Id,
      pinnedRun.Id,
      programRevisionTwo.Items[0].Id,
      workoutRevisionTwo.Id));

    WorkoutProgramRun restarted = await programStore.RestartAsync(
      Guid.NewGuid(),
      runner.Id,
      programId,
      Now.AddMinutes(4),
      Op("program.restart"));

    Assert.Equal(programRevisionTwo.RevisionId, restarted.ProgramRevisionId);
    Assert.Null(await programStore.ValidateSelectionAsync(
      runner.Id,
      pinnedRun.Id,
      programRevisionOne.Items[0].Id,
      workoutRevisionOne.Id));
    Assert.NotNull(await programStore.ValidateSelectionAsync(
      runner.Id,
      restarted.Id,
      programRevisionTwo.Items[0].Id,
      workoutRevisionTwo.Id));
    Assert.Null(await programStore.ValidateSelectionAsync(
      runner.Id,
      restarted.Id,
      programRevisionTwo.Items[0].Id,
      workoutRevisionOne.Id));

    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    WorkoutProgramRunEntity abandoned = await context.WorkoutProgramRuns.SingleAsync(run => run.Id == pinnedRun.Id);
    Assert.Equal(nameof(WorkoutProgramRunStatus.Abandoned), abandoned.Status);
    Assert.Equal(Now.AddMinutes(4), abandoned.EndedAtUtc);
  }

  [Fact]
  public async Task Alternative_revision_is_persisted_and_is_a_valid_choice_for_the_same_program_slot()
  {
    UserProfile runner = await CreateProfileAsync("Choice runner");
    StoredWorkoutRevision primary = await CreateWorkoutAsync("Fixed pace", 7);
    StoredWorkoutRevision alternative = await CreateWorkoutAsync("Heart-rate guided", 6);
    Guid itemId = Guid.NewGuid();
    var revision = new WorkoutProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, "Choice plan", null, "10K",
      [new WorkoutProgramItem(itemId, primary.Id, 1, alternatives:
        [new WorkoutProgramAlternative(alternative.Id, 2, "hr-alternative")])]);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null, null, Now, Op("program.start"));

    StoredWorkoutProgramProgress stored = Assert.Single(await store.ListAsync(runner.Id));
    WorkoutProgramAlternative storedAlternative = Assert.Single(stored.Program.CurrentRevision.Items[0].Alternatives);
    Assert.Equal(alternative.Id, storedAlternative.WorkoutRevisionId);
    Assert.NotNull(await store.ValidateSelectionAsync(runner.Id, run.Id, itemId, primary.Id));
    Assert.NotNull(await store.ValidateSelectionAsync(runner.Id, run.Id, itemId, alternative.Id));
  }

  [Fact]
  public async Task Clear_upcoming_abandons_only_the_selected_runners_active_schedule_and_preserves_profile()
  {
    UserProfile runner = await CreateProfileAsync("Clear runner");
    UserProfile other = await CreateProfileAsync("Keep runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Planned run", 7);
    WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    var schedule = new WorkoutProgramSchedule(
      new DateOnly(2026, 8, 10), WeekdayFlags.Monday | WeekdayFlags.Wednesday, "Europe/Brussels");
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null, schedule, Now, Op("program.start"));
    WorkoutProgramRun otherRun = await store.StartAsync(
      Guid.NewGuid(), other.Id, revision.RevisionId, null, null, schedule, Now, Op("program.start"));

    WorkoutProgramClearUpcomingPreview preview = await store.PreviewClearUpcomingAsync(
      runner.Id, run.Id, new DateOnly(2026, 8, 7));
    Assert.True(preview.CanApply);
    Assert.Equal(2, preview.UpcomingSessionCount);
    await Assert.ThrowsAsync<KeyNotFoundException>(() => store.PreviewClearUpcomingAsync(
      other.Id, run.Id, new DateOnly(2026, 8, 7)));

    WorkoutProgramClearUpcomingPreview cleared = await store.ClearUpcomingAsync(
      runner.Id, run.Id, new DateOnly(2026, 8, 7), preview.RunVersion, Op("program.run.clear-upcoming"));
    Assert.False(cleared.CanApply);
    Assert.Equal(2, cleared.UpcomingSessionCount);
    Assert.Null(Assert.Single(await store.ListAsync(runner.Id)).Run);
    Assert.Equal(otherRun.Id, Assert.Single(await store.ListAsync(other.Id)).Run?.Id);
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    Assert.True(await context.UserProfiles.AnyAsync(profile => profile.Id == runner.Id && !profile.IsArchived));
    Assert.Equal(nameof(WorkoutProgramRunStatus.Abandoned),
      (await context.WorkoutProgramRuns.SingleAsync(candidate => candidate.Id == run.Id)).Status);
  }

  [Fact]
  public async Task Garmin_catalog_contains_runnable_plan_variants_and_scheduled_watch_choices()
  {
    UserProfile runner = await CreateProfileAsync("Garmin runner");
    StoredWorkoutRevision primary = await CreateWorkoutAsync("Outdoor fixed pace", 7);
    StoredWorkoutRevision alternative = await CreateWorkoutAsync("Outdoor heart rate", 6);
    var revision = new WorkoutProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, "Outside-ready plan", null, "10K",
      [new WorkoutProgramItem(Guid.NewGuid(), primary.Id, 1, 1, 1, "Base",
        [new WorkoutProgramAlternative(alternative.Id, 2, "hr-alternative")])]);
    var programStore = new WorkoutProgramStore(_factory);
    await programStore.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await programStore.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(new DateOnly(2026, 8, 10), WeekdayFlags.Monday, "Europe/Brussels"),
      Now, Op("program.start"));
    var catalog = new GarminSyncCatalog(
      new WorkoutStore(_factory),
      programStore,
      new CalendarStore(_factory));

    IReadOnlyList<GarminSyncDocument> documents = await catalog.BuildSessionAsync(
      runner.Id, new DateOnly(2026, 8, 10), alternative.Id, CancellationToken.None);
    Assert.Contains(documents, document => document.Kind == "Workout" && document.SourceId == alternative.Id);
    Assert.DoesNotContain(documents, document => document.Kind == "Workout" && document.SourceId == primary.Id);
    GarminSyncDocument calendar = Assert.Single(documents, document => document.Kind == "Calendar");
    using JsonDocument calendarJson = JsonDocument.Parse(calendar.PayloadJson);
    Assert.Single(calendarJson.RootElement.GetProperty("occurrences")[0].GetProperty("workouts").EnumerateArray());
    Assert.Equal(alternative.Id, calendarJson.RootElement.GetProperty("occurrences")[0]
      .GetProperty("workouts")[0].GetProperty("workoutRevisionId").GetGuid());
  }

  [Fact]
  public async Task Schedule_changes_are_profile_scoped_versioned_and_skips_advance_without_completion()
  {
    UserProfile runner = await CreateProfileAsync("Schedule runner");
    UserProfile other = await CreateProfileAsync("Other runner");
    StoredWorkoutRevision first = await CreateWorkoutAsync("First", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Second", 7);
    StoredWorkoutRevision third = await CreateWorkoutAsync("Third", 8);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, first.Id, second.Id, third.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));

    await Assert.ThrowsAsync<KeyNotFoundException>(() => store.PreviewScheduleChangeAsync(
      other.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveOne, new DateOnly(2026, 8, 11)));
    WorkoutProgramScheduleChangePreview preview = await store.PreviewScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveFollowing, new DateOnly(2026, 8, 11));
    Assert.True(preview.CanApply);
    Assert.Equal(3, preview.Impacts.Count);
    Assert.Equal([new DateOnly(2026, 8, 11), new DateOnly(2026, 8, 13), new DateOnly(2026, 8, 16)],
      preview.Impacts.Select(static impact => impact.NewDate));

    WorkoutProgramScheduleChangePreview moved = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveFollowing,
      new DateOnly(2026, 8, 11), run.Version, Op("program.schedule.change"));
    Assert.Equal(2, moved.RunVersion);
    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() => store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.Skip,
      null, run.Version, Op("program.schedule.change")));

    WorkoutProgramScheduleChangePreview skipped = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.Skip,
      null, moved.RunVersion, Op("program.schedule.change"));
    Assert.Equal(3, skipped.RunVersion);
    StoredWorkoutProgramProgress progress = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(0, progress.Progress?.CompletedItemCount);
    Assert.Equal(1, progress.Progress?.SkippedItemCount);
    Assert.Equal(revision.Items[1].Id, progress.Progress?.NextItem?.Id);
    Assert.DoesNotContain(
      WorkoutProgramScheduleProjector.ProjectAll(
        revision, progress.Run!, progress.ScheduleOverrides, progress.ExtraOccurrences),
      item => item.Item.Id == revision.Items[0].Id);
    WorkoutProgramScheduleChangePreview skippedMoveFollowing = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveFollowing,
      new DateOnly(2026, 8, 12));
    Assert.False(skippedMoveFollowing.CanApply);
    Assert.Contains("skipped", skippedMoveFollowing.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Schedule_moves_do_not_place_a_plan_item_before_an_earlier_item()
  {
    UserProfile runner = await CreateProfileAsync("Chronology runner");
    StoredWorkoutRevision first = await CreateWorkoutAsync("First", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Second", 7);
    WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, first.Id, second.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(),
      runner.Id,
      revision.RevisionId,
      null,
      null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday,
        "Europe/Brussels"),
      Now,
      Op("program.start"));

    WorkoutProgramScheduleChangePreview moveOne = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[1].Id,
      WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 9));
    WorkoutProgramScheduleChangePreview moveFollowing = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[1].Id,
      WorkoutProgramScheduleAction.MoveFollowing,
      new DateOnly(2026, 8, 9));

    Assert.False(moveOne.CanApply);
    Assert.False(moveFollowing.CanApply);
    Assert.Contains("before", moveOne.Message, StringComparison.OrdinalIgnoreCase);
    Assert.Contains("before", moveFollowing.Message, StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Completed_session_can_be_repeated_on_a_full_calendar_without_rewinding_progress()
  {
    UserProfile runner = await CreateProfileAsync("Repeat runner");
    StoredWorkoutRevision first = await CreateWorkoutAsync("First", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Second", 7);
    StoredWorkoutRevision third = await CreateWorkoutAsync("Third", 8);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, first.Id, second.Id, third.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    await SaveTerminalSessionAsync(
      runner,
      first,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, run.Id, revision.Items[0].Id));

    WorkoutProgramScheduleChangePreview preview = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.Repeat,
      new DateOnly(2026, 8, 12));
    Assert.True(preview.CanApply);
    Assert.Equal([new DateOnly(2026, 8, 12)], preview.CollisionDates);
    Assert.Single(preview.Impacts, static impact => impact.IsRepeat);

    WorkoutProgramScheduleChangePreview repeated = await store.ApplyScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.Repeat,
      new DateOnly(2026, 8, 12),
      preview.RunVersion,
      Op("program.schedule.repeat"));
    StoredWorkoutProgramProgress afterRepeat = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(1, afterRepeat.Progress?.CompletedItemCount);
    Assert.Equal(revision.Items[1].Id, afterRepeat.Progress?.NextItem?.Id);
    ScheduledWorkoutProgramItem extra = Assert.Single(
      WorkoutProgramScheduleProjector.ProjectAll(
        revision, afterRepeat.Run!, afterRepeat.ScheduleOverrides, afterRepeat.ExtraOccurrences),
      static occurrence => occurrence.IsRepeat);
    Assert.Equal(new DateOnly(2026, 8, 12), extra.Date);
    Assert.Equal(revision.Items[0].Id, extra.Item.Id);

    WorkoutProgramScheduleChangePreview shiftPreview = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.RepeatAndShift,
      new DateOnly(2026, 8, 15));
    Assert.True(shiftPreview.CanApply);
    Assert.Equal(3, shiftPreview.Impacts.Count);
    Assert.Single(shiftPreview.Impacts, static impact => impact.IsRepeat);
    Assert.Equal(repeated.RunVersion, shiftPreview.RunVersion);
  }

  [Fact]
  public async Task Completed_session_can_move_to_an_exact_off_rhythm_date_without_rewinding_progress()
  {
    UserProfile runner = await CreateProfileAsync("Completed move runner");
    StoredWorkoutRevision first = await CreateWorkoutAsync("Completed first", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Upcoming second", 7);
    StoredWorkoutRevision third = await CreateWorkoutAsync("Upcoming third", 8);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, first.Id, second.Id, third.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    await SaveTerminalSessionAsync(
      runner,
      first,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, run.Id, revision.Items[0].Id));

    var targetDate = new DateOnly(2026, 8, 11); // Tuesday is outside the plan's rhythm.
    WorkoutProgramScheduleChangePreview preview = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveOne,
      targetDate);

    Assert.True(preview.CanApply);
    WorkoutProgramScheduleImpact impact = Assert.Single(preview.Impacts);
    Assert.Equal(new DateOnly(2026, 8, 10), impact.CurrentDate);
    Assert.Equal(targetDate, impact.NewDate);
    foreach (WorkoutProgramScheduleAction blockedAction in new[]
      {
        WorkoutProgramScheduleAction.Skip,
        WorkoutProgramScheduleAction.Restore,
      })
    {
      WorkoutProgramScheduleChangePreview blocked = await store.PreviewScheduleChangeAsync(
        runner.Id,
        run.Id,
        revision.Items[0].Id,
        blockedAction,
        null);
      Assert.False(blocked.CanApply);
      Assert.Contains("Completed", blocked.Message, StringComparison.OrdinalIgnoreCase);
    }

    WorkoutProgramScheduleChangePreview moved = await store.ApplyScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveOne,
      targetDate,
      preview.RunVersion,
      Op("program.schedule.move.completed"));

    Assert.True(moved.CanApply);
    StoredWorkoutProgramProgress progress = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(1, progress.Progress?.CompletedItemCount);
    Assert.Equal(revision.Items[1].Id, progress.Progress?.NextItem?.Id);
    ScheduledWorkoutProgramItem completed = Assert.Single(
      WorkoutProgramScheduleProjector.ProjectAll(
        revision, progress.Run!, progress.ScheduleOverrides, progress.ExtraOccurrences),
      occurrence => occurrence.Item.Id == revision.Items[0].Id && !occurrence.IsRepeat);
    Assert.Equal(targetDate, completed.Date);

    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    WorkoutSessionEntity linkedSession = Assert.Single(await context.WorkoutSessions.AsNoTracking()
      .Where(session => session.WorkoutProgramRunId == run.Id &&
        session.WorkoutProgramItemId == revision.Items[0].Id)
      .ToListAsync());
    Assert.Equal(nameof(SessionState.Completed), linkedSession.State);
    Assert.Equal(Now.AddHours(1).AddSeconds(1), linkedSession.StartedAtUtc);
    Assert.Equal(Now.AddHours(1).AddSeconds(1).AddMinutes(10), linkedSession.EndedAtUtc);
  }

  [Fact]
  public async Task Completed_late_session_can_shift_itself_and_all_later_incomplete_sessions_without_rewriting_history()
  {
    UserProfile runner = await CreateProfileAsync("Late completion runner");
    StoredWorkoutRevision first = await CreateWorkoutAsync("Completed late", 6);
    StoredWorkoutRevision second = await CreateWorkoutAsync("Upcoming second", 7);
    StoredWorkoutRevision third = await CreateWorkoutAsync("Upcoming third", 8);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, first.Id, second.Id, third.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    await SaveTerminalSessionAsync(
      runner,
      first,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, run.Id, revision.Items[0].Id));

    var actualCompletionDate = new DateOnly(2026, 8, 12);
    WorkoutProgramScheduleChangePreview preview = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveFollowing,
      actualCompletionDate);

    Assert.True(preview.CanApply);
    Assert.Equal(3, preview.Impacts.Count);
    Assert.Equal(
      [new DateOnly(2026, 8, 12), new DateOnly(2026, 8, 14), new DateOnly(2026, 8, 17)],
      preview.Impacts.Select(static impact => impact.NewDate));

    WorkoutProgramScheduleChangePreview shifted = await store.ApplyScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveFollowing,
      actualCompletionDate,
      preview.RunVersion,
      Op("program.schedule.shift.completed-late"));

    Assert.True(shifted.CanApply);
    StoredWorkoutProgramProgress progress = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(1, progress.Progress?.CompletedItemCount);
    Assert.Equal(revision.Items[1].Id, progress.Progress?.NextItem?.Id);
    ScheduledWorkoutProgramItem[] canonical = WorkoutProgramScheduleProjector.ProjectAll(
        revision, progress.Run!, progress.ScheduleOverrides, progress.ExtraOccurrences)
      .Where(static occurrence => !occurrence.IsRepeat)
      .OrderBy(static occurrence => occurrence.Item.Position)
      .ToArray();
    Assert.Equal(
      [new DateOnly(2026, 8, 12), new DateOnly(2026, 8, 14), new DateOnly(2026, 8, 17)],
      canonical.Select(static occurrence => occurrence.Date));

    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    WorkoutSessionEntity linkedSession = Assert.Single(await context.WorkoutSessions.AsNoTracking()
      .Where(session => session.WorkoutProgramRunId == run.Id &&
        session.WorkoutProgramItemId == revision.Items[0].Id)
      .ToListAsync());
    Assert.Equal(nameof(SessionState.Completed), linkedSession.State);
    Assert.Equal(Now.AddHours(1).AddSeconds(1), linkedSession.StartedAtUtc);
    Assert.Equal(Now.AddHours(1).AddSeconds(1).AddMinutes(10), linkedSession.EndedAtUtc);
  }

  [Fact]
  public async Task Default_day_change_previews_and_reschedules_only_future_generated_sessions()
  {
    UserProfile runner = await CreateProfileAsync("Rhythm runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Foundation", 6);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1,
      workout.Id, workout.Id, workout.Id, workout.Id, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    await SaveTerminalSessionAsync(
      runner,
      workout,
      SessionState.Completed,
      new WorkoutSessionSelection(WorkoutSelectionSource.Program, run.Id, revision.Items[0].Id));
    WorkoutProgramScheduleChangePreview moved = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[1].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 16), run.Version, Op("program.schedule.move"));
    WorkoutProgramScheduleChangePreview skipped = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[2].Id, WorkoutProgramScheduleAction.Skip,
      null, moved.RunVersion, Op("program.schedule.skip"));
    WorkoutProgramScheduleChangePreview repeated = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.Repeat,
      new DateOnly(2026, 8, 14), skipped.RunVersion, Op("program.schedule.repeat"));

    WeekdayFlags newDays = WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday;
    WorkoutProgramDefaultDaysPreview preview = await store.PreviewDefaultDaysChangeAsync(
      runner.Id, run.Id, newDays, new DateOnly(2026, 8, 10), new DateOnly(2026, 8, 4));

    Assert.True(preview.CanApply);
    Assert.Equal(repeated.RunVersion, preview.RunVersion);
    Assert.Equal(3, preview.Impacts.Count);
    Assert.Equal(
      [new DateOnly(2026, 8, 18), new DateOnly(2026, 8, 20), new DateOnly(2026, 8, 23)],
      preview.Impacts.Select(static impact => impact.NewDate));
    Assert.Empty(preview.CollisionDates);
    Assert.Equal(4, preview.PreservedExceptionCount);
    Assert.Equal(64, preview.Revision.Length);

    WorkoutProgramDefaultDaysPreview applied = await store.ApplyDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      newDays,
      preview.EffectiveDate,
      new DateOnly(2026, 8, 4),
      preview.RunVersion,
      preview.Revision,
      Op("program.default-days.change"));
    Assert.Equal(preview.RunVersion + 1, applied.RunVersion);

    StoredWorkoutProgramProgress progress = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(newDays, progress.Run!.Schedule!.Weekdays);
    IReadOnlyList<ScheduledWorkoutProgramItem> projected = WorkoutProgramScheduleProjector.ProjectAll(
      revision, progress.Run, progress.ScheduleOverrides, progress.ExtraOccurrences);
    Assert.Contains(projected, item => item.Item.Id == revision.Items[0].Id && !item.IsRepeat && item.Date == new DateOnly(2026, 8, 10));
    Assert.Contains(projected, item => item.Item.Id == revision.Items[0].Id && item.IsRepeat && item.Date == new DateOnly(2026, 8, 14));
    Assert.Contains(projected, item => item.Item.Id == revision.Items[1].Id && item.Date == new DateOnly(2026, 8, 16));
    Assert.DoesNotContain(projected, item => item.Item.Id == revision.Items[2].Id);
    Assert.Contains(projected, item => item.Item.Id == revision.Items[5].Id && item.Date == new DateOnly(2026, 8, 23));

    WeekdayFlags originalDays = WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday;
    WorkoutProgramDefaultDaysPreview reversePreview = await store.PreviewDefaultDaysChangeAsync(
      runner.Id, run.Id, originalDays, new DateOnly(2026, 8, 17), new DateOnly(2026, 8, 4));
    Assert.True(reversePreview.CanApply);
    Assert.DoesNotContain(reversePreview.Impacts, impact => impact.ProgramItemId == revision.Items[1].Id);
    Assert.DoesNotContain(reversePreview.Impacts, impact => impact.ProgramItemId == revision.Items[2].Id);

    await store.ApplyDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      originalDays,
      reversePreview.EffectiveDate,
      new DateOnly(2026, 8, 4),
      reversePreview.RunVersion,
      reversePreview.Revision,
      Op("program.default-days.reverse"));
    StoredWorkoutProgramProgress reversedProgress = Assert.Single(await store.ListAsync(runner.Id));
    Assert.Equal(originalDays, reversedProgress.Run!.Schedule!.Weekdays);
    IReadOnlyList<ScheduledWorkoutProgramItem> reversed = WorkoutProgramScheduleProjector.ProjectAll(
      revision, reversedProgress.Run, reversedProgress.ScheduleOverrides, reversedProgress.ExtraOccurrences);
    Assert.Contains(reversed, item => item.Item.Id == revision.Items[0].Id && item.IsRepeat && item.Date == new DateOnly(2026, 8, 14));
    Assert.Contains(reversed, item => item.Item.Id == revision.Items[1].Id && item.Date == new DateOnly(2026, 8, 16));
    Assert.DoesNotContain(reversed, item => item.Item.Id == revision.Items[2].Id);
  }

  [Fact]
  public async Task Schedule_moves_and_training_day_changes_block_double_booked_dates()
  {
    UserProfile runner = await CreateProfileAsync("Collision guard runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Collision guard workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1,
      workout.Id, workout.Id, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));

    WorkoutProgramScheduleChangePreview moveOne = await store.PreviewScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 12));
    Assert.False(moveOne.CanApply);
    Assert.Equal([new DateOnly(2026, 8, 12)], moveOne.CollisionDates);
    Assert.Contains("empty date", moveOne.Message, StringComparison.OrdinalIgnoreCase);
    await Assert.ThrowsAsync<ArgumentException>(() => store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 12), run.Version, Op("program.schedule.move")));

    WorkoutProgramScheduleChangePreview moveFollowing = await store.PreviewScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[1].Id, WorkoutProgramScheduleAction.MoveFollowing,
      new DateOnly(2026, 8, 10));
    Assert.False(moveFollowing.CanApply);
    Assert.Equal([new DateOnly(2026, 8, 10)], moveFollowing.CollisionDates);

    WorkoutProgramScheduleChangePreview moved = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 18), run.Version, Op("program.schedule.move.allowed"));
    WorkoutProgramDefaultDaysPreview days = await store.PreviewDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      new DateOnly(2026, 8, 10),
      new DateOnly(2026, 8, 4));
    Assert.True(days.CanApply);
    Assert.Empty(days.CollisionDates);
    Assert.All(days.Impacts, impact => Assert.True(impact.NewDate > new DateOnly(2026, 8, 18)));
    await store.ApplyDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      days.EffectiveDate,
      new DateOnly(2026, 8, 4),
      moved.RunVersion,
      days.Revision,
      Op("program.default-days.change"));
  }

  [Fact]
  public async Task Default_day_changes_preserve_program_order_around_manually_moved_sessions()
  {
    UserProfile runner = await CreateProfileAsync("Ordered rhythm runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Ordered rhythm workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1,
      workout.Id, workout.Id, workout.Id, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    WorkoutProgramScheduleChangePreview moved = await store.ApplyScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[1].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 16), run.Version, Op("program.schedule.move"));

    WorkoutProgramDefaultDaysPreview preview = await store.PreviewDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Sunday,
      new DateOnly(2026, 8, 10),
      new DateOnly(2026, 8, 4));

    Assert.True(preview.CanApply);
    Assert.Equal(moved.RunVersion, preview.RunVersion);
    WorkoutProgramDefaultDaysImpact third = Assert.Single(
      preview.Impacts, impact => impact.ProgramItemId == revision.Items[2].Id);
    Assert.True(third.NewDate > new DateOnly(2026, 8, 16));

    await store.ApplyDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      (WeekdayFlags)preview.NewWeekdayMask,
      preview.EffectiveDate,
      new DateOnly(2026, 8, 4),
      preview.RunVersion,
      preview.Revision,
      Op("program.default-days.change"));
    StoredWorkoutProgramProgress progress = Assert.Single(await store.ListAsync(runner.Id));
    DateOnly[] dates = WorkoutProgramScheduleProjector.ProjectAll(
        revision, progress.Run!, progress.ScheduleOverrides, progress.ExtraOccurrences)
      .Where(static occurrence => !occurrence.IsRepeat)
      .OrderBy(static occurrence => occurrence.Item.Position)
      .Select(static occurrence => occurrence.Date)
      .ToArray();
    Assert.Equal(dates.Order().ToArray(), dates);
    Assert.Equal(dates.Length, dates.Distinct().Count());
  }

  [Fact]
  public async Task Program_schedule_changes_block_dates_occupied_by_recurring_workouts()
  {
    UserProfile runner = await CreateProfileAsync("Cross-source collision runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Cross-source workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1,
      workout.Id, workout.Id, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));
    Guid calendarSeriesId = Guid.NewGuid();
    await new CalendarStore(_factory).CreateAsync(
      new CalendarSeriesDefinition(
        calendarSeriesId,
        runner.Id,
        "Tuesday workout",
        "Europe/Brussels",
        new WeeklyRecurrence(
          new DateOnly(2026, 8, 11),
          new DateOnly(2026, 8, 18),
          1,
          WeekdayFlags.Tuesday),
        [new WorkoutAlternative(workout.Id, 0)],
        []),
      Now,
      Op("calendar.create"));

    WorkoutProgramScheduleChangePreview move = await store.PreviewScheduleChangeAsync(
      runner.Id, run.Id, revision.Items[0].Id, WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2026, 8, 18));
    Assert.False(move.CanApply);
    Assert.Equal([new DateOnly(2026, 8, 18)], move.CollisionDates);

    WorkoutProgramDefaultDaysPreview days = await store.PreviewDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      new DateOnly(2026, 8, 10),
      new DateOnly(2026, 8, 4));
    Assert.False(days.CanApply);
    Assert.Contains(new DateOnly(2026, 8, 11), days.CollisionDates);
  }

  [Fact]
  public async Task Schedule_effective_date_uses_the_plans_time_zone_instead_of_the_gateway_time_zone()
  {
    UserProfile runner = await CreateProfileAsync("Time-zone runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Time-zone workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 9),
        WeekdayFlags.Sunday,
        "America/New_York"),
      Now,
      Op("program.start"));

    DateOnly localDate = await store.GetScheduleLocalDateAsync(
      runner.Id,
      run.Id,
      DateTimeOffset.Parse("2026-08-10T02:00:00Z"));

    Assert.Equal(new DateOnly(2026, 8, 9), localDate);
  }

  [Fact]
  public async Task Repeated_move_limits_are_measured_from_the_current_scheduled_date()
  {
    UserProfile runner = await CreateProfileAsync("Repeated move runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Repeated move workout", 6);
    WorkoutProgramRevision revision = ProgramRevision(Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(new DateOnly(2026, 8, 10), WeekdayFlags.Monday, "Europe/Brussels"),
      Now,
      Op("program.start"));
    WorkoutProgramScheduleChangePreview first = await store.ApplyScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2027, 8, 10),
      run.Version,
      Op("program.schedule.move.first"));

    WorkoutProgramScheduleChangePreview second = await store.PreviewScheduleChangeAsync(
      runner.Id,
      run.Id,
      revision.Items[0].Id,
      WorkoutProgramScheduleAction.MoveOne,
      new DateOnly(2028, 8, 9));

    Assert.True(second.CanApply);
    Assert.Equal(first.RunVersion, second.RunVersion);
  }

  [Fact]
  public async Task Default_day_change_rejects_invalid_counts_past_dates_and_stale_previews()
  {
    UserProfile runner = await CreateProfileAsync("Guarded rhythm runner");
    StoredWorkoutRevision workout = await CreateWorkoutAsync("Easy", 6);
    WorkoutProgramRevision revision = ProgramRevision(
      Guid.NewGuid(), Guid.NewGuid(), 1, workout.Id, workout.Id, workout.Id);
    var store = new WorkoutProgramStore(_factory);
    await store.CreateAsync(revision, Now, Op("program.create"));
    WorkoutProgramRun run = await store.StartAsync(
      Guid.NewGuid(), runner.Id, revision.RevisionId, null, null,
      new WorkoutProgramSchedule(
        new DateOnly(2026, 8, 10),
        WeekdayFlags.Monday | WeekdayFlags.Wednesday | WeekdayFlags.Saturday,
        "Europe/Brussels"),
      Now, Op("program.start"));

    WorkoutProgramDefaultDaysPreview wrongCount = await store.PreviewDefaultDaysChangeAsync(
      runner.Id, run.Id, WeekdayFlags.Tuesday, new DateOnly(2026, 8, 10), new DateOnly(2026, 8, 4));
    Assert.False(wrongCount.CanApply);
    Assert.Contains("exactly 3", wrongCount.Message);
    WorkoutProgramDefaultDaysPreview past = await store.PreviewDefaultDaysChangeAsync(
      runner.Id, run.Id, WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      new DateOnly(2026, 8, 3), new DateOnly(2026, 8, 4));
    Assert.False(past.CanApply);

    WorkoutProgramDefaultDaysPreview preview = await store.PreviewDefaultDaysChangeAsync(
      runner.Id, run.Id, WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      new DateOnly(2026, 8, 10), new DateOnly(2026, 8, 4));
    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() => store.ApplyDefaultDaysChangeAsync(
      runner.Id,
      run.Id,
      WeekdayFlags.Tuesday | WeekdayFlags.Thursday | WeekdayFlags.Sunday,
      preview.EffectiveDate,
      new DateOnly(2026, 8, 4),
      preview.RunVersion,
      new string('0', 64),
      Op("program.default-days.change")));
  }

  private async Task<UserProfile> CreateProfileAsync(string displayName)
  {
    var profile = new UserProfile(
      Guid.NewGuid(),
      displayName,
      UnitSystem.Metric,
      70,
      190,
      12,
      [new HeartRateZone(1, "Warm up", 95, 114)],
      HeartRateControllerSettings.Default);
    await new ProfileStore(_factory).CreateAsync(profile, Now, Op("profile.create"));
    return profile;
  }

  private async Task<StoredWorkoutRevision> CreateWorkoutAsync(string title, double speed) =>
    await new WorkoutStore(_factory).CreateAsync(
      Guid.NewGuid(),
      Definition(title, speed),
      Now,
      Op("workout.create"));

  private async Task SaveTerminalSessionAsync(
    UserProfile profile,
    StoredWorkoutRevision workout,
    SessionState state,
    WorkoutSessionSelection selection)
  {
    var store = new SessionStore(_factory);
    Guid sessionId = Guid.NewGuid();
    DateTimeOffset armedAt = Now.AddHours(1);
    DateTimeOffset startedAt = armedAt.AddSeconds(1);
    DateTimeOffset endedAt = startedAt.AddMinutes(10);
    await store.CreateAsync(new NewWorkoutSession(
      sessionId,
      profile.Id,
      profile.DisplayName,
      workout.Id,
      "Program workout",
      armedAt,
      "{}",
      SessionMetricAlgorithms.EstimatedCaloriesV1,
      selection));
    await store.MarkRunningAsync(sessionId, startedAt);
    await store.FinalizeAsync(new SessionSummary(
      sessionId,
      profile.Id,
      profile.DisplayName,
      workout.Id,
      "Program workout",
      state,
      startedAt,
      endedAt,
      TimeSpan.FromMinutes(10),
      1,
      70,
      130,
      145,
      6,
      1));
  }

  private static WorkoutProgramRevision ProgramRevision(
    Guid programId,
    Guid revisionId,
    int revisionNumber,
    params Guid[] workoutRevisionIds) => new(
      programId,
      revisionId,
      revisionNumber,
      "First 5K",
      "A deterministic progression.",
      "5K",
      workoutRevisionIds.Select((id, index) =>
        new WorkoutProgramItem(Guid.NewGuid(), id, index + 1)).ToArray());

  private static WorkoutDefinition Definition(string title, double speed) => new(
    1,
    title,
    null,
    [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(10)), new FixedSpeed(speed), new FixedIncline(1))]);

  private static PersistenceWriteOperation Op(string type) => new(
    Guid.NewGuid(),
    type,
    200,
    "{}",
    Now,
    new string('a', 64));

  private sealed class CountingDbContextFactory(string databasePath, CommandCounterInterceptor counter)
    : IDbContextFactory<TreadmillRunnerDbContext>
  {
    private readonly DbContextOptions<TreadmillRunnerDbContext> _options =
      new DbContextOptionsBuilder<TreadmillRunnerDbContext>()
        .UseSqlite(new SqliteConnectionStringBuilder
        {
          DataSource = databasePath,
          Mode = SqliteOpenMode.ReadWrite,
          Pooling = false,
        }.ToString())
        .AddInterceptors(counter)
        .Options;

    public TreadmillRunnerDbContext CreateDbContext() => new(_options);

    public Task<TreadmillRunnerDbContext> CreateDbContextAsync(CancellationToken cancellationToken = default)
    {
      cancellationToken.ThrowIfCancellationRequested();
      return Task.FromResult(CreateDbContext());
    }
  }

  private sealed class CommandCounterInterceptor : DbCommandInterceptor
  {
    private int _readerCount;
    public int ReaderCount => Volatile.Read(ref _readerCount);

    public override InterceptionResult<DbDataReader> ReaderExecuting(
      DbCommand command,
      CommandEventData eventData,
      InterceptionResult<DbDataReader> result)
    {
      Interlocked.Increment(ref _readerCount);
      return result;
    }

    public override ValueTask<InterceptionResult<DbDataReader>> ReaderExecutingAsync(
      DbCommand command,
      CommandEventData eventData,
      InterceptionResult<DbDataReader> result,
      CancellationToken cancellationToken = default)
    {
      Interlocked.Increment(ref _readerCount);
      return ValueTask.FromResult(result);
    }
  }

}
