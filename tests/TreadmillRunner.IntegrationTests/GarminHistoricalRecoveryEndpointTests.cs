using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class GarminHistoricalRecoveryEndpointTests(GarminHistoricalRecoveryGatewayFactory factory)
  : IClassFixture<GarminHistoricalRecoveryGatewayFactory>
{
  [Fact]
  public async Task Status_is_available_only_with_an_exact_retained_fit_pair_and_enabled_connected_account()
  {
    using RecoveryHarness harness = await RecoveryHarness.CreateAsync(factory, createBackups: false);

    JsonElement withoutBackups = await harness.GetStatusAsync();
    Assert.False(withoutBackups.GetProperty("available").GetBoolean());
    Assert.False(withoutBackups.GetProperty("canMergeIntoOne").GetBoolean());
    Assert.Contains("FIT", withoutBackups.GetProperty("message").GetString(), StringComparison.OrdinalIgnoreCase);

    harness.CreateBackups();
    JsonElement withBackups = await harness.GetStatusAsync();
    Assert.True(withBackups.GetProperty("available").GetBoolean());
    Assert.False(withBackups.GetProperty("busy").GetBoolean());
    Assert.True(withBackups.GetProperty("canMergeIntoOne").GetBoolean());
    Assert.True(withBackups.GetProperty("canUndoMerge").GetBoolean());

    await harness.DisableAccountAsync();
    JsonElement disabled = await harness.GetStatusAsync();
    Assert.False(disabled.GetProperty("available").GetBoolean());
    Assert.Contains("Reconnect", disabled.GetProperty("message").GetString(), StringComparison.OrdinalIgnoreCase);
  }

  [Fact]
  public async Task Post_requires_exact_confirmation_queues_both_guarded_actions_and_replays_idempotently()
  {
    using RecoveryHarness merge = await RecoveryHarness.CreateAsync(factory, createBackups: true);

    using HttpResponseMessage invalid = await merge.PostAsync(
      new { operationId = Guid.NewGuid(), action = "MergeIntoOne", confirmation = "merge into one" });
    Assert.Equal(HttpStatusCode.BadRequest, invalid.StatusCode);
    GarminActivityUploadJob unchanged = await merge.GetJobAsync();
    Assert.Equal("Confirmed", unchanged.Status);
    Assert.Equal("WatchSearch", unchanged.OperationPhase);

    Guid mergeOperation = Guid.NewGuid();
    using HttpResponseMessage queued = await merge.PostAsync(
      new { operationId = mergeOperation, action = "MergeIntoOne", confirmation = "MERGE INTO ONE" });
    Assert.Equal(HttpStatusCode.Accepted, queued.StatusCode);
    JsonElement queuedBody = await queued.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(merge.Job.Id, queuedBody.GetProperty("id").GetGuid());
    Assert.Equal("Pending", queuedBody.GetProperty("status").GetString());
    Assert.Equal("EnsureReplacement", queuedBody.GetProperty("operationPhase").GetString());

    GarminActivityUploadJob afterQueue = await merge.GetJobAsync();
    Assert.Equal("Pending", afterQueue.Status);
    Assert.Equal("EnsureReplacement", afterQueue.OperationPhase);

    merge.DeleteBackups();
    using HttpResponseMessage replay = await merge.PostAsync(
      new { operationId = mergeOperation, action = "MergeIntoOne", confirmation = "MERGE INTO ONE" });
    Assert.Equal(HttpStatusCode.Accepted, replay.StatusCode);
    JsonElement replayBody = await replay.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(merge.Job.Id, replayBody.GetProperty("id").GetGuid());
    Assert.Equal("EnsureReplacement", replayBody.GetProperty("operationPhase").GetString());
    GarminActivityUploadJob afterReplay = await merge.GetJobAsync();
    Assert.Equal(afterQueue.Status, afterReplay.Status);
    Assert.Equal(afterQueue.OperationPhase, afterReplay.OperationPhase);
    Assert.Equal(afterQueue.UpdatedAtUtc, afterReplay.UpdatedAtUtc);

    using RecoveryHarness undo = await RecoveryHarness.CreateAsync(factory, createBackups: true);
    Guid undoOperation = Guid.NewGuid();
    using HttpResponseMessage undoResponse = await undo.PostAsync(
      new { operationId = undoOperation, action = "UndoMerge", confirmation = "UNDO GARMIN MERGE" });
    Assert.Equal(HttpStatusCode.Accepted, undoResponse.StatusCode);
    JsonElement undoBody = await undoResponse.Content.ReadFromJsonAsync<JsonElement>();
    Assert.Equal(undo.Job.Id, undoBody.GetProperty("id").GetGuid());
    Assert.Equal("ResolveOriginal", undoBody.GetProperty("operationPhase").GetString());
  }

  [Fact]
  public async Task Concurrent_posts_with_the_same_operation_id_replay_one_queued_recovery()
  {
    using RecoveryHarness harness = await RecoveryHarness.CreateAsync(factory, createBackups: true);
    Guid operationId = Guid.NewGuid();
    var request = new
    {
      operationId,
      action = "MergeIntoOne",
      confirmation = "MERGE INTO ONE",
    };

    // Start both HTTP requests before awaiting either response.  This covers
    // the race where both handlers pass their read-only receipt pre-check.
    Task<HttpResponseMessage> firstTask = harness.PostAsync(request);
    Task<HttpResponseMessage> secondTask = harness.PostAsync(request);
    HttpResponseMessage[] responses = await Task.WhenAll(firstTask, secondTask);
    try
    {
      Assert.All(responses, response => Assert.Equal(HttpStatusCode.Accepted, response.StatusCode));
      JsonElement[] bodies = await Task.WhenAll(responses.Select(static response => response.Content.ReadFromJsonAsync<JsonElement>()));
      Assert.All(bodies, body =>
      {
        Assert.Equal(harness.Job.Id, body.GetProperty("id").GetGuid());
        Assert.Equal("Pending", body.GetProperty("status").GetString());
        Assert.Equal("EnsureReplacement", body.GetProperty("operationPhase").GetString());
      });
      Assert.Equal(
        bodies[0].GetProperty("updatedAtUtc").GetDateTimeOffset(),
        bodies[1].GetProperty("updatedAtUtc").GetDateTimeOffset());

      GarminActivityUploadJob persisted = await harness.GetJobAsync();
      Assert.Equal("Pending", persisted.Status);
      Assert.Equal("EnsureReplacement", persisted.OperationPhase);
      Assert.Equal(1, await factory.CountReceiptsAsync(operationId));

      using HttpResponseMessage conflictingReplay = await harness.PostAsync(new
      {
        operationId,
        action = "UndoMerge",
        confirmation = "UNDO GARMIN MERGE",
      });
      Assert.Equal(HttpStatusCode.Conflict, conflictingReplay.StatusCode);
      GarminActivityUploadJob unchanged = await harness.GetJobAsync();
      Assert.Equal(persisted.UpdatedAtUtc, unchanged.UpdatedAtUtc);
      Assert.Equal(1, await factory.CountReceiptsAsync(operationId));
    }
    finally
    {
      foreach (HttpResponseMessage response in responses) response.Dispose();
    }
  }

  [Fact]
  public async Task Busy_disabled_and_missing_backup_requests_reject_without_mutating_the_job()
  {
    using RecoveryHarness busy = await RecoveryHarness.CreateAsync(factory, createBackups: true);
    using HttpResponseMessage first = await busy.PostAsync(
      new { operationId = Guid.NewGuid(), action = "MergeIntoOne", confirmation = "MERGE INTO ONE" });
    Assert.Equal(HttpStatusCode.Accepted, first.StatusCode);
    GarminActivityUploadJob busyBefore = await busy.GetJobAsync();

    using HttpResponseMessage second = await busy.PostAsync(
      new { operationId = Guid.NewGuid(), action = "UndoMerge", confirmation = "UNDO GARMIN MERGE" });
    Assert.Equal(HttpStatusCode.Conflict, second.StatusCode);
    GarminActivityUploadJob busyAfter = await busy.GetJobAsync();
    Assert.Equal(busyBefore.Status, busyAfter.Status);
    Assert.Equal(busyBefore.OperationPhase, busyAfter.OperationPhase);
    Assert.Equal(busyBefore.UpdatedAtUtc, busyAfter.UpdatedAtUtc);

    using RecoveryHarness disabled = await RecoveryHarness.CreateAsync(factory, createBackups: true);
    await disabled.DisableAccountAsync();
    GarminActivityUploadJob disabledBefore = await disabled.GetJobAsync();
    using HttpResponseMessage disabledResponse = await disabled.PostAsync(
      new { operationId = Guid.NewGuid(), action = "MergeIntoOne", confirmation = "MERGE INTO ONE" });
    Assert.Equal(HttpStatusCode.Conflict, disabledResponse.StatusCode);
    GarminActivityUploadJob disabledAfter = await disabled.GetJobAsync();
    Assert.Equal(disabledBefore.Status, disabledAfter.Status);
    Assert.Equal(disabledBefore.OperationPhase, disabledAfter.OperationPhase);
    Assert.Equal(disabledBefore.UpdatedAtUtc, disabledAfter.UpdatedAtUtc);

    using RecoveryHarness missing = await RecoveryHarness.CreateAsync(factory, createBackups: false);
    GarminActivityUploadJob missingBefore = await missing.GetJobAsync();
    using HttpResponseMessage missingResponse = await missing.PostAsync(
      new { operationId = Guid.NewGuid(), action = "UndoMerge", confirmation = "UNDO GARMIN MERGE" });
    Assert.Equal(HttpStatusCode.Conflict, missingResponse.StatusCode);
    GarminActivityUploadJob missingAfter = await missing.GetJobAsync();
    Assert.Equal(missingBefore.Status, missingAfter.Status);
    Assert.Equal(missingBefore.OperationPhase, missingAfter.OperationPhase);
    Assert.Equal(missingBefore.UpdatedAtUtc, missingAfter.UpdatedAtUtc);
  }

  private sealed class RecoveryHarness : IDisposable
  {
    private readonly GarminHistoricalRecoveryGatewayFactory application;
    private readonly HttpClient client;

    private RecoveryHarness(
      GarminHistoricalRecoveryGatewayFactory application,
      HttpClient client,
      Guid profileId,
      Guid sessionId,
      GarminActivityUploadJob job)
    {
      this.application = application;
      this.client = client;
      ProfileId = profileId;
      SessionId = sessionId;
      Job = job;
    }

    public Guid ProfileId { get; }
    public Guid SessionId { get; }
    public GarminActivityUploadJob Job { get; }

    public static async Task<RecoveryHarness> CreateAsync(
      GarminHistoricalRecoveryGatewayFactory factory,
      bool createBackups)
    {
      Guid profileId = Guid.NewGuid();
      Guid sessionId = Guid.NewGuid();
      DateTimeOffset now = DateTimeOffset.Parse("2026-08-29T08:00:00Z");
      HttpClient client = factory.CreateClient(new WebApplicationFactoryClientOptions
      {
        AllowAutoRedirect = false,
        BaseAddress = new Uri("https://localhost"),
      });
      await factory.Services.GetRequiredService<GarminActivityUploadWorker>().StopAsync(CancellationToken.None);
      await factory.SeedSessionAsync(profileId, sessionId, now);

      IGarminActivityUploadStore uploads = factory.Services.GetRequiredService<IGarminActivityUploadStore>();
      await uploads.ConnectAsync(
        profileId,
        "historical-recovery-test",
        "protected-token-store",
        enabled: true,
        watchActivityHandling: GarminWatchActivityHandling.MergeAndReplace,
        nowUtc: now.AddHours(-1));
      Assert.True(await uploads.ReconcileCompletedSessionsAsync(now) > 0);
      GarminActivityUploadJob job = Assert.IsType<GarminActivityUploadJob>(await uploads.FindBySessionAsync(sessionId));
      GarminActivityUploadJob leased = Assert.IsType<GarminActivityUploadJob>(
        await uploads.LeaseNextAsync(now, TimeSpan.FromMinutes(2)));
      Assert.Equal(job.Id, leased.Id);
      await uploads.MarkConfirmedAsync(job.Id, remoteId: null, "protected-token-store", now.AddMinutes(1));
      job = Assert.IsType<GarminActivityUploadJob>(await uploads.FindBySessionAsync(sessionId));

      var harness = new RecoveryHarness(factory, client, profileId, sessionId, job);
      if (createBackups) harness.CreateBackups();
      return harness;
    }

    public void CreateBackups()
    {
      File.WriteAllBytes(OriginalBackupPath, [1, 2, 3, 4]);
      File.WriteAllBytes(ReplacementBackupPath, [5, 6, 7, 8]);
    }

    public void DeleteBackups()
    {
      File.Delete(OriginalBackupPath);
      File.Delete(ReplacementBackupPath);
    }

    public async Task<JsonElement> GetStatusAsync() => await client.GetFromJsonAsync<JsonElement>(
      $"/api/integrations/garmin/activity-upload/profiles/{ProfileId}/sessions/{SessionId}/historical-recovery");

    public async Task<HttpResponseMessage> PostAsync(object request) => await client.PostAsJsonAsync(
      $"/api/integrations/garmin/activity-upload/profiles/{ProfileId}/sessions/{SessionId}/historical-recovery", request);

    public async Task<GarminActivityUploadJob> GetJobAsync()
    {
      IGarminActivityUploadStore uploads = application.Services.GetRequiredService<IGarminActivityUploadStore>();
      return Assert.IsType<GarminActivityUploadJob>(await uploads.FindBySessionAsync(SessionId));
    }

    public async Task DisableAccountAsync()
    {
      IGarminActivityUploadStore uploads = application.Services.GetRequiredService<IGarminActivityUploadStore>();
      GarminActivityUploadAccount account = Assert.IsType<GarminActivityUploadAccount>(await uploads.FindAccountAsync(ProfileId));
      await uploads.SetSettingsAsync(
        ProfileId,
        enabled: false,
        GarminWatchActivityHandling.MergeAndReplace,
        account.Version,
        DateTimeOffset.UtcNow);
    }

    public void Dispose()
    {
      client.Dispose();
      File.Delete(OriginalBackupPath);
      File.Delete(ReplacementBackupPath);
    }

    private string OriginalBackupPath => Path.Combine(
      application.BackupRoot,
      $"watch-original_{Job.Id:N}_original.fit");

    private string ReplacementBackupPath => Path.Combine(
      application.BackupRoot,
      $"watch-original_{Job.Id:N}_replacement.fit");
  }
}

public sealed class GarminHistoricalRecoveryGatewayFactory : WebApplicationFactory<TreadmillRunner.Gateway.Program>
{
  private readonly string directory = Path.Combine(
    Path.GetTempPath(),
    "TreadmillRunner.Tests",
    $"garmin-historical-recovery-{Guid.NewGuid():N}");

  private string DatabasePath => Path.Combine(directory, "gateway.db");

  public string BackupRoot => Path.Combine(directory, "backups");

  protected override void ConfigureWebHost(IWebHostBuilder builder)
  {
    Directory.CreateDirectory(directory);
    Directory.CreateDirectory(BackupRoot);
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    using TreadmillRunnerDbContext database = factory.CreateDbContext();
    database.Database.Migrate();

    builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(
      new Dictionary<string, string?>
      {
        ["GarminActivityUpload:BackupRoot"] = BackupRoot,
        ["Persistence:DatabasePath"] = DatabasePath,
        ["Persistence:DataProtectionKeyPath"] = Path.Combine(directory, "keys"),
      }));
  }

  public async Task SeedSessionAsync(Guid profileId, Guid sessionId, DateTimeOffset now)
  {
    Guid workoutId = Guid.NewGuid();
    Guid revisionId = Guid.NewGuid();
    string profileName = $"Historical recovery {profileId:N}";
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    context.UserProfiles.Add(new UserProfileEntity
    {
      Id = profileId,
      DisplayName = profileName,
      NormalizedDisplayName = profileName.ToUpperInvariant(),
      UnitSystem = "Metric",
      WeightKilograms = 70,
      Version = 1,
      CreatedAtUtc = now,
      UpdatedAtUtc = now,
    });
    context.Workouts.Add(new WorkoutEntity { Id = workoutId, Name = "Historical recovery", Kind = "Structured", CreatedAtUtc = now });
    context.WorkoutRevisions.Add(new WorkoutRevisionEntity
    {
      Id = revisionId,
      WorkoutId = workoutId,
      RevisionNumber = 1,
      DefinitionJson = "{}",
      ContentSha256 = new string('c', 64),
      CreatedAtUtc = now,
    });
    context.WorkoutSessions.Add(new WorkoutSessionEntity
    {
      Id = sessionId,
      UserProfileId = profileId,
      UserProfileName = profileName,
      WorkoutRevisionId = revisionId,
      WorkoutTitle = "Historical recovery",
      State = nameof(SessionState.Completed),
      SessionOrigin = nameof(SessionOrigin.Legacy),
      ArmedAtUtc = now.AddMinutes(-31),
      StartedAtUtc = now.AddMinutes(-30),
      EndedAtUtc = now.AddMinutes(-10),
      DurationSeconds = 1200,
      DistanceKilometers = 2.5,
      MetricAlgorithmVersion = "v1",
      ControllerConfigurationJson = "{}",
    });
    await context.SaveChangesAsync();
  }

  public async Task<int> CountReceiptsAsync(Guid operationId)
  {
    IDbContextFactory<TreadmillRunnerDbContext> factory = TreadmillRunnerDatabase.CreateFactory(DatabasePath);
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    return await context.OperationReceipts.CountAsync(receipt => receipt.ClientOperationId == operationId);
  }

  protected override void Dispose(bool disposing)
  {
    base.Dispose(disposing);
    Microsoft.Data.Sqlite.SqliteConnection.ClearAllPools();
    if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
  }
}
