using TreadmillRunner.Gateway.Live;

namespace TreadmillRunner.IntegrationTests;

public sealed class LiveEffectBatchTests
{
  [Fact]
  public async Task Terminal_effect_runs_after_current_session_guard_rejects_replaced_session()
  {
    var batch = new LiveEffectBatch();
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var applied = false;
    batch.Add(metadata, _ =>
    {
      applied = true;
      return Task.CompletedTask;
    }, terminal: true);

    await batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None);

    Assert.True(applied);
  }

  [Fact]
  public async Task Nonterminal_effect_is_skipped_when_session_generation_is_stale()
  {
    var batch = new LiveEffectBatch();
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var applied = false;
    batch.Add(metadata, _ =>
    {
      applied = true;
      return Task.CompletedTask;
    });

    await batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None);

    Assert.False(applied);
  }

  [Fact]
  public async Task Terminal_effect_retries_a_transient_failure_without_repeating_the_appended_event()
  {
    var batch = new LiveEffectBatch();
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var eventAppended = false;
    var finalized = false;
    var appendCount = 0;
    var finalizeCount = 0;
    batch.Add(metadata, _ =>
    {
      if (!eventAppended)
      {
        eventAppended = true;
        appendCount++;
      }

      finalizeCount++;
      if (finalizeCount == 1) return Task.FromException(new IOException("transient"));
      finalized = true;
      return Task.CompletedTask;
    }, terminal: true);

    await batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None);

    Assert.Equal(1, appendCount);
    Assert.Equal(2, finalizeCount);
    Assert.True(finalized);
  }

  [Fact]
  public async Task Terminal_effect_keeps_retrying_persistent_storage_until_shutdown_cancellation()
  {
    using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(550));
    var batch = new LiveEffectBatch();
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var attempts = 0;
    batch.Add(metadata, _ =>
    {
      attempts++;
      return Task.FromException(new IOException("persistent"));
    }, terminal: true);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
      batch.ExecuteAsync((_, _) => Task.FromResult(false), cancellation.Token));

    Assert.InRange(attempts, 2, 6);
  }

  [Fact]
  public async Task Terminal_effect_retry_honors_shutdown_cancellation()
  {
    using var cancellation = new CancellationTokenSource();
    var batch = new LiveEffectBatch();
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var attempts = 0;
    batch.Add(metadata, _ =>
    {
      attempts++;
      cancellation.Cancel();
      return Task.FromException(new IOException("shutdown"));
    }, terminal: true);

    await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
      batch.ExecuteAsync((_, _) => Task.FromResult(false), cancellation.Token));

    Assert.Equal(1, attempts);
  }

  [Fact]
  public async Task Terminal_effect_stops_retrying_after_the_bounded_budget()
  {
    var batch = new LiveEffectBatch(TimeSpan.FromMilliseconds(250));
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var attempts = 0;
    batch.Add(metadata, _ =>
    {
      attempts++;
      return Task.FromException(new IOException("persistent"));
    }, terminal: true);

    IOException exception = await Assert.ThrowsAsync<IOException>(() =>
      batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None));

    Assert.Equal("persistent", exception.Message);
    Assert.InRange(attempts, 2, 5);
  }

  [Fact]
  public async Task Terminal_retry_budget_starts_after_a_slow_first_attempt_fails()
  {
    var batch = new LiveEffectBatch(TimeSpan.FromMilliseconds(250));
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var attempts = 0;
    batch.Add(metadata, async _ =>
    {
      attempts++;
      if (attempts == 1)
      {
        await Task.Delay(350);
        throw new IOException("first attempt failed after a slow prerequisite");
      }
    }, terminal: true);

    await batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None);

    Assert.Equal(2, attempts);
  }

  [Fact]
  public async Task Terminal_internal_timeout_is_retryable_when_shutdown_was_not_requested()
  {
    var batch = new LiveEffectBatch(TimeSpan.FromMilliseconds(250));
    var metadata = new LiveEffectMetadata(Guid.NewGuid(), 4, 7, Guid.NewGuid());
    var attempts = 0;
    batch.Add(metadata, _ =>
    {
      attempts++;
      return attempts == 1
        ? Task.FromCanceled(new CancellationToken(canceled: true))
        : Task.CompletedTask;
    }, terminal: true);

    await batch.ExecuteAsync((_, _) => Task.FromResult(false), CancellationToken.None);

    Assert.Equal(2, attempts);
  }
}
