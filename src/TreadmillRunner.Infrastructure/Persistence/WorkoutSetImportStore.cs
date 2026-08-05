using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record WorkoutSetImportItem(
  string CanonicalSlot,
  string Variant,
  string SourcePath,
  WorkoutDefinition Definition);

public sealed record WorkoutSetImportReceipt(
  Guid PreviewId,
  string SourceSha256,
  Guid WorkoutProgramId,
  Guid WorkoutProgramRevisionId,
  string ProgramName,
  int WorkoutCount)
{
  public static WorkoutSetImportReceipt Parse(string json) =>
    JsonSerializer.Deserialize<WorkoutSetImportReceipt>(json)
    ?? throw new InvalidOperationException("Stored workout-set import receipt is invalid.");
}

public sealed record WorkoutSetImportOutcome(WorkoutSetImportReceipt Receipt, bool Replayed);

public interface IWorkoutSetImportStore
{
  Task<WorkoutSetImportOutcome> ImportAsync(
    PersistenceWriteOperation operation,
    Guid previewId,
    string sourceSha256,
    Guid? userProfileId,
    string sourceFileName,
    string programName,
    string? programDescription,
    string category,
    IReadOnlyList<WorkoutSetImportItem> items,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default);
}

public sealed class WorkoutSetImportStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IWorkoutSetImportStore
{
  private static readonly SemaphoreSlim ImportGate = new(1, 1);

  public async Task<WorkoutSetImportOutcome> ImportAsync(
    PersistenceWriteOperation operation,
    Guid previewId,
    string sourceSha256,
    Guid? userProfileId,
    string sourceFileName,
    string programName,
    string? programDescription,
    string category,
    IReadOnlyList<WorkoutSetImportItem> items,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(sourceSha256);
    ArgumentException.ThrowIfNullOrWhiteSpace(programName);
    ArgumentException.ThrowIfNullOrWhiteSpace(category);
    ArgumentNullException.ThrowIfNull(items);
    if (previewId == Guid.Empty || items.Count == 0 || items.Count > 1_000)
      throw new ArgumentException("A workout-set import requires a preview and 1 to 1,000 selected workouts.");
    if (items.Select(static item => item.CanonicalSlot).Distinct(StringComparer.Ordinal).Count() != items.Count)
      throw new ArgumentException("A workout-set import must select exactly one workout per canonical slot.");

    await ImportGate.WaitAsync(cancellationToken);
    try
    {
      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      OperationReceipt? existing = await FindReceiptAsync(context, operation.ClientOperationId, cancellationToken);
      if (existing is not null)
      {
        PersistenceReceipts.ThrowReplayOrScopeConflict(existing, operation);
      }

      await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
      if (userProfileId is { } profileId && !await context.UserProfiles.AsNoTracking()
        .AnyAsync(profile => profile.Id == profileId && !profile.IsArchived, cancellationToken))
        throw new KeyNotFoundException("The selected profile does not exist or is archived.");

      Guid programId = Guid.NewGuid();
      Guid programRevisionId = Guid.NewGuid();
      var program = new WorkoutProgramEntity
      {
        Id = programId,
        CreatedAtUtc = nowUtc,
      };
      var programRevision = new WorkoutProgramRevisionEntity
      {
        Id = programRevisionId,
        WorkoutProgramId = programId,
        RevisionNumber = 1,
        Name = programName,
        Description = programDescription,
        Category = category,
        CreatedAtUtc = nowUtc,
      };
      program.Revisions.Add(programRevision);
      context.WorkoutPrograms.Add(program);

      int position = 0;
      foreach (WorkoutSetImportItem item in items)
      {
        position++;
        Guid workoutId = Guid.NewGuid();
        Guid revisionId = Guid.NewGuid();
        string definitionJson = WorkoutDefinitionCanonicalizer.Serialize(item.Definition);
        string contentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(item.Definition);
        var workout = new WorkoutEntity
        {
          Id = workoutId,
          Name = item.Definition.Title,
          Kind = WorkoutKind.Structured.ToString(),
          CreatedAtUtc = nowUtc,
        };
        workout.Revisions.Add(new WorkoutRevisionEntity
        {
          Id = revisionId,
          WorkoutId = workoutId,
          RevisionNumber = 1,
          DefinitionJson = definitionJson,
          ContentSha256 = contentSha256,
          CreatedAtUtc = nowUtc,
        });
        context.Workouts.Add(workout);
        context.ImportAudits.Add(new ImportAuditEntity
        {
          Id = Guid.NewGuid(),
          UserProfileId = userProfileId,
          WorkoutId = workoutId,
          WorkoutRevisionId = revisionId,
          OriginalFileName = Path.GetFileName(sourceFileName),
          Format = "TreadmillWorkoutBundleV4",
          SourceSha256 = sourceSha256,
          WarningSummaryJson = JsonSerializer.Serialize(new
          {
            item.CanonicalSlot,
            item.Variant,
            SourcePath = Path.GetFileName(item.SourcePath),
          }),
          ImportedAtUtc = nowUtc,
        });
        programRevision.Items.Add(new WorkoutProgramItemEntity
        {
          Id = Guid.NewGuid(),
          WorkoutProgramRevisionId = programRevisionId,
          WorkoutRevisionId = revisionId,
          Position = position,
        });
      }

      var domainProgram = new WorkoutProgramRevision(
        programId,
        programRevisionId,
        1,
        programName,
        programDescription,
        category,
        programRevision.Items.OrderBy(static item => item.Position)
          .Select(item => new WorkoutProgramItem(item.Id, item.WorkoutRevisionId, item.Position)).ToArray());
      programRevision.ContentSha256 = WorkoutProgramCanonicalizer.ComputeSha256(domainProgram);
      var receipt = new WorkoutSetImportReceipt(
        previewId,
        sourceSha256,
        programId,
        programRevisionId,
        programName,
        items.Count);
      PersistenceReceipts.Add(context, operation with { OutcomeJson = JsonSerializer.Serialize(receipt) });
      await context.SaveChangesAsync(cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return new WorkoutSetImportOutcome(receipt, Replayed: false);
    }
    catch (OperationReplayException replay)
    {
      return new WorkoutSetImportOutcome(WorkoutSetImportReceipt.Parse(replay.Receipt.OutcomeJson), Replayed: true);
    }
    finally
    {
      ImportGate.Release();
    }
  }

  private static async Task<OperationReceipt?> FindReceiptAsync(
    TreadmillRunnerDbContext context,
    Guid operationId,
    CancellationToken cancellationToken) =>
    await context.OperationReceipts.AsNoTracking()
      .Where(receipt => receipt.ClientOperationId == operationId)
      .Select(receipt => new OperationReceipt(
        receipt.Id,
        receipt.ClientOperationId,
        receipt.OperationType,
        receipt.StatusCode,
        receipt.OutcomeJson,
        receipt.CreatedAtUtc,
        receipt.RequestFingerprint))
      .SingleOrDefaultAsync(cancellationToken);
}
