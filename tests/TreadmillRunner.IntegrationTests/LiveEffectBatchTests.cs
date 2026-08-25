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
}
