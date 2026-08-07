using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Household;
using TreadmillRunner.Core.Profiles;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PersistenceStoreTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private IDbContextFactory<TreadmillRunnerDbContext> _factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    _factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(_directory, "stores.db"));
    await using var context = await _factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    Directory.Delete(_directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Profile_store_round_trips_nullable_limits_unique_name_and_archive_version()
  {
    var store = new ProfileStore(_factory);
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var profile = Profile(Guid.NewGuid(), "Runner");
    var createOperation = Op("profile.create", now) with { RequestFingerprint = new string('a', 64) };

    var created = await store.CreateAsync(profile, now, createOperation);

    Assert.Equal(72.5, created.Profile.WeightKilograms);
    Assert.Null(created.Profile.MaximumHeartRateBpm);
    Assert.Null(created.Profile.MaximumSpeedKph);
    Assert.Equal(0.3, created.Profile.HeartRateController.IncreaseStepKph);
    Assert.Equal(45, created.Profile.HeartRateController.IncreaseCooldownSeconds);
    Assert.Equal(0.7, created.Profile.HeartRateController.DecreaseStepKph);
    Assert.Equal(20, created.Profile.HeartRateController.DecreaseCooldownSeconds);
    Assert.Equal(1, created.Version);
    var replay = await Assert.ThrowsAsync<OperationReplayException>(() =>
      store.CreateAsync(profile, now, createOperation));
    Assert.Equal(createOperation.OutcomeJson, replay.Receipt.OutcomeJson);
    var scopeConflict = await Assert.ThrowsAsync<OperationScopeConflictException>(() =>
      store.CreateAsync(
        Profile(Guid.NewGuid(), "Another Runner"),
        now,
        createOperation with { RequestFingerprint = new string('b', 64) }));
    Assert.Equal(createOperation.ClientOperationId, scopeConflict.Receipt.ClientOperationId);
    Assert.Equal(createOperation.RequestFingerprint, scopeConflict.Receipt.RequestFingerprint);
    await Assert.ThrowsAsync<OperationScopeConflictException>(() =>
      store.CreateAsync(
        Profile(Guid.NewGuid(), "Different Action Runner"),
        now,
        createOperation with { OperationType = "profile.update" }));
    await Assert.ThrowsAsync<DbUpdateException>(() =>
      store.CreateAsync(Profile(Guid.NewGuid(), " runner "), now, Op("profile.create", now)));

    Assert.True(await store.SetArchivedAsync(profile.Id, true, created.Version, now.AddMinutes(1), Op("profile.archive", now)));
    var archived = await store.FindAsync(profile.Id);
    Assert.True(archived!.IsArchived);
    Assert.Equal(2, archived.Version);
    Assert.Equal(now.AddMinutes(1), archived.ArchivedAtUtc);
    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() =>
      store.SetArchivedAsync(profile.Id, false, expectedVersion: 1, now.AddMinutes(2), Op("profile.restore", now)));

    var missingOperation = Op("profile.archive", now) with
    {
      NotFoundStatusCode = 404,
      NotFoundOutcomeJson = "{\"code\":\"profile_not_found\"}",
    };
    Assert.False(await store.SetArchivedAsync(
      Guid.NewGuid(), true, expectedVersion: 1, now.AddMinutes(3), missingOperation));
    var missingReceipt = await new OperationReceiptStore(_factory).FindAsync(missingOperation.ClientOperationId);
    Assert.Equal(404, missingReceipt!.StatusCode);
    Assert.Equal(missingOperation.NotFoundOutcomeJson, missingReceipt.OutcomeJson);
  }

  [Fact]
  public async Task Local_first_store_round_trips_runner_preferences_goals_and_backup_verification()
  {
    var now = DateTimeOffset.Parse("2026-08-07T10:00:00Z");
    var profile = Profile(Guid.NewGuid(), "Local Runner");
    await new ProfileStore(_factory).CreateAsync(profile, now, Op("profile.create", now));
    var store = new LocalFirstExperienceStore(_factory);

    VersionedRunnerExperiencePreferences defaults = await store.GetPreferencesAsync(profile.Id);
    Assert.Equal(0, defaults.Version);
    Assert.Equal(LiveDisplayStyle.Balanced, defaults.Preferences.DisplayStyle);

    var requested = new RunnerExperiencePreferences(
      LiveDisplayStyle.LargeText,
      [LiveMetric.ElapsedTime, LiveMetric.HeartRate, LiveMetric.Distance],
      new RunCuePreferences(true, true, false, true, true, 65));
    VersionedRunnerExperiencePreferences saved = await store.SavePreferencesAsync(
      profile.Id, requested, 0, now.AddMinutes(1));
    Assert.Equal(1, saved.Version);
    RunnerExperiencePreferences reloaded = (await store.GetPreferencesAsync(profile.Id)).Preferences;
    Assert.Equal(requested.DisplayStyle, reloaded.DisplayStyle);
    Assert.Equal(requested.PrimaryMetrics, reloaded.PrimaryMetrics);
    Assert.Equal(requested.Cues, reloaded.Cues);
    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() =>
      store.SavePreferencesAsync(profile.Id, requested, 0, now.AddMinutes(2)));

    LocalGoalDefinition goal = await store.SaveGoalAsync(
      profile.Id, null, "Sessions", "Weekly", 3, true, null, now.AddMinutes(3));
    LocalGoalDefinition updatedGoal = await store.SaveGoalAsync(
      profile.Id, goal.Id, "Sessions", "Weekly", 4, true, goal.Version, now.AddMinutes(4));
    Assert.Equal(2, updatedGoal.Version);
    Assert.Equal(4, Assert.Single(await store.ListGoalsAsync(profile.Id)).TargetValue);

    string backupDirectory = Path.Combine(_directory, "household-backups");
    VersionedLocalBackupPolicy policy = await store.SaveBackupPolicyAsync(
      null, new LocalBackupPolicy(backupDirectory, 24, 7, true), null, now.AddMinutes(5));
    var verification = new StoredBackupVerification(
      Guid.NewGuid(), policy.Id, Path.Combine(backupDirectory, "backup.db"), "Verified",
      "Full SQLite integrity check passed.", 4096, now.AddMinutes(6), now.AddMinutes(7));
    await store.RecordBackupVerificationAsync(verification);

    Assert.Equal(policy, await store.GetBackupPolicyAsync());
    Assert.Equal(verification, Assert.Single(await store.ListBackupVerificationsAsync(10)));
  }

  [Fact]
  public async Task Import_confirmation_is_atomic_idempotent_and_revision_queries_are_stable()
  {
    var store = new WorkoutStore(_factory);
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var operationId = Guid.NewGuid();
    var previewId = Guid.NewGuid();
    var workoutId = Guid.NewGuid();
    var definition = Definition("Imported Run", 8);
    var audit = new ImportAuditRecord(
      Guid.NewGuid(), null, workoutId, Guid.Empty, "incoming.fit", "fit", new string('d', 64), "[\"cadence-rounded\"]", now);

    var operation = Op("workout.import.confirm", now) with
    {
      ClientOperationId = operationId,
      RequestFingerprint = new string('c', 64),
    };
    var first = await store.ConfirmImportAsync(operation, previewId, workoutId, definition, audit, now);
    var replay = await store.ConfirmImportAsync(operation, previewId, workoutId, definition, audit, now.AddMinutes(1));
    await Assert.ThrowsAsync<InvalidOperationException>(() =>
      store.ConfirmImportAsync(operation, Guid.NewGuid(), workoutId, definition, audit, now.AddMinutes(1)));
    await Assert.ThrowsAsync<OperationScopeConflictException>(() =>
      store.ConfirmImportAsync(
        operation with { RequestFingerprint = new string('d', 64) },
        previewId,
        workoutId,
        definition,
        audit,
        now.AddMinutes(1)));
    var duplicate = await store.ConfirmImportAsync(
      Op("workout.import.confirm", now) with { RequestFingerprint = new string('e', 64) },
      Guid.NewGuid(), Guid.NewGuid(), definition, audit with { Id = Guid.NewGuid() }, now.AddMinutes(2));
    var duplicateAppend = await store.AppendRevisionAsync(
      workoutId, definition, now.AddMinutes(3), Op("workout.revision.append", now));

    Assert.False(first.Replayed);
    Assert.True(replay.Replayed);
    Assert.Equal(first.Revision.Id, replay.Revision.Id);
    Assert.Equal(first.Receipt, replay.Receipt);
    Assert.Equal("[\"cadence-rounded\"]", replay.Receipt.WarningSummaryJson);
    Assert.False(duplicate.Replayed);
    Assert.Equal(first.Revision.Id, duplicate.Revision.Id);
    Assert.Equal(first.Revision.Id, duplicateAppend.Id);
    Assert.Single(await store.ListAsync());
    Assert.Single(await store.ListRevisionsAsync(workoutId));
    await using var context = await _factory.CreateDbContextAsync();
    Assert.Equal(2, await context.ImportAudits.CountAsync());
    Assert.Equal(3, await context.OperationReceipts.CountAsync());
  }

  [Fact]
  public async Task Concurrent_revision_appends_receive_distinct_monotonic_numbers_and_complete_receipts()
  {
    var store = new WorkoutStore(_factory);
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var workoutId = Guid.NewGuid();
    await store.CreateAsync(workoutId, Definition("Initial", 7), now, Op("workout.create", now));
    var firstOperation = Op("workout.revision.append", now);
    var secondOperation = Op("workout.revision.append", now);

    var revisions = await Task.WhenAll(
      store.AppendRevisionAsync(workoutId, Definition("Tempo", 8), now.AddMinutes(1), firstOperation),
      store.AppendRevisionAsync(workoutId, Definition("Intervals", 9), now.AddMinutes(2), secondOperation));

    Assert.Equal([2, 3], revisions.Select(revision => revision.RevisionNumber).Order().ToArray());
    Assert.Equal(3, (await store.ListRevisionsAsync(workoutId)).Count);
    var receiptStore = new OperationReceiptStore(_factory);
    var firstReceipt = await receiptStore.FindAsync(firstOperation.ClientOperationId);
    var parsed = WorkoutRevisionReceipt.Parse(firstReceipt!.OutcomeJson);
    Assert.Contains(revisions, revision => revision.Id == parsed.Id);
    Assert.Equal(firstReceipt.StatusCode, firstOperation.StatusCode);
  }

  [Fact]
  public async Task Workout_reuse_returns_distinct_completed_structured_revisions_for_profile()
  {
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    UserProfile profile = Profile(Guid.NewGuid(), "Reuse Runner");
    await new ProfileStore(_factory).CreateAsync(profile, now, Op("profile.create", now));
    var workouts = new WorkoutStore(_factory);
    StoredWorkoutRevision structured = await workouts.CreateAsync(
      Guid.NewGuid(), Definition("Easy repeat", 6.5), now, Op("workout.create", now));
    StoredWorkoutRevision manual = await workouts.CreateAsync(
      Guid.NewGuid(), Definition("Manual run", 0.8), now, Op("workout.create", now), kind: WorkoutKind.ManualTemplate);
    var sessions = new SessionStore(_factory);

    async Task Complete(Guid revisionId, string title, DateTimeOffset endedAt)
    {
      Guid sessionId = Guid.NewGuid();
      DateTimeOffset startedAt = endedAt.AddMinutes(-20);
      await sessions.CreateAsync(new NewWorkoutSession(
        sessionId, profile.Id, profile.DisplayName, revisionId, title,
        startedAt.AddSeconds(-2), "{}", SessionMetricAlgorithms.EstimatedCaloriesV1));
      await sessions.MarkRunningAsync(sessionId, startedAt);
      await sessions.FinalizeAsync(new SessionSummary(
        sessionId, profile.Id, profile.DisplayName, revisionId, title,
        SessionState.Completed, startedAt, endedAt, TimeSpan.FromMinutes(20),
        2, 120, null, null, 6, 1));
    }

    await Complete(structured.Id, "Easy repeat", now.AddHours(1));
    await Complete(structured.Id, "Easy repeat", now.AddHours(2));
    await Complete(manual.Id, "Manual run", now.AddHours(3));

    StoredWorkoutReuse reusable = Assert.Single(await workouts.ListReusableAsync(profile.Id));
    Assert.Equal(structured.Id, reusable.WorkoutRevisionId);
    Assert.Equal(2, reusable.CompletionCount);
    Assert.Equal(now.AddHours(2), reusable.LastCompletedAtUtc);
  }

  [Fact]
  public async Task Calendar_store_round_trips_series_and_exact_day_selection()
  {
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var profileStore = new ProfileStore(_factory);
    var profile = Profile(Guid.NewGuid(), "Calendar Runner");
    await profileStore.CreateAsync(profile, now, Op("profile.create", now));
    var workoutStore = new WorkoutStore(_factory);
    var revision = await workoutStore.CreateAsync(Guid.NewGuid(), Definition("Tuesday", 7), now, Op("workout.create", now));
    var series = new CalendarSeriesDefinition(
      Guid.NewGuid(), profile.Id, "Weekly", "Europe/Brussels",
      new WeeklyRecurrence(new DateOnly(2026, 8, 1), null, 1, WeekdayFlags.Tuesday),
      [new WorkoutAlternative(revision.Id, 0)], []);
    var store = new CalendarStore(_factory);

    var created = await store.CreateAsync(series, now, Op("calendar.create", now));
    var selection = new StoredTrainingDaySelection(
      profile.Id, new DateOnly(2026, 8, 4), series.Id, revision.Id, now);
    await store.SaveSelectionAsync(selection, Op("calendar.select", now));

    var renamed = new CalendarSeriesDefinition(
      series.Id, profile.Id, "Renamed Weekly", "Europe/Brussels", series.Recurrence,
      series.Alternatives, series.Exceptions);
    var updated = await store.UpdateAsync(renamed, created.Version, Op("calendar.update", now));

    Assert.Equal(1, created.Version);
    Assert.Equal(2, updated.Version);
    Assert.Equal("Renamed Weekly", (await store.FindAsync(series.Id))!.Series.Name);
    Assert.Equal(selection, await store.FindSelectionAsync(profile.Id, selection.Date));
    var invalid = selection with { WorkoutRevisionId = Guid.NewGuid() };
    await Assert.ThrowsAsync<InvalidOperationException>(() =>
      store.SaveSelectionAsync(invalid, Op("calendar.select", now)));
  }

  [Fact]
  public async Task Calendar_selection_uses_effective_recurrence_and_exception_options()
  {
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var profile = Profile(Guid.NewGuid(), "Exception Runner");
    await new ProfileStore(_factory).CreateAsync(profile, now, Op("profile.create", now));
    var workoutStore = new WorkoutStore(_factory);
    var baseRevision = await workoutStore.CreateAsync(
      Guid.NewGuid(), Definition("Base", 7), now, Op("workout.create", now));
    var replaceRevision = await workoutStore.CreateAsync(
      Guid.NewGuid(), Definition("Replace", 8), now, Op("workout.create", now));
    var addRevision = await workoutStore.CreateAsync(
      Guid.NewGuid(), Definition("Add", 9), now, Op("workout.create", now));
    var skipDate = new DateOnly(2026, 8, 4);
    var offDayReplaceDate = new DateOnly(2026, 8, 5);
    var replaceDate = new DateOnly(2026, 8, 11);
    var addDate = new DateOnly(2026, 8, 18);
    var series = new CalendarSeriesDefinition(
      Guid.NewGuid(), profile.Id, "Exceptions", "Europe/Brussels",
      new WeeklyRecurrence(new DateOnly(2026, 8, 1), null, 1, WeekdayFlags.Tuesday),
      [new WorkoutAlternative(baseRevision.Id, 0)],
      [
        new CalendarExceptionDefinition(skipDate, CalendarExceptionKind.Skip, []),
        new CalendarExceptionDefinition(offDayReplaceDate, CalendarExceptionKind.Replace,
          [new WorkoutAlternative(replaceRevision.Id, 0)]),
        new CalendarExceptionDefinition(replaceDate, CalendarExceptionKind.Replace,
          [new WorkoutAlternative(replaceRevision.Id, 0)]),
        new CalendarExceptionDefinition(addDate, CalendarExceptionKind.Add,
          [new WorkoutAlternative(addRevision.Id, 1)]),
      ]);
    var store = new CalendarStore(_factory);
    await store.CreateAsync(series, now, Op("calendar.create", now));

    Task Save(DateOnly date, Guid revisionId) => store.SaveSelectionAsync(
      new StoredTrainingDaySelection(profile.Id, date, series.Id, revisionId, now),
      Op("calendar.select", now));

    await Assert.ThrowsAsync<InvalidOperationException>(() => Save(new DateOnly(2026, 8, 6), baseRevision.Id));
    await Assert.ThrowsAsync<InvalidOperationException>(() => Save(offDayReplaceDate, replaceRevision.Id));
    await Assert.ThrowsAsync<InvalidOperationException>(() => Save(skipDate, baseRevision.Id));
    await Assert.ThrowsAsync<InvalidOperationException>(() => Save(replaceDate, baseRevision.Id));
    await Save(replaceDate, replaceRevision.Id);
    await Save(addDate, baseRevision.Id);
    await Save(addDate, addRevision.Id);

    var selected = await store.FindSelectionAsync(profile.Id, addDate);
    Assert.Equal(addRevision.Id, selected!.WorkoutRevisionId);

    await using var context = await _factory.CreateDbContextAsync();
    await context.Database.ExecuteSqlInterpolatedAsync(
      $"UPDATE CalendarExceptions SET Kind = {"99"} WHERE CalendarSeriesId = {series.Id}");
    await Assert.ThrowsAsync<InvalidOperationException>(() => store.FindAsync(series.Id));
  }

  private static UserProfile Profile(Guid id, string name) => new(
    id, name, UnitSystem.Metric, 72.5, null, null,
    [new HeartRateZone(1, "Easy", 90, 115)],
    new HeartRateControllerSettings(0.3, 45, 0.7, 20));

  private static WorkoutDefinition Definition(string title, double speed) => new(
    1, title, null,
    [new WorkoutStep(new TimeGoal(TimeSpan.FromMinutes(20)), new FixedSpeed(speed), new FixedIncline(1))]);

  private static PersistenceWriteOperation Op(string type, DateTimeOffset now) => new(
    Guid.NewGuid(), type, 200, "{}", now, new string('0', 64));
}
