using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
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
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "programs.db"));
    await using TreadmillRunnerDbContext context = await _factory.CreateDbContextAsync();
    await context.Database.EnsureCreatedAsync();
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory)) Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
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
      new DateOnly(2026, 8, 18), run.Version, Op("program.schedule.move"));
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
    Assert.Equal([new DateOnly(2026, 8, 18)], preview.CollisionDates);
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
    Assert.Contains(projected, item => item.Item.Id == revision.Items[1].Id && item.Date == new DateOnly(2026, 8, 18));
    Assert.DoesNotContain(projected, item => item.Item.Id == revision.Items[2].Id);
    Assert.Contains(projected, item => item.Item.Id == revision.Items[5].Id && item.Date == new DateOnly(2026, 8, 23));
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

}
