using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Control;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class SessionStoreTests : IAsyncLifetime
{
  private readonly string _directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    Guid.NewGuid().ToString("N"));

  private string DatabasePath => Path.Combine(_directory, "sessions.db");

  public Task InitializeAsync()
  {
    Directory.CreateDirectory(_directory);
    return Task.CompletedTask;
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(_directory))
    {
      Directory.Delete(_directory, recursive: true);
    }

    return Task.CompletedTask;
  }

  [Fact]
  public async Task Session_store_round_trips_samples_events_summary_and_debrief()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var armedAt = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var sessionId = Guid.NewGuid();

    await store.CreateAsync(new NewWorkoutSession(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      armedAt,
      "{\"heartRateController\":\"disabled\"}",
      SessionMetricAlgorithms.EstimatedCaloriesV1));
    await store.MarkRunningAsync(sessionId, armedAt.AddSeconds(3));
    await store.AppendSampleAsync(Sample(sessionId, 0, armedAt.AddSeconds(3), 0, 6.5, 6.4, 118));
    await store.AppendSampleAsync(Sample(sessionId, 1, armedAt.AddSeconds(4), 1, 6.5, 6.6, 120));
    await store.AppendEventAsync(sessionId, new SessionWarningEvent(
      "simulator-note",
      "Runner changed pace from the console.",
      armedAt.AddSeconds(4)));
    await store.FinalizeAsync(new SessionSummary(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      armedAt.AddSeconds(3),
      armedAt.AddMinutes(20),
      TimeSpan.FromMinutes(19).Add(TimeSpan.FromSeconds(57)),
      2.2,
      143,
      129,
      151,
      6.6,
      1.0));
    await store.SaveDebriefAsync(new SessionDebrief(
      sessionId,
      5,
      " Comfortable finish. ",
      armedAt.AddMinutes(21)));

    StoredWorkoutSession stored = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(sessionId));
    Assert.Equal(SessionState.Completed, stored.State);
    Assert.Equal(2, stored.Samples.Count);
    Assert.Equal(1, stored.Samples[1].Sequence);
    Assert.Equal(6.6, stored.Samples[1].MeasuredSpeedKph);
    Assert.IsType<SessionWarningEvent>(Assert.Single(stored.Events));
    Assert.Equal(5, stored.Debrief?.PerceivedExertion);
    Assert.Equal("Comfortable finish.", stored.Debrief?.Note);

    SessionSummary summary = Assert.Single(await store.ListSummariesAsync(ids.ProfileId));
    Assert.Equal(sessionId, summary.SessionId);
    Assert.Equal("Runner", summary.UserProfileName);
    Assert.Equal("Easy Run", summary.WorkoutTitle);
    Assert.Equal(2.2, summary.DistanceKilometers);
  }

  [Fact]
  public async Task Startup_interruption_marks_only_unfinished_sessions_and_records_event()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var activeId = Guid.NewGuid();
    var completedId = Guid.NewGuid();

    await store.CreateAsync(New(activeId, ids, now));
    await store.MarkRunningAsync(activeId, now.AddSeconds(3));
    await store.CreateAsync(New(completedId, ids, now.AddMinutes(-30)));
    await store.MarkRunningAsync(completedId, now.AddMinutes(-30).AddSeconds(3));
    await store.FinalizeAsync(new SessionSummary(
      completedId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      now.AddMinutes(-30).AddSeconds(3),
      now.AddMinutes(-10),
      TimeSpan.FromMinutes(19),
      2.1,
      135,
      126,
      146,
      6.5,
      1));

    int interrupted = await store.InterruptUnfinishedAsync(now, "Gateway restarted.");

    Assert.Equal(1, interrupted);
    StoredWorkoutSession active = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(activeId));
    Assert.Equal(SessionState.Interrupted, active.State);
    Assert.Equal(now, active.EndedAt);
    SessionInterruptedEvent interruption = Assert.IsType<SessionInterruptedEvent>(Assert.Single(active.Events));
    Assert.Equal("Gateway restarted.", interruption.Reason);
    Assert.Equal(SessionState.Completed, (await store.FindAsync(completedId))?.State);
  }

  [Fact]
  public async Task Hardware_session_recovery_checkpoint_round_trips_without_marking_the_session_terminal()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    SeedIds ids = await ReadSeedIdsAsync(factory);
    DateTimeOffset started = DateTimeOffset.Parse("2026-08-06T10:00:00Z");
    Guid sessionId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(
      sessionId, ids.ProfileId, "Runner", ids.RevisionId, "Recovery run", started.AddSeconds(-2),
      "{}", SessionMetricAlgorithms.EstimatedCaloriesV1, origin: SessionOrigin.Hardware));
    await store.MarkRunningAsync(sessionId, started);
    var progression = new WorkoutProgressionCheckpoint(2, TimeSpan.FromMinutes(3), .4,
      TimeSpan.FromMinutes(2), .25);
    var checkpoint = new SessionRecoveryCheckpoint(
      sessionId, started.AddMinutes(3), SessionState.Running, 8, started, progression,
      .4, 7.2, 1.5, 7.5, 1.5, HeartRateAutomationMode.Full, 12);

    await store.SaveRecoveryCheckpointAsync(checkpoint);
    RecoverableWorkoutSession recovered = Assert.IsType<RecoverableWorkoutSession>(await store.FindRecoverableAsync());

    Assert.Equal(SessionState.Running, recovered.Session.State);
    Assert.Equal(checkpoint, recovered.Checkpoint);
  }

  [Fact]
  public async Task System_tests_are_hidden_by_default_and_delete_transactionally_with_replay_receipt()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    Guid sessionId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Garmin upload verification", now, "{\"mode\":\"GarminUploadTest\"}",
      SessionMetricAlgorithms.EstimatedCaloriesV1, origin: SessionOrigin.SystemTest));
    await store.MarkRunningAsync(sessionId, now.AddSeconds(1));
    await store.AppendSampleAsync(Sample(sessionId, 0, now.AddSeconds(1), 0, 6, 6, 120));
    await store.FinalizeAsync(new SessionSummary(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Garmin upload verification", SessionState.Completed, now.AddSeconds(1), now.AddMinutes(1),
      TimeSpan.FromSeconds(59), .1, 8, 120, 120, 6, 0, origin: SessionOrigin.SystemTest));

    Assert.Empty(await store.ListSummariesAsync(ids.ProfileId));
    Assert.Equal(SessionOrigin.SystemTest, Assert.Single(await store.ListSummariesAsync(ids.ProfileId, includeSystemTests: true)).Origin);
    HistoryDeletionPreview preview = Assert.IsType<HistoryDeletionPreview>(await store.PreviewDeletionAsync(sessionId, ids.ProfileId));
    Assert.True(preview.CanDelete);
    Guid operationId = Guid.NewGuid();
    string fingerprint = new string('d', 64);
    HistoryDeletionResult deleted = await store.DeleteAsync(new DeleteHistorySessionOperation(
      operationId, sessionId, ids.ProfileId, preview.Revision, fingerprint, now.AddMinutes(2)));
    Assert.True(deleted.Deleted);
    Assert.Equal(1, deleted.DeletedSampleCount);
    Assert.Null(await store.FindAsync(sessionId));
    OperationReplayException replay = await Assert.ThrowsAsync<OperationReplayException>(() => store.DeleteAsync(
      new DeleteHistorySessionOperation(operationId, sessionId, ids.ProfileId, preview.Revision, fingerprint, now.AddMinutes(2))));
    Assert.Contains("history.delete", replay.Receipt.OperationType, StringComparison.Ordinal);
  }

  [Theory]
  [InlineData("Pending", false)]
  [InlineData("InFlight", false)]
  [InlineData("Unknown", false)]
  [InlineData("Confirmed", true)]
  [InlineData("FoundInGarmin", true)]
  [InlineData("Dismissed", true)]
  [InlineData("Failed", true)]
  public async Task System_test_deletion_obeys_every_Garmin_job_terminal_rule(string status, bool expectedCanDelete)
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    SeedIds ids = await ReadSeedIdsAsync(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    Guid sessionId = await CreateTerminalSessionAsync(store, ids, now, SessionOrigin.SystemTest);
    Guid accountId = Guid.NewGuid();
    Guid jobId = Guid.NewGuid();
    await using (var context = await factory.CreateDbContextAsync())
    {
      context.GarminActivityUploadAccounts.Add(new GarminActivityUploadAccountEntity
      {
        Id = accountId,
        UserProfileId = ids.ProfileId,
        AccountLabel = "Test",
        ProtectedTokenStore = "protected",
        Enabled = true,
        State = "Connected",
        ConnectedAtUtc = now,
        UpdatedAtUtc = now,
        Version = 1,
      });
      context.GarminActivityUploadJobs.Add(new GarminActivityUploadJobEntity
      {
        Id = jobId,
        UserProfileId = ids.ProfileId,
        GarminActivityUploadAccountId = accountId,
        WorkoutSessionId = sessionId,
        IdempotencyKey = new string('f', 64),
        Status = status,
        AvailableAtUtc = now,
        CreatedAtUtc = now,
        UpdatedAtUtc = now,
        AcknowledgedAtUtc = status == "FoundInGarmin" ? now : null,
      });
      await context.SaveChangesAsync();
    }

    HistoryDeletionPreview preview = Assert.IsType<HistoryDeletionPreview>(await store.PreviewDeletionAsync(sessionId, ids.ProfileId));
    Assert.Equal(expectedCanDelete, preview.CanDelete);
    if (!expectedCanDelete) return;
    await store.DeleteAsync(new DeleteHistorySessionOperation(
      Guid.NewGuid(), sessionId, ids.ProfileId, preview.Revision, new string('a', 64), now.AddMinutes(2)));
    await using var assertionContext = await factory.CreateDbContextAsync();
    Assert.False(await assertionContext.GarminActivityUploadJobs.AnyAsync(item => item.Id == jobId));
  }

  [Fact]
  public async Task Deletion_blocks_nonterminal_or_unsettled_Garmin_sessions_but_allows_terminal_plan_history()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    SeedIds ids = await ReadSeedIdsAsync(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");

    Guid nonterminalId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(nonterminalId, ids.ProfileId, "Runner", ids.RevisionId,
      "Active", now, "{}", SessionMetricAlgorithms.EstimatedCaloriesV1, origin: SessionOrigin.Hardware));
    Assert.False(Assert.IsType<HistoryDeletionPreview>(await store.PreviewDeletionAsync(nonterminalId, ids.ProfileId)).CanDelete);

    Guid linkedId = await CreateTerminalSessionAsync(store, ids, now.AddMinutes(2), SessionOrigin.Hardware);
    await using (var context = await factory.CreateDbContextAsync())
    {
      var program = new WorkoutProgramEntity { Id = Guid.NewGuid(), CreatedAtUtc = now };
      var revision = new WorkoutProgramRevisionEntity
      {
        Id = Guid.NewGuid(),
        WorkoutProgramId = program.Id,
        RevisionNumber = 1,
        Name = "Plan",
        Category = "5K",
        ContentSha256 = new string('c', 64),
        CreatedAtUtc = now,
      };
      var item = new WorkoutProgramItemEntity { Id = Guid.NewGuid(), WorkoutProgramRevisionId = revision.Id, WorkoutRevisionId = ids.RevisionId, Position = 1 };
      var run = new WorkoutProgramRunEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = ids.ProfileId,
        WorkoutProgramRevisionId = revision.Id,
        Status = "Active",
        StartedAtUtc = now,
        Version = 1,
      };
      context.AddRange(program, revision, item, run);
      WorkoutSessionEntity linked = await context.WorkoutSessions.SingleAsync(candidate => candidate.Id == linkedId);
      linked.WorkoutProgramRunId = run.Id;
      linked.WorkoutProgramItemId = item.Id;
      await context.SaveChangesAsync();
    }
    HistoryDeletionPreview linkedPreview = Assert.IsType<HistoryDeletionPreview>(
      await store.PreviewDeletionAsync(linkedId, ids.ProfileId));
    Assert.True(linkedPreview.CanDelete);
    Assert.True(linkedPreview.IsProgramLinked);
    await store.DeleteAsync(new DeleteHistorySessionOperation(
      Guid.NewGuid(), linkedId, ids.ProfileId, linkedPreview.Revision, new string('b', 64), now.AddMinutes(3)));
    Assert.Null(await store.FindAsync(linkedId));

    Guid normalId = await CreateTerminalSessionAsync(store, ids, now.AddMinutes(4), SessionOrigin.Hardware);
    HistoryDeletionPreview stalePreview = Assert.IsType<HistoryDeletionPreview>(await store.PreviewDeletionAsync(normalId, ids.ProfileId));
    Assert.True(stalePreview.CanDelete);
    await using (var context = await factory.CreateDbContextAsync())
    {
      Guid accountId = Guid.NewGuid();
      context.GarminActivityUploadAccounts.Add(new GarminActivityUploadAccountEntity
      {
        Id = accountId,
        UserProfileId = ids.ProfileId,
        AccountLabel = "Test",
        ProtectedTokenStore = "protected",
        Enabled = true,
        State = "Connected",
        ConnectedAtUtc = now,
        UpdatedAtUtc = now,
        Version = 1,
      });
      context.GarminActivityUploadJobs.Add(new GarminActivityUploadJobEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = ids.ProfileId,
        GarminActivityUploadAccountId = accountId,
        WorkoutSessionId = normalId,
        IdempotencyKey = new string('e', 64),
        Status = "Confirmed",
        AvailableAtUtc = now,
        CreatedAtUtc = now,
        UpdatedAtUtc = now,
      });
      await context.SaveChangesAsync();
    }
    await Assert.ThrowsAsync<DbUpdateConcurrencyException>(() => store.DeleteAsync(new DeleteHistorySessionOperation(
      Guid.NewGuid(), normalId, ids.ProfileId, stalePreview.Revision, new string('d', 64), now.AddMinutes(6))));
    HistoryDeletionPreview confirmedGarminPreview = Assert.IsType<HistoryDeletionPreview>(
      await store.PreviewDeletionAsync(normalId, ids.ProfileId));
    Assert.True(confirmedGarminPreview.CanDelete);
    Assert.True(confirmedGarminPreview.GarminRemoteActivityMayRemain);
    await store.DeleteAsync(new DeleteHistorySessionOperation(
      Guid.NewGuid(), normalId, ids.ProfileId, confirmedGarminPreview.Revision, new string('e', 64), now.AddMinutes(7)));
    Assert.Null(await store.FindAsync(normalId));
  }

  [Fact]
  public async Task Deleting_historical_hardware_session_adjusts_later_maintenance_baselines()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    SeedIds ids = await ReadSeedIdsAsync(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    Guid sessionId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Incorrect hardware run", now, "{\"mode\":\"hardware:ftms:Ftms\"}",
      SessionMetricAlgorithms.EstimatedCaloriesV1, origin: SessionOrigin.Hardware));
    await store.MarkRunningAsync(sessionId, now.AddSeconds(1));
    await store.FinalizeAsync(new SessionSummary(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Incorrect hardware run", SessionState.Completed, now.AddSeconds(1), now.AddHours(1),
      TimeSpan.FromMinutes(59), 5, 100, null, null, 5, 0, origin: SessionOrigin.Hardware));

    Guid policyId = Guid.NewGuid();
    Guid eventId = Guid.NewGuid();
    await using (var context = await factory.CreateDbContextAsync())
    {
      var enrollment = new DeviceEnrollmentEntity
      {
        Id = Guid.NewGuid(),
        Role = "Treadmill",
        DeviceId = "local",
        ProtocolId = "omega",
        IdentityFingerprint = new string('a', 64),
        DisplayName = "Omega Z",
        TelemetryMode = "Ftms",
        CapabilitiesJson = "{}",
        Evidence = "Verified",
        Version = 1,
        CreatedAtUtc = now,
        UpdatedAtUtc = now,
      };
      context.DeviceEnrollments.Add(enrollment);
      context.TreadmillMaintenancePolicies.Add(new TreadmillMaintenancePolicyEntity
      {
        Id = policyId,
        DeviceEnrollmentId = enrollment.Id,
        IntervalMonths = 3,
        DistanceIntervalKilometers = 241,
        Version = 1,
        CreatedAtUtc = now.AddHours(2),
        UpdatedAtUtc = now.AddHours(2),
      });
      context.TreadmillMaintenanceEvents.Add(new TreadmillMaintenanceEventEntity
      {
        Id = eventId,
        TreadmillMaintenancePolicyId = policyId,
        OperationId = Guid.NewGuid(),
        PerformedAtUtc = now.AddHours(2),
        AppDistanceBaselineKilometers = 5,
        CreatedAtUtc = now.AddHours(2),
      });
      await context.SaveChangesAsync();
    }

    HistoryDeletionPreview preview = Assert.IsType<HistoryDeletionPreview>(await store.PreviewDeletionAsync(sessionId, ids.ProfileId));
    await store.DeleteAsync(new DeleteHistorySessionOperation(
      Guid.NewGuid(), sessionId, ids.ProfileId, preview.Revision, new string('b', 64), now.AddHours(3)));

    await using var assertionContext = await factory.CreateDbContextAsync();
    Assert.Equal(0, (await assertionContext.TreadmillMaintenanceEvents.SingleAsync(item => item.Id == eventId)).AppDistanceBaselineKilometers);
  }

  [Fact]
  [Trait("Category", "Soak")]
  public async Task Four_hour_one_hertz_session_persists_and_reads_all_samples()
  {
    var factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await MigrateAndSeedAsync(factory);
    ISessionStore store = new SessionStore(factory);
    var ids = await ReadSeedIdsAsync(factory);
    var armedAt = DateTimeOffset.Parse("2026-08-02T10:00:00Z");
    var startedAt = armedAt.AddSeconds(3);
    var sessionId = Guid.NewGuid();

    await store.CreateAsync(New(sessionId, ids, armedAt));
    await store.MarkRunningAsync(sessionId, startedAt);
    for (var sequence = 0; sequence < 14_400; sequence++)
    {
      await store.AppendSampleAsync(Sample(
        sessionId,
        sequence,
        startedAt.AddSeconds(sequence),
        sequence,
        requestedSpeed: 8,
        measuredSpeed: 8,
        heartRate: 135));
    }

    await store.FinalizeAsync(new SessionSummary(
      sessionId,
      ids.ProfileId,
      "Runner",
      ids.RevisionId,
      "Easy Run",
      SessionState.Completed,
      startedAt,
      startedAt.AddHours(4),
      TimeSpan.FromHours(4),
      32,
      1_728,
      135,
      135,
      8,
      1));

    StoredWorkoutSession stored = Assert.IsType<StoredWorkoutSession>(await store.FindAsync(sessionId));
    Assert.Equal(14_400, stored.Samples.Count);
    Assert.Equal(0, stored.Samples[0].Sequence);
    Assert.Equal(14_399, stored.Samples[^1].Sequence);
    Assert.Equal(TimeSpan.FromSeconds(14_399), stored.Samples[^1].Elapsed);
  }

  private static NewWorkoutSession New(Guid sessionId, SeedIds ids, DateTimeOffset armedAt) => new(
    sessionId,
    ids.ProfileId,
    "Runner",
    ids.RevisionId,
    "Easy Run",
    armedAt,
    "{}",
    SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static SessionSample Sample(
    Guid sessionId,
    long sequence,
    DateTimeOffset capturedAt,
    double elapsedSeconds,
    double requestedSpeed,
    double measuredSpeed,
    ushort heartRate) => new(
      sessionId,
      sequence,
      capturedAt,
      TimeSpan.FromSeconds(elapsedSeconds),
      plannedSpeedKph: 6.5,
      requestedSpeed,
      measuredSpeed,
      plannedInclinePercent: 1,
      requestedInclinePercent: 1,
      measuredInclinePercent: 1,
      heartRate,
      distanceKilometers: measuredSpeed * elapsedSeconds / 3600,
      estimatedKilocalories: elapsedSeconds * 0.12,
      telemetryAge: TimeSpan.FromMilliseconds(40),
      SessionMetricAlgorithms.EstimatedCaloriesV1);

  private static async Task<Guid> CreateTerminalSessionAsync(
    ISessionStore store,
    SeedIds ids,
    DateTimeOffset now,
    SessionOrigin origin)
  {
    Guid sessionId = Guid.NewGuid();
    await store.CreateAsync(new NewWorkoutSession(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Run", now, "{}", SessionMetricAlgorithms.EstimatedCaloriesV1, origin: origin));
    await store.MarkRunningAsync(sessionId, now.AddSeconds(1));
    await store.FinalizeAsync(new SessionSummary(sessionId, ids.ProfileId, "Runner", ids.RevisionId,
      "Run", SessionState.Completed, now.AddSeconds(1), now.AddMinutes(1), TimeSpan.FromSeconds(59),
      .1, 8, 120, 120, 6, 0, origin: origin));
    return sessionId;
  }

  private static async Task MigrateAndSeedAsync(IDbContextFactory<TreadmillRunnerDbContext> factory)
  {
    await using var context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
    var now = DateTimeOffset.Parse("2026-08-02T08:00:00Z");
    var profile = new UserProfileEntity
    {
      Id = Guid.NewGuid(),
      DisplayName = "Runner",
      NormalizedDisplayName = "RUNNER",
      UnitSystem = "Metric",
      WeightKilograms = 72,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    };
    var workout = new WorkoutEntity
    {
      Id = Guid.NewGuid(),
      Name = "Easy Run",
      CreatedAtUtc = now,
    };
    workout.Revisions.Add(new WorkoutRevisionEntity
    {
      Id = Guid.NewGuid(),
      RevisionNumber = 1,
      DefinitionJson = "{\"blocks\":[],\"schemaVersion\":1,\"title\":\"Easy Run\"}",
      ContentSha256 = new string('e', 64),
      CreatedAtUtc = now,
    });
    context.AddRange(profile, workout);
    await context.SaveChangesAsync();
  }

  private static async Task<SeedIds> ReadSeedIdsAsync(IDbContextFactory<TreadmillRunnerDbContext> factory)
  {
    await using var context = await factory.CreateDbContextAsync();
    return new SeedIds(
      await context.UserProfiles.Select(profile => profile.Id).SingleAsync(),
      await context.WorkoutRevisions.Select(revision => revision.Id).SingleAsync());
  }

  private sealed record SeedIds(Guid ProfileId, Guid RevisionId);
}
