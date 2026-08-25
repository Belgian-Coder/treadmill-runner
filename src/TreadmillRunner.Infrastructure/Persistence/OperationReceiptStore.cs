using Microsoft.EntityFrameworkCore;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record OperationReceipt(
  Guid Id,
  Guid ClientOperationId,
  string OperationType,
  int StatusCode,
  string OutcomeJson,
  DateTimeOffset CreatedAtUtc,
  string RequestFingerprint);
public sealed record PersistenceWriteOperation(
  Guid ClientOperationId,
  string OperationType,
  int StatusCode,
  string OutcomeJson,
  DateTimeOffset CreatedAtUtc,
  string RequestFingerprint,
  int NotFoundStatusCode = 404,
  string NotFoundOutcomeJson = "{}")
{
  internal PersistenceWriteOperation ForNotFound() => this with
  {
    StatusCode = NotFoundStatusCode,
    OutcomeJson = NotFoundOutcomeJson,
  };
}

public sealed class OperationReplayException(OperationReceipt receipt, Exception? innerException = null)
  : InvalidOperationException($"Operation {receipt.ClientOperationId} has already completed.", innerException)
{
  public OperationReceipt Receipt { get; } = receipt;
}

public sealed class OperationScopeConflictException(
  OperationReceipt receipt,
  PersistenceWriteOperation attemptedOperation,
  Exception? innerException = null)
  : InvalidOperationException(
    $"Operation {receipt.ClientOperationId} was already used for a different action or request.",
    innerException)
{
  public OperationReceipt Receipt { get; } = receipt;
  public PersistenceWriteOperation AttemptedOperation { get; } = attemptedOperation;
}

public interface IOperationReceiptStore
{
  Task<OperationReceipt?> FindAsync(Guid clientOperationId, CancellationToken cancellationToken = default);
  Task<bool> TryAddAsync(OperationReceipt receipt, CancellationToken cancellationToken = default);
  Task<int> PruneAsync(DateTimeOffset olderThanUtc, CancellationToken cancellationToken = default);
}

internal static class PersistenceReceipts
{
  public static async Task ThrowIfCompletedAsync(
    TreadmillRunnerDbContext context,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken)
  {
    var receipt = await context.OperationReceipts.AsNoTracking()
      .Where(candidate => candidate.ClientOperationId == operation.ClientOperationId)
      .Select(candidate => new OperationReceipt(
        candidate.Id, candidate.ClientOperationId, candidate.OperationType,
        candidate.StatusCode, candidate.OutcomeJson, candidate.CreatedAtUtc,
        candidate.RequestFingerprint))
      .SingleOrDefaultAsync(cancellationToken);
    if (receipt is not null)
    {
      ThrowReplayOrScopeConflict(receipt, operation);
    }
  }

  public static void Add(TreadmillRunnerDbContext context, PersistenceWriteOperation operation) =>
    context.OperationReceipts.Add(new OperationReceiptEntity
    {
      Id = Guid.NewGuid(),
      ClientOperationId = operation.ClientOperationId,
      OperationType = operation.OperationType,
      StatusCode = operation.StatusCode,
      OutcomeJson = operation.OutcomeJson,
      CreatedAtUtc = operation.CreatedAtUtc,
      RequestFingerprint = operation.RequestFingerprint,
    });

  public static async Task SaveAsync(
    TreadmillRunnerDbContext context,
    IDbContextFactory<TreadmillRunnerDbContext> contextFactory,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken)
  {
    Add(context, operation);
    try
    {
      await context.SaveChangesAsync(cancellationToken);
    }
    catch (DbUpdateException exception)
    {
      await using var replayContext = await contextFactory.CreateDbContextAsync(cancellationToken);
      var receipt = await replayContext.OperationReceipts.AsNoTracking()
        .Where(candidate => candidate.ClientOperationId == operation.ClientOperationId)
        .Select(candidate => new OperationReceipt(
          candidate.Id, candidate.ClientOperationId, candidate.OperationType,
          candidate.StatusCode, candidate.OutcomeJson, candidate.CreatedAtUtc,
          candidate.RequestFingerprint))
        .SingleOrDefaultAsync(cancellationToken);
      if (receipt is not null)
      {
        ThrowReplayOrScopeConflict(receipt, operation, exception);
      }

      throw;
    }
  }

  public static void ThrowReplayOrScopeConflict(
    OperationReceipt receipt,
    PersistenceWriteOperation operation,
    Exception? innerException = null)
  {
    if (string.Equals(receipt.OperationType, operation.OperationType, StringComparison.Ordinal) &&
        string.Equals(receipt.RequestFingerprint, operation.RequestFingerprint, StringComparison.Ordinal))
    {
      throw new OperationReplayException(receipt, innerException);
    }

    throw new OperationScopeConflictException(receipt, operation, innerException);
  }
}

public sealed class OperationReceiptStore(IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IOperationReceiptStore
{
  public async Task<OperationReceipt?> FindAsync(Guid clientOperationId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.OperationReceipts.AsNoTracking()
      .Where(receipt => receipt.ClientOperationId == clientOperationId)
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

  public async Task<bool> TryAddAsync(OperationReceipt receipt, CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(receipt);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    context.OperationReceipts.Add(new OperationReceiptEntity
    {
      Id = receipt.Id,
      ClientOperationId = receipt.ClientOperationId,
      OperationType = receipt.OperationType,
      StatusCode = receipt.StatusCode,
      OutcomeJson = receipt.OutcomeJson,
      CreatedAtUtc = receipt.CreatedAtUtc,
      RequestFingerprint = receipt.RequestFingerprint,
    });
    try
    {
      await context.SaveChangesAsync(cancellationToken);
      return true;
    }
    catch (DbUpdateException)
    {
      return false;
    }
  }

  public async Task<int> PruneAsync(
    DateTimeOffset olderThanUtc,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await context.Database.ExecuteSqlInterpolatedAsync($"""
      DELETE FROM OperationReceipts
      WHERE julianday(CreatedAtUtc) < julianday({olderThanUtc})
      """, cancellationToken);
  }
}
