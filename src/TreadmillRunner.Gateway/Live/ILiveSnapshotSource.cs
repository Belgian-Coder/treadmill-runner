using TreadmillRunner.Core.Live;

namespace TreadmillRunner.Gateway.Live;

public interface ILiveSnapshotSource
{
  LiveSnapshot Current { get; }
}
