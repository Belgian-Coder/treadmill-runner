using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using System.Text.Json;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record StoredWorkout(Guid Id, string Name, WorkoutKind Kind, bool IsArchived, int LatestRevisionNumber, Guid LatestRevisionId, string LatestDefinitionJson, string LatestContentSha256, DateTimeOffset LatestCreatedAtUtc);
public sealed record StoredWorkoutRevision(Guid Id, Guid WorkoutId, int RevisionNumber, string DefinitionJson, string ContentSha256, DateTimeOffset CreatedAtUtc);
public sealed record StoredWorkoutReuse(
  Guid WorkoutId,
  Guid WorkoutRevisionId,
  string DefinitionJson,
  DateTimeOffset LastCompletedAtUtc,
  TimeSpan LastActualDuration,
  int CompletionCount);
public sealed record WorkoutRevisionReceipt(Guid Id, Guid WorkoutId, int RevisionNumber, string DefinitionJson, string ContentSha256, DateTimeOffset CreatedAtUtc)
{
  public static WorkoutRevisionReceipt Parse(string json) =>
    JsonSerializer.Deserialize<WorkoutRevisionReceipt>(json)
      ?? throw new InvalidOperationException("Stored workout revision receipt is invalid.");
}
public sealed record ImportAuditRecord(Guid Id, Guid? UserProfileId, Guid WorkoutId, Guid WorkoutRevisionId, string OriginalFileName, string Format, string SourceSha256, string WarningSummaryJson, DateTimeOffset ImportedAtUtc);
public sealed record ImportConfirmationReceipt(
  Guid PreviewId,
  string SourceSha256,
  Guid WorkoutId,
  Guid WorkoutRevisionId,
  int RevisionNumber,
  string ContentSha256,
  string WarningSummaryJson)
{
  public static ImportConfirmationReceipt Parse(string json) =>
    JsonSerializer.Deserialize<ImportConfirmationReceipt>(json)
      ?? throw new InvalidOperationException("Stored import receipt is invalid.");
}
public sealed record ImportConfirmationOutcome(StoredWorkoutRevision Revision, ImportConfirmationReceipt Receipt, bool Replayed);

public interface IWorkoutStore
{
  Task<IReadOnlyList<StoredWorkout>> ListAsync(CancellationToken cancellationToken = default);
  Task<IReadOnlyList<StoredWorkoutReuse>> ListReusableAsync(Guid userProfileId, int take = 4, CancellationToken cancellationToken = default);
  Task<StoredWorkoutRevision> CreateAsync(Guid workoutId, WorkoutDefinition definition, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default, WorkoutKind kind = WorkoutKind.Structured);
  Task<StoredWorkoutRevision> AppendRevisionAsync(Guid workoutId, WorkoutDefinition definition, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<StoredWorkoutRevision?> FindRevisionAsync(Guid revisionId, CancellationToken cancellationToken = default);
  Task<IReadOnlyList<StoredWorkoutRevision>> ListRevisionsAsync(Guid workoutId, CancellationToken cancellationToken = default);
  Task RecordImportAsync(ImportAuditRecord audit, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<ImportConfirmationOutcome> ConfirmImportAsync(PersistenceWriteOperation operation, Guid previewId, Guid workoutId, WorkoutDefinition definition, ImportAuditRecord audit, DateTimeOffset nowUtc, CancellationToken cancellationToken = default);
  Task<bool> SetArchivedAsync(Guid workoutId, bool isArchived, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
}

public sealed class WorkoutStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IWorkoutStore
{
  private static readonly SemaphoreSlim RevisionAppendGate = new(1, 1);
  private static readonly SemaphoreSlim ImportConfirmationGate = new(1, 1);

  public async Task<IReadOnlyList<StoredWorkout>> ListAsync(CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.Workouts.AsNoTracking()
      .OrderBy(workout => workout.Name)
      .Select(workout => new
      {
        Workout = workout,
        Latest = workout.Revisions.OrderByDescending(revision => revision.RevisionNumber).First(),
      })
      .Select(item => new StoredWorkout(
        item.Workout.Id,
        item.Workout.Name,
        Enum.Parse<WorkoutKind>(item.Workout.Kind),
        item.Workout.IsArchived,
        item.Latest.RevisionNumber,
        item.Latest.Id,
        item.Latest.DefinitionJson,
        item.Latest.ContentSha256,
        item.Latest.CreatedAtUtc))
      .ToListAsync(cancellationToken);
  }

  public async Task<IReadOnlyList<StoredWorkoutReuse>> ListReusableAsync(
    Guid userProfileId,
    int take = 4,
    CancellationToken cancellationToken = default)
  {
    if (userProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.", nameof(userProfileId));
    if (take is < 1 or > 12) throw new ArgumentOutOfRangeException(nameof(take), "Reuse take must be between 1 and 12.");
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    IQueryable<WorkoutSessionEntity> recentCompleted = context.WorkoutSessions
      .FromSqlInterpolated($"""
        SELECT * FROM "WorkoutSessions"
        WHERE "UserProfileId" = {userProfileId}
          AND "State" = 'Completed'
          AND "EndedAtUtc" IS NOT NULL
        ORDER BY "EndedAtUtc" DESC
        LIMIT 1000
        """)
      .AsNoTracking();
    var rows = await recentCompleted
      .Join(
        context.WorkoutRevisions.AsNoTracking(),
        session => session.WorkoutRevisionId,
        revision => revision.Id,
        (session, revision) => new { Session = session, Revision = revision })
      .Join(
        context.Workouts.AsNoTracking().Where(workout => !workout.IsArchived && workout.Kind == "Structured"),
        item => item.Revision.WorkoutId,
        workout => workout.Id,
        (item, workout) => new { item.Session, item.Revision, Workout = workout })
      .Select(item => new
      {
        WorkoutId = item.Workout.Id,
        WorkoutRevisionId = item.Revision.Id,
        item.Revision.DefinitionJson,
        item.Session.EndedAtUtc,
        item.Session.DurationSeconds,
      })
      .ToArrayAsync(cancellationToken);
    return rows
      .Where(static item => item.EndedAtUtc is not null)
      .GroupBy(item => new { item.WorkoutId, item.WorkoutRevisionId, item.DefinitionJson })
      .Select(group =>
      {
        var latest = group.MaxBy(static item => item.EndedAtUtc)!;
        return new StoredWorkoutReuse(
          group.Key.WorkoutId,
          group.Key.WorkoutRevisionId,
          group.Key.DefinitionJson,
          latest.EndedAtUtc!.Value,
          TimeSpan.FromSeconds(latest.DurationSeconds),
          group.Count());
      })
      .OrderByDescending(static item => item.LastCompletedAtUtc)
      .Take(take)
      .ToArray();
  }

  public async Task<StoredWorkoutRevision> CreateAsync(
    Guid workoutId,
    WorkoutDefinition definition,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default,
    WorkoutKind kind = WorkoutKind.Structured)
  {
    ArgumentNullException.ThrowIfNull(definition);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var workout = new WorkoutEntity
    {
      Id = workoutId,
      Name = definition.Title,
      Kind = kind.ToString(),
      CreatedAtUtc = nowUtc,
    };
    var revision = CreateRevision(workoutId, 1, definition, nowUtc);
    workout.Revisions.Add(revision);
    context.Workouts.Add(workout);
    var mappedRevision = Map(revision);
    await PersistenceReceipts.SaveAsync(
      context, contextFactory, WithRevisionOutcome(operation, mappedRevision), cancellationToken);
    return mappedRevision;
  }

  public async Task<StoredWorkoutRevision> AppendRevisionAsync(
    Guid workoutId,
    WorkoutDefinition definition,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(definition);
    await RevisionAppendGate.WaitAsync(cancellationToken);
    try
    {
      for (var attempt = 1; ; attempt++)
      {
        try
        {
          return await AppendRevisionOnceAsync(workoutId, definition, nowUtc, operation, cancellationToken);
        }
        catch (Exception exception) when (attempt < 3 && IsRetryableAppendFailure(exception))
        {
          await Task.Delay(TimeSpan.FromMilliseconds(25 * attempt), cancellationToken);
        }
      }
    }
    finally
    {
      RevisionAppendGate.Release();
    }
  }

  private async Task<StoredWorkoutRevision> AppendRevisionOnceAsync(
    Guid workoutId,
    WorkoutDefinition definition,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var contentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(definition);
    var existing = await context.WorkoutRevisions.AsNoTracking()
      .SingleOrDefaultAsync(
        revision => revision.WorkoutId == workoutId && revision.ContentSha256 == contentSha256,
        cancellationToken);
    if (existing is not null)
    {
      var mappedExisting = Map(existing);
      await PersistenceReceipts.SaveAsync(
        context, contextFactory, WithRevisionOutcome(operation, mappedExisting), cancellationToken);
      return mappedExisting;
    }

    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    var workout = await context.Workouts.SingleOrDefaultAsync(candidate => candidate.Id == workoutId, cancellationToken)
      ?? throw new KeyNotFoundException($"Workout {workoutId} was not found.");
    var nextRevision = await context.WorkoutRevisions
      .Where(revision => revision.WorkoutId == workoutId)
      .MaxAsync(revision => revision.RevisionNumber, cancellationToken) + 1;
    var revision = CreateRevision(workoutId, nextRevision, definition, nowUtc);
    workout.Name = definition.Title;
    context.WorkoutRevisions.Add(revision);
    var mappedRevision = Map(revision);
    await PersistenceReceipts.SaveAsync(
      context, contextFactory, WithRevisionOutcome(operation, mappedRevision), cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return mappedRevision;
  }

  public async Task<StoredWorkoutRevision?> FindRevisionAsync(Guid revisionId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.WorkoutRevisions.AsNoTracking()
      .Where(revision => revision.Id == revisionId)
      .Select(revision => new StoredWorkoutRevision(
        revision.Id,
        revision.WorkoutId,
        revision.RevisionNumber,
        revision.DefinitionJson,
        revision.ContentSha256,
        revision.CreatedAtUtc))
      .SingleOrDefaultAsync(cancellationToken);
  }

  public async Task<IReadOnlyList<StoredWorkoutRevision>> ListRevisionsAsync(
    Guid workoutId,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.WorkoutRevisions.AsNoTracking()
      .Where(revision => revision.WorkoutId == workoutId)
      .OrderByDescending(revision => revision.RevisionNumber)
      .Select(revision => new StoredWorkoutRevision(
        revision.Id,
        revision.WorkoutId,
        revision.RevisionNumber,
        revision.DefinitionJson,
        revision.ContentSha256,
        revision.CreatedAtUtc))
      .ToListAsync(cancellationToken);
  }

  public async Task RecordImportAsync(ImportAuditRecord audit, PersistenceWriteOperation operation, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    context.ImportAudits.Add(new ImportAuditEntity
    {
      Id = audit.Id,
      UserProfileId = audit.UserProfileId,
      WorkoutId = audit.WorkoutId,
      WorkoutRevisionId = audit.WorkoutRevisionId,
      OriginalFileName = Path.GetFileName(audit.OriginalFileName),
      Format = audit.Format,
      SourceSha256 = audit.SourceSha256,
      WarningSummaryJson = audit.WarningSummaryJson,
      ImportedAtUtc = audit.ImportedAtUtc,
    });
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
  }

  public async Task<ImportConfirmationOutcome> ConfirmImportAsync(
    PersistenceWriteOperation operation,
    Guid previewId,
    Guid workoutId,
    WorkoutDefinition definition,
    ImportAuditRecord audit,
    DateTimeOffset nowUtc,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(definition);
    await ImportConfirmationGate.WaitAsync(cancellationToken);
    try
    {
      if (await FindImportOutcomeAsync(operation, cancellationToken) is { } replay)
      {
        if (replay.Receipt.PreviewId != previewId)
        {
          throw new InvalidOperationException(
            $"Operation {operation.ClientOperationId} was completed for a different import preview.");
        }

        return replay;
      }

      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
      var contentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(definition);
      var existingRevision = await context.ImportAudits
        .Where(existingAudit => existingAudit.Format == audit.Format && existingAudit.SourceSha256 == audit.SourceSha256)
        .Select(existingAudit => existingAudit.WorkoutRevisionId)
        .Join(
          context.WorkoutRevisions,
          revisionId => revisionId,
          revision => revision.Id,
          (_, revision) => revision)
        .Where(revision => revision.ContentSha256 == contentSha256)
        .AsNoTracking()
        .FirstOrDefaultAsync(cancellationToken)
        ?? await context.WorkoutRevisions.AsNoTracking()
          .OrderBy(revision => revision.Id)
          .FirstOrDefaultAsync(revision => revision.ContentSha256 == contentSha256, cancellationToken);
      if (existingRevision is not null)
      {
        AddImportAudit(context, audit, existingRevision.WorkoutId, existingRevision.Id, nowUtc);
        var deduplicatedReceipt = CreateImportReceipt(previewId, audit.SourceSha256, existingRevision, audit.WarningSummaryJson);
        try
        {
          await PersistenceReceipts.SaveAsync(
            context,
            contextFactory,
            operation with { OutcomeJson = JsonSerializer.Serialize(deduplicatedReceipt) },
            cancellationToken);
          await transaction.CommitAsync(cancellationToken);
          return new ImportConfirmationOutcome(Map(existingRevision), deduplicatedReceipt, Replayed: false);
        }
        catch (OperationReplayException)
        {
          await transaction.RollbackAsync(cancellationToken);
          return await FindImportOutcomeAsync(operation, cancellationToken)
            ?? throw new InvalidOperationException("The concurrent import receipt could not be loaded.");
        }
      }

      var workout = new WorkoutEntity
      {
        Id = workoutId,
        Name = definition.Title,
        Kind = WorkoutKind.Structured.ToString(),
        CreatedAtUtc = nowUtc,
      };
      var revision = CreateRevision(workoutId, 1, definition, nowUtc);
      workout.Revisions.Add(revision);
      context.Workouts.Add(workout);
      AddImportAudit(context, audit, workoutId, revision.Id, nowUtc);
      ImportConfirmationReceipt completedReceipt = CreateImportReceipt(
        previewId,
        audit.SourceSha256,
        revision,
        audit.WarningSummaryJson);

      try
      {
        await PersistenceReceipts.SaveAsync(
          context,
          contextFactory,
          operation with { OutcomeJson = JsonSerializer.Serialize(completedReceipt) },
          cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        var mappedRevision = Map(revision);
        return new ImportConfirmationOutcome(
          mappedRevision,
          completedReceipt,
          Replayed: false);
      }
      catch (OperationReplayException)
      {
        await transaction.RollbackAsync(cancellationToken);
        var concurrentReplay = await FindImportOutcomeAsync(operation, cancellationToken);
        if (concurrentReplay is null)
        {
          throw;
        }

        return concurrentReplay;
      }
    }
    finally
    {
      ImportConfirmationGate.Release();
    }
  }

  public async Task<bool> SetArchivedAsync(Guid workoutId, bool isArchived, PersistenceWriteOperation operation, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    var workout = await context.Workouts.SingleOrDefaultAsync(candidate => candidate.Id == workoutId, cancellationToken);
    if (workout is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }

    workout.IsArchived = isArchived;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  private static WorkoutRevisionEntity CreateRevision(Guid workoutId, int number, WorkoutDefinition definition, DateTimeOffset nowUtc) => new()
  {
    Id = Guid.NewGuid(),
    WorkoutId = workoutId,
    RevisionNumber = number,
    DefinitionJson = WorkoutDefinitionCanonicalizer.Serialize(definition),
    ContentSha256 = WorkoutDefinitionCanonicalizer.ComputeSha256(definition),
    CreatedAtUtc = nowUtc,
  };

  private static StoredWorkoutRevision Map(WorkoutRevisionEntity revision) => new(
    revision.Id,
    revision.WorkoutId,
    revision.RevisionNumber,
    revision.DefinitionJson,
    revision.ContentSha256,
    revision.CreatedAtUtc);

  private static PersistenceWriteOperation WithRevisionOutcome(
    PersistenceWriteOperation operation,
    StoredWorkoutRevision revision) =>
    operation with
    {
      OutcomeJson = JsonSerializer.Serialize(new WorkoutRevisionReceipt(
        revision.Id,
        revision.WorkoutId,
        revision.RevisionNumber,
        revision.DefinitionJson,
        revision.ContentSha256,
        revision.CreatedAtUtc)),
    };

  private static bool IsRetryableAppendFailure(Exception exception)
  {
    for (var current = exception; current is not null; current = current.InnerException!)
    {
      if (current is SqliteException { SqliteErrorCode: 5 or 6 })
      {
        return true;
      }

      if (current is SqliteException { SqliteErrorCode: 19 } sqliteException &&
          sqliteException.Message.Contains("WorkoutRevisions", StringComparison.Ordinal))
      {
        return true;
      }
    }

    return false;
  }

  private async Task<ImportConfirmationOutcome?> FindImportOutcomeAsync(
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    var storedReceipt = await context.OperationReceipts.AsNoTracking()
      .Where(receipt => receipt.ClientOperationId == operation.ClientOperationId)
      .Select(receipt => new OperationReceipt(
        receipt.Id,
        receipt.ClientOperationId,
        receipt.OperationType,
        receipt.StatusCode,
        receipt.OutcomeJson,
        receipt.CreatedAtUtc,
        receipt.RequestFingerprint))
      .SingleOrDefaultAsync(cancellationToken);
    if (storedReceipt is null)
    {
      return null;
    }

    if (storedReceipt.OperationType != operation.OperationType ||
        storedReceipt.RequestFingerprint != operation.RequestFingerprint)
    {
      throw new OperationScopeConflictException(storedReceipt, operation);
    }

    var receipt = ImportConfirmationReceipt.Parse(storedReceipt.OutcomeJson);
    var revision = await context.WorkoutRevisions.AsNoTracking()
      .SingleAsync(candidate => candidate.Id == receipt.WorkoutRevisionId, cancellationToken);
    return new ImportConfirmationOutcome(Map(revision), receipt, Replayed: true);
  }

  private static ImportConfirmationReceipt CreateImportReceipt(
    Guid previewId,
    string sourceSha256,
    WorkoutRevisionEntity revision,
    string warningSummaryJson) =>
    new(previewId, sourceSha256, revision.WorkoutId, revision.Id, revision.RevisionNumber, revision.ContentSha256, warningSummaryJson);

  private static void AddImportAudit(
    TreadmillRunnerDbContext context,
    ImportAuditRecord audit,
    Guid workoutId,
    Guid workoutRevisionId,
    DateTimeOffset nowUtc) =>
    context.ImportAudits.Add(new ImportAuditEntity
    {
      Id = audit.Id,
      UserProfileId = audit.UserProfileId,
      WorkoutId = workoutId,
      WorkoutRevisionId = workoutRevisionId,
      OriginalFileName = Path.GetFileName(audit.OriginalFileName),
      Format = audit.Format,
      SourceSha256 = audit.SourceSha256,
      WarningSummaryJson = audit.WarningSummaryJson,
      ImportedAtUtc = nowUtc,
    });
}
