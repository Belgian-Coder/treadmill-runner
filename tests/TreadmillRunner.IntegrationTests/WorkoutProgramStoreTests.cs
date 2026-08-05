using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
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
      Guid.NewGuid(), firstRunner.Id, program.RevisionId, null, null, Now.AddMinutes(1), Op("program.start"));
    WorkoutProgramRun secondRun = await programStore.StartAsync(
      Guid.NewGuid(), secondRunner.Id, program.RevisionId, null, null, Now.AddMinutes(1), Op("program.start"));

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
