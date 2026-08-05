using System.Security.Cryptography;
using Microsoft.AspNetCore.Mvc;
using TreadmillRunner.Infrastructure.Persistence;
using TreadmillRunner.Protocols.Imports;

namespace TreadmillRunner.Gateway.Planning;

public sealed record WorkoutSetImportConfirmRequest(
  Guid OperationId,
  Guid PreviewId,
  string SourceSha256,
  Guid? ProfileId,
  string SelectionStrategy);

public sealed record WorkoutSetImportPreview(
  Guid Id,
  string FileName,
  byte[] SourceBytes,
  string SourceSha256,
  TreadmillWorkoutBundle Bundle,
  DateTimeOffset CreatedAtUtc,
  DateTimeOffset ExpiresAtUtc);

public sealed class WorkoutSetImportPreviewStore(TimeProvider timeProvider)
{
  private static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(15);
  private const int MaximumCount = 4;
  private const long MaximumBytes = 128L * 1024 * 1024;
  private readonly object _gate = new();
  private readonly Dictionary<Guid, WorkoutSetImportPreview> _previews = [];

  public WorkoutSetImportPreview Add(string fileName, byte[] sourceBytes, TreadmillWorkoutBundle bundle)
  {
    lock (_gate)
    {
      DateTimeOffset now = timeProvider.GetUtcNow();
      Purge(now);
      while (_previews.Count >= MaximumCount ||
             _previews.Values.Sum(static preview => (long)preview.SourceBytes.Length) + sourceBytes.Length > MaximumBytes)
      {
        Guid oldest = _previews.Values.MinBy(static preview => preview.CreatedAtUtc)!.Id;
        _previews.Remove(oldest);
      }
      var preview = new WorkoutSetImportPreview(
        Guid.NewGuid(),
        NormalizeFileName(fileName),
        sourceBytes,
        Convert.ToHexStringLower(SHA256.HashData(sourceBytes)),
        bundle,
        now,
        now + Lifetime);
      _previews.Add(preview.Id, preview);
      return preview;
    }
  }

  private static string NormalizeFileName(string fileName)
  {
    string safe = Path.GetFileName(fileName).Trim();
    if (safe.Length == 0) safe = "generated-workout-set.zip";
    return safe.Length <= 255 ? safe : safe[..255];
  }

  public bool TryGet(Guid id, out WorkoutSetImportPreview? preview)
  {
    lock (_gate)
    {
      Purge(timeProvider.GetUtcNow());
      return _previews.TryGetValue(id, out preview);
    }
  }

  private void Purge(DateTimeOffset now)
  {
    foreach (Guid id in _previews.Where(item => item.Value.ExpiresAtUtc <= now).Select(static item => item.Key).ToArray())
      _previews.Remove(id);
  }
}

public static class WorkoutSetPlanningEndpoints
{
  private const int MultipartOverhead = 64 * 1024;

  public static IEndpointRouteBuilder MapWorkoutSetPlanning(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/planning/workout-sets");
    group.MapPost("/import/preview", PreviewAsync).DisableAntiforgery();
    group.MapPost("/import/confirm", ConfirmAsync);
    return endpoints;
  }

  [RequestSizeLimit(TreadmillWorkoutBundleImporter.MaximumArchiveBytes + MultipartOverhead)]
  [RequestFormLimits(MultipartBodyLengthLimit = TreadmillWorkoutBundleImporter.MaximumArchiveBytes)]
  private static async Task<IResult> PreviewAsync(
    HttpRequest request,
    TreadmillWorkoutBundleImporter importer,
    WorkoutSetImportPreviewStore previews,
    CancellationToken cancellationToken)
  {
    try
    {
      if (request.ContentLength is > TreadmillWorkoutBundleImporter.MaximumArchiveBytes + MultipartOverhead)
        return Results.Problem("The workout-set upload exceeds 64 MB.", statusCode: StatusCodes.Status413PayloadTooLarge);
      if (!request.HasFormContentType) throw new WorkoutImportException("Workout-set preview requires multipart form data.");
      IFormCollection form = await request.ReadFormAsync(cancellationToken);
      IFormFile? file = form.Files.GetFile("file");
      if (form.Files.Count != 1 || file is null || form.Count != 0)
        throw new WorkoutImportException("Upload exactly one generated workout-set ZIP.");
      if (file.Length is <= 0 or > TreadmillWorkoutBundleImporter.MaximumArchiveBytes)
        throw new WorkoutImportException("Choose a non-empty generated workout-set ZIP no larger than 64 MB.");
      await using Stream input = file.OpenReadStream();
      using var buffer = new MemoryStream((int)file.Length);
      await input.CopyToAsync(buffer, cancellationToken);
      byte[] source = buffer.ToArray();
      await using var parseStream = new MemoryStream(source, writable: false);
      TreadmillWorkoutBundle bundle = await importer.ImportAsync(parseStream, file.FileName, cancellationToken);
      WorkoutSetImportPreview preview = previews.Add(file.FileName, source, bundle);
      return Results.Ok(ToDto(preview));
    }
    catch (WorkoutImportException exception)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]> { ["file"] = [exception.Message] });
    }
    catch (InvalidDataException)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]> { ["file"] = ["The uploaded ZIP is malformed."] });
    }
  }

  private static async Task<IResult> ConfirmAsync(
    WorkoutSetImportConfirmRequest request,
    WorkoutSetImportPreviewStore previews,
    TreadmillWorkoutBundleImporter importer,
    IWorkoutSetImportStore store,
    IOperationReceiptStore receipts,
    TimeProvider timeProvider,
    CancellationToken cancellationToken)
  {
    try
    {
      if (request.OperationId == Guid.Empty || request.PreviewId == Guid.Empty)
        throw new ArgumentException("OperationId and PreviewId are required.");
      if (!Enum.TryParse(request.SelectionStrategy, ignoreCase: true, out WorkoutSetSelectionStrategy strategy) || !Enum.IsDefined(strategy))
        throw new ArgumentException("Choose Default, PreferHeartRate, PreferFixed, or PreferOmegaRecovery.");
      string fingerprint = PlanningOperationFingerprint.Compute(new
      {
        request.PreviewId,
        request.SourceSha256,
        request.ProfileId,
        Strategy = strategy,
      });
      if (await receipts.FindAsync(request.OperationId, cancellationToken) is { } completed)
      {
        if (completed.OperationType != "workout-set.import.confirm" || completed.RequestFingerprint != fingerprint)
          return Results.Conflict(new { message = "That operation ID was already used for a different action." });
        return Results.Ok(WorkoutSetImportReceipt.Parse(completed.OutcomeJson) with { });
      }
      if (!previews.TryGet(request.PreviewId, out WorkoutSetImportPreview? preview) || preview is null)
        return Results.Problem("The workout-set preview expired or is no longer available.", statusCode: StatusCodes.Status410Gone);
      if (!string.Equals(preview.SourceSha256, request.SourceSha256, StringComparison.Ordinal))
        return Results.Conflict(new { message = "The preview source does not match the confirmation request." });

      await using var source = new MemoryStream(preview.SourceBytes, writable: false);
      TreadmillWorkoutBundle reparsed = await importer.ImportAsync(source, preview.FileName, cancellationToken);
      IReadOnlyList<TreadmillWorkoutBundleVariant> selected = reparsed.Select(strategy);
      DateTimeOffset now = timeProvider.GetUtcNow();
      WorkoutSetImportOutcome outcome = await store.ImportAsync(
        new PersistenceWriteOperation(
          request.OperationId,
          "workout-set.import.confirm",
          StatusCodes.Status201Created,
          "{}",
          now,
          fingerprint),
        request.PreviewId,
        preview.SourceSha256,
        request.ProfileId,
        preview.FileName,
        reparsed.PlanName,
        $"Generated by treadmill-workout {reparsed.ToolVersion}. Exactly one variant is selected for each canonical slot ({strategy}).",
        reparsed.Category,
        selected.Select(item => new WorkoutSetImportItem(
          item.CanonicalSlot,
          item.Variant,
          item.SourcePath,
          item.Definition)).ToArray(),
        now,
        cancellationToken);
      return Results.Created($"/api/planning/programs/{outcome.Receipt.WorkoutProgramId}", new
      {
        outcome.Receipt.PreviewId,
        outcome.Receipt.SourceSha256,
        outcome.Receipt.WorkoutProgramId,
        outcome.Receipt.WorkoutProgramRevisionId,
        outcome.Receipt.ProgramName,
        outcome.Receipt.WorkoutCount,
        Strategy = strategy.ToString(),
        outcome.Replayed,
      });
    }
    catch (WorkoutImportException exception)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]> { ["previewId"] = [exception.Message] });
    }
    catch (ArgumentException exception)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [exception.Message] });
    }
    catch (KeyNotFoundException exception)
    {
      return Results.ValidationProblem(new Dictionary<string, string[]> { ["profileId"] = [exception.Message] });
    }
    catch (OperationScopeConflictException)
    {
      return Results.Conflict(new { message = "That operation ID was already used for a different action." });
    }
  }

  private static object ToDto(WorkoutSetImportPreview preview)
  {
    var strategies = Enum.GetValues<WorkoutSetSelectionStrategy>().Select(strategy => new
    {
      Name = strategy.ToString(),
      Substitutions = preview.Bundle.Select(strategy).Count(item => item.Variant != "primary"),
    }).ToArray();
    return new
    {
      PreviewId = preview.Id,
      preview.SourceSha256,
      preview.FileName,
      preview.Bundle.PlanName,
      preview.Bundle.Category,
      preview.Bundle.ToolVersion,
      SlotCount = preview.Bundle.Slots.Count,
      VariantCount = preview.Bundle.Slots.Sum(static slot => slot.Variants.Count),
      preview.ExpiresAtUtc,
      preview.Bundle.Warnings,
      Strategies = strategies,
      Slots = preview.Bundle.Slots.Select(slot => new
      {
        slot.CanonicalSlot,
        slot.Week,
        slot.Session,
        Variants = slot.Variants.Select(variant => new
        {
          variant.SessionId,
          variant.Variant,
          variant.Title,
          variant.ControlMode,
          variant.SelectionRule,
        }).ToArray(),
      }).ToArray(),
    };
  }
}
