using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Protocols.Imports;

public enum WorkoutImportFormat
{
  NativeJson,
  QDomyosXml,
  GarminFit,
}

public sealed record WorkoutImportWarning(string Code, string Message);

public sealed record WorkoutImportResult(
    WorkoutImportFormat Format,
    WorkoutDefinition Definition,
    int ExpandedStepCount,
    TimeSpan? TotalDuration,
    IReadOnlyList<WorkoutImportWarning> Warnings);

public interface IWorkoutImporter
{
  WorkoutImportFormat Format { get; }

  ValueTask<WorkoutImportResult> ImportAsync(
      Stream source,
      string fileName,
      CancellationToken cancellationToken = default);
}

public sealed class WorkoutImportException(string message, Exception? innerException = null)
    : FormatException(message, innerException);

public static class WorkoutImportLimits
{
  public const int MaximumBytes = 10 * 1024 * 1024;
  public const int MaximumExpandedSteps = WorkoutDefinitionLimits.MaximumExpandedSteps;
  public static readonly TimeSpan MaximumDuration = WorkoutDefinitionLimits.MaximumKnownDuration;
}
