using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Gateway.Planning;

public interface IWorkoutImportPreviewStore
{
  WorkoutImportPreview Add(string fileName, WorkoutImportFormat format, byte[] sourceBytes, WorkoutImportResult result);

  bool TryGet(Guid previewId, out WorkoutImportPreview? preview);
}

public sealed record WorkoutImportPreview(
  Guid Id,
  string FileName,
  WorkoutImportFormat Format,
  byte[] SourceBytes,
  WorkoutImportResult Result,
  DateTimeOffset CreatedAtUtc,
  DateTimeOffset ExpiresAtUtc);

public sealed class WorkoutImportPreviewStore(TimeProvider timeProvider) : IWorkoutImportPreviewStore
{
  public static readonly TimeSpan PreviewLifetime = TimeSpan.FromMinutes(15);
  private const long MaximumStoredBytes = 32L * 1024 * 1024;
  private const int MaximumPreviewCount = 16;
  private readonly object gate = new();
  private readonly Dictionary<Guid, WorkoutImportPreview> previews = [];

  public WorkoutImportPreview Add(
    string fileName,
    WorkoutImportFormat format,
    byte[] sourceBytes,
    WorkoutImportResult result)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(fileName);
    ArgumentNullException.ThrowIfNull(sourceBytes);
    ArgumentNullException.ThrowIfNull(result);
    if (sourceBytes.Length is 0 or > WorkoutImportLimits.MaximumBytes)
    {
      throw new ArgumentOutOfRangeException(nameof(sourceBytes), "Preview bytes must be between 1 byte and 10 MB.");
    }

    lock (gate)
    {
      DateTimeOffset now = timeProvider.GetUtcNow();
      PurgeExpired(now);
      while (previews.Count >= MaximumPreviewCount ||
             previews.Values.Sum(static preview => (long)preview.SourceBytes.Length) + sourceBytes.Length > MaximumStoredBytes)
      {
        Guid oldest = previews.Values.MinBy(static preview => preview.CreatedAtUtc)!.Id;
        previews.Remove(oldest);
      }

      var preview = new WorkoutImportPreview(
        Guid.NewGuid(),
        Path.GetFileName(fileName),
        format,
        [.. sourceBytes],
        result,
        now,
        now + PreviewLifetime);
      previews.Add(preview.Id, preview);
      return preview;
    }
  }

  public bool TryGet(Guid previewId, out WorkoutImportPreview? preview)
  {
    lock (gate)
    {
      PurgeExpired(timeProvider.GetUtcNow());
      return previews.TryGetValue(previewId, out preview);
    }
  }

  private void PurgeExpired(DateTimeOffset now)
  {
    foreach (Guid previewId in previews
      .Where(pair => pair.Value.ExpiresAtUtc <= now)
      .Select(static pair => pair.Key)
      .ToArray())
    {
      previews.Remove(previewId);
    }
  }
}
