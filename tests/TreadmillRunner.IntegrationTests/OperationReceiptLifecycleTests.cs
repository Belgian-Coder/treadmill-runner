using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class OperationReceiptLifecycleTests : IAsyncLifetime
{
  private readonly string directory = Path.Combine(Path.GetTempPath(), "TreadmillRunner.Tests", Guid.NewGuid().ToString("N"));
  private IDbContextFactory<TreadmillRunnerDbContext> factory = null!;

  public async Task InitializeAsync()
  {
    Directory.CreateDirectory(directory);
    factory = TreadmillRunnerDatabase.CreateFactory(Path.Combine(directory, "receipts.db"));
    await using TreadmillRunnerDbContext context = await factory.CreateDbContextAsync();
    await context.Database.MigrateAsync();
  }

  public Task DisposeAsync()
  {
    SqliteConnection.ClearAllPools();
    if (Directory.Exists(directory)) Directory.Delete(directory, recursive: true);
    return Task.CompletedTask;
  }

  [Fact]
  public async Task Terminal_receipt_replays_exactly_and_same_client_operation_cannot_be_admitted_twice()
  {
    var store = new OperationReceiptStore(factory);
    Guid clientOperationId = Guid.NewGuid();
    var receipt = new OperationReceipt(
      Guid.NewGuid(),
      clientOperationId,
      "session.stop",
      200,
      "{\"state\":\"Stopped\"}",
      DateTimeOffset.Parse("2026-08-23T12:00:00Z"),
      new string('a', 64));

    Assert.True(await store.TryAddAsync(receipt));
    Assert.False(await store.TryAddAsync(receipt with { Id = Guid.NewGuid() }));

    OperationReceipt? replay = await store.FindAsync(clientOperationId);
    Assert.NotNull(replay);
    Assert.Equal(receipt.OperationType, replay.OperationType);
    Assert.Equal(receipt.StatusCode, replay.StatusCode);
    Assert.Equal(receipt.OutcomeJson, replay.OutcomeJson);
    Assert.Equal(receipt.RequestFingerprint, replay.RequestFingerprint);
  }

  [Fact]
  public async Task Receipt_retention_prunes_only_entries_older_than_the_ninety_day_replay_window()
  {
    var store = new OperationReceiptStore(factory);
    DateTimeOffset now = DateTimeOffset.Parse("2026-08-23T12:00:00Z");
    var expired = new OperationReceipt(
      Guid.NewGuid(), Guid.NewGuid(), "session.stop", 200, "{}", now.AddDays(-91), new string('b', 64));
    var retained = new OperationReceipt(
      Guid.NewGuid(), Guid.NewGuid(), "session.stop", 200, "{}", now.AddDays(-89), new string('c', 64));

    Assert.True(await store.TryAddAsync(expired));
    Assert.True(await store.TryAddAsync(retained));
    int removed = await store.PruneAsync(now.Subtract(TimeSpan.FromDays(90)));

    Assert.Equal(1, removed);
    Assert.Null(await store.FindAsync(expired.ClientOperationId));
    Assert.NotNull(await store.FindAsync(retained.ClientOperationId));
  }
}
