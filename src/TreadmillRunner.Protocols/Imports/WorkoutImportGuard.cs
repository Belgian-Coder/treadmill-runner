using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

internal static class WorkoutImportGuard
{
  public static async ValueTask<byte[]> ReadBoundedAsync(
      Stream source,
      CancellationToken cancellationToken)
  {
    ArgumentNullException.ThrowIfNull(source);
    if (!source.CanRead)
    {
      throw new WorkoutImportException("The workout source is not readable.");
    }

    using MemoryStream buffer = new();
    byte[] chunk = new byte[81920];
    while (true)
    {
      int read = await source.ReadAsync(chunk, cancellationToken).ConfigureAwait(false);
      if (read == 0)
      {
        break;
      }

      if (buffer.Length + read > WorkoutImportLimits.MaximumBytes)
      {
        throw new WorkoutImportException("The workout file exceeds the 10 MB limit.");
      }

      buffer.Write(chunk, 0, read);
    }

    if (buffer.Length == 0)
    {
      throw new WorkoutImportException("The workout file is empty.");
    }

    return buffer.ToArray();
  }

  public static WorkoutImportResult Validate(
      WorkoutImportFormat format,
      WorkoutDefinition definition,
      IReadOnlyList<WorkoutImportWarning> warnings)
  {
    return new WorkoutImportResult(
        format,
        definition,
        definition.ExpandedStepCount,
        definition.KnownDuration,
        warnings);
  }
}
