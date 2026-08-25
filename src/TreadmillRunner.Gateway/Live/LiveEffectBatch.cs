using System.Collections.Generic;

namespace TreadmillRunner.Gateway.Live;

internal sealed record LiveEffectMetadata(
  Guid SessionId,
  long SessionVersion,
  long ConnectionGeneration,
  Guid AuthorityId);

internal sealed class LiveEffectBatch
{
  private readonly List<LiveEffect> effects = [];

  public bool IsEmpty => effects.Count == 0;

  public void Add(
    LiveEffectMetadata metadata,
    Func<CancellationToken, Task> apply,
    bool terminal = false)
  {
    ArgumentNullException.ThrowIfNull(apply);
    effects.Add(new LiveEffect(metadata, apply, terminal));
  }

  public bool HasTerminalEffects => effects.Any(static effect => effect.Terminal);

  public async Task ExecuteAsync(
    Func<LiveEffectMetadata, CancellationToken, Task<bool>> isCurrent,
    CancellationToken cancellationToken)
  {
    foreach (LiveEffect effect in effects)
    {
      // Terminal persistence is tied to the session identity, not whichever
      // session happens to be active when its asynchronous effect drains. A
      // new Arm may legitimately replace the in-memory active run before the
      // old terminal summary has committed.
      if (!effect.Terminal && !await isCurrent(effect.Metadata, cancellationToken)) continue;
      await effect.Apply(cancellationToken);
    }
  }

  private sealed record LiveEffect(
    LiveEffectMetadata Metadata,
    Func<CancellationToken, Task> Apply,
    bool Terminal);
}
