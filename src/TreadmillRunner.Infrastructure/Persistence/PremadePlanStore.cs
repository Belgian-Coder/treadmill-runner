using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record PremadePlanInstallation(
  Guid Id,
  Guid UserProfileId,
  string TemplateId,
  string TemplateVersion,
  string TemplateContentSha256,
  int CopyNumber,
  Guid WorkoutProgramId,
  DateTimeOffset CreatedAtUtc);

public sealed record PremadePlanMaterialization(
  PremadePlanTemplate Template,
  Guid UserProfileId,
  IReadOnlyDictionary<string, WorkoutDefinition> WorkoutsByKey,
  bool FreshCopy);

public sealed record PremadePlanMaterializationResult(
  PremadePlanInstallation Installation,
  Guid WorkoutProgramRevisionId,
  int PositionCount,
  int UniqueWorkoutCount,
  bool AlreadyAdded,
  bool Replayed);

public interface IPremadePlanStore
{
  Task<IReadOnlyList<PremadePlanInstallation>> ListAsync(Guid userProfileId, CancellationToken cancellationToken = default);
  Task<PremadePlanMaterializationResult> MaterializeAsync(
    PremadePlanMaterialization request,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default);
}

public sealed class PremadePlanStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IPremadePlanStore
{
  private static readonly SemaphoreSlim MaterializationGate = new(1, 1);

  public async Task<IReadOnlyList<PremadePlanInstallation>> ListAsync(
    Guid userProfileId,
    CancellationToken cancellationToken = default)
  {
    if (userProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.", nameof(userProfileId));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    PremadePlanInstallationEntity[] installations = await context.PremadePlanInstallations.AsNoTracking()
      .Where(installation => installation.UserProfileId == userProfileId)
      .OrderBy(installation => installation.TemplateId)
      .ThenBy(installation => installation.CopyNumber)
      .ToArrayAsync(cancellationToken);
    return installations.Select(Map).ToArray();
  }

  public async Task<PremadePlanMaterializationResult> MaterializeAsync(
    PremadePlanMaterialization request,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(request);
    if (request.UserProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.", nameof(request));
    if (request.WorkoutsByKey.Count == 0) throw new ArgumentException("At least one workout is required.", nameof(request));
    await MaterializationGate.WaitAsync(cancellationToken);
    try
    {
      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
      if (!await context.UserProfiles.AnyAsync(profile => profile.Id == request.UserProfileId && !profile.IsArchived, cancellationToken))
        throw new KeyNotFoundException("Profile was not found.");

      PremadePlanInstallationEntity[] prior = await context.PremadePlanInstallations
        .Where(installation => installation.UserProfileId == request.UserProfileId &&
          installation.TemplateId == request.Template.Id &&
          installation.TemplateVersion == request.Template.Version)
        .OrderBy(installation => installation.CopyNumber)
        .ToArrayAsync(cancellationToken);
      if (!request.FreshCopy && prior.FirstOrDefault() is { } existing)
      {
        var existingResult = new PremadePlanMaterializationResult(
          Map(existing),
          await context.WorkoutProgramRevisions.Where(revision => revision.WorkoutProgramId == existing.WorkoutProgramId)
            .OrderByDescending(revision => revision.RevisionNumber).Select(revision => revision.Id).FirstAsync(cancellationToken),
          request.Template.SessionCount,
          await context.WorkoutProgramItems.Where(item => item.WorkoutProgramRevision.WorkoutProgramId == existing.WorkoutProgramId)
            .Select(item => item.WorkoutRevisionId).Distinct().CountAsync(cancellationToken),
          AlreadyAdded: true,
          Replayed: false);
        await PersistenceReceipts.SaveAsync(
          context,
          contextFactory,
          operation with { StatusCode = 200, OutcomeJson = JsonSerializer.Serialize(existingResult) },
          cancellationToken);
        return existingResult;
      }

      int copyNumber = prior.Length == 0 ? 1 : prior.Max(static installation => installation.CopyNumber) + 1;
      DateTimeOffset now = operation.CreatedAtUtc;
      var revisionByHash = new Dictionary<string, WorkoutRevisionEntity>(StringComparer.Ordinal);
      foreach ((string key, WorkoutDefinition definition) in request.WorkoutsByKey.OrderBy(static item => item.Key, StringComparer.Ordinal))
      {
        string hash = WorkoutDefinitionCanonicalizer.ComputeSha256(definition);
        if (revisionByHash.ContainsKey(hash)) continue;
        Guid workoutId = Guid.NewGuid();
        var revision = new WorkoutRevisionEntity
        {
          Id = Guid.NewGuid(),
          WorkoutId = workoutId,
          RevisionNumber = 1,
          DefinitionJson = WorkoutDefinitionCanonicalizer.Serialize(definition),
          ContentSha256 = hash,
          CreatedAtUtc = now,
        };
        context.Workouts.Add(new WorkoutEntity
        {
          Id = workoutId,
          Name = definition.Title,
          Kind = nameof(WorkoutKind.PlanInternal),
          CreatedAtUtc = now,
          Revisions = [revision],
        });
        revisionByHash.Add(hash, revision);
      }

      Guid programId = Guid.NewGuid();
      Guid programRevisionId = Guid.NewGuid();
      var itemEntities = new List<WorkoutProgramItemEntity>(request.Template.SessionCount);
      foreach (PremadePlanSessionTemplate session in request.Template.Sessions)
      {
        WorkoutDefinition definition = request.WorkoutsByKey[session.WorkoutKey];
        WorkoutRevisionEntity revision = revisionByHash[WorkoutDefinitionCanonicalizer.ComputeSha256(definition)];
        itemEntities.Add(new WorkoutProgramItemEntity
        {
          Id = Guid.NewGuid(),
          WorkoutProgramRevisionId = programRevisionId,
          WorkoutRevisionId = revision.Id,
          Position = session.Position,
          WeekNumber = session.WeekNumber,
          SessionNumber = session.SessionNumber,
          Phase = session.Phase,
          Alternatives = session.AlternativeVariants.Select((alternative, index) =>
          {
            WorkoutDefinition alternativeDefinition = request.WorkoutsByKey[alternative.WorkoutKey];
            WorkoutRevisionEntity alternativeRevision = revisionByHash[WorkoutDefinitionCanonicalizer.ComputeSha256(alternativeDefinition)];
            return new WorkoutProgramItemAlternativeEntity
            {
              Id = Guid.NewGuid(),
              WorkoutProgramItemId = Guid.Empty,
              WorkoutRevisionId = alternativeRevision.Id,
              DisplayOrder = index + 1,
              Variant = alternative.Variant,
            };
          }).ToList(),
        });
      }

      foreach (WorkoutProgramItemEntity item in itemEntities)
        foreach (WorkoutProgramItemAlternativeEntity alternative in item.Alternatives)
          alternative.WorkoutProgramItemId = item.Id;

      var coreRevision = new WorkoutProgramRevision(
        programId,
        programRevisionId,
        1,
        copyNumber == 1 ? request.Template.Name : $"{request.Template.Name} · Copy {copyNumber}",
        request.Template.Description,
        request.Template.Goal,
        itemEntities.Select(item => new WorkoutProgramItem(
          item.Id, item.WorkoutRevisionId, item.Position, item.WeekNumber, item.SessionNumber, item.Phase,
          item.Alternatives.Select(alternative => new WorkoutProgramAlternative(
            alternative.WorkoutRevisionId, alternative.DisplayOrder, alternative.Variant)).ToArray())).ToArray(),
        request.Template.Id,
        request.Template.Version,
        request.UserProfileId);
      var programRevision = new WorkoutProgramRevisionEntity
      {
        Id = programRevisionId,
        WorkoutProgramId = programId,
        RevisionNumber = 1,
        Name = coreRevision.Name,
        Description = coreRevision.Description,
        Category = coreRevision.Category,
        ContentSha256 = WorkoutProgramCanonicalizer.ComputeSha256(coreRevision),
        TemplateId = coreRevision.TemplateId,
        TemplateVersion = coreRevision.TemplateVersion,
        OwnerProfileId = coreRevision.OwnerProfileId,
        CreatedAtUtc = now,
        Items = itemEntities,
      };
      context.WorkoutPrograms.Add(new WorkoutProgramEntity
      {
        Id = programId,
        CreatedAtUtc = now,
        Revisions = [programRevision],
      });
      var installation = new PremadePlanInstallationEntity
      {
        Id = Guid.NewGuid(),
        UserProfileId = request.UserProfileId,
        TemplateId = request.Template.Id,
        TemplateVersion = request.Template.Version,
        TemplateContentSha256 = request.Template.ContentSha256,
        CopyNumber = copyNumber,
        WorkoutProgramId = programId,
        CreatedAtUtc = now,
      };
      context.PremadePlanInstallations.Add(installation);
      var result = new PremadePlanMaterializationResult(
        Map(installation),
        programRevisionId,
        request.Template.SessionCount,
        revisionByHash.Count,
        AlreadyAdded: false,
        Replayed: false);
      await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
      await PersistenceReceipts.SaveAsync(
        context,
        contextFactory,
        operation with { OutcomeJson = JsonSerializer.Serialize(result) },
        cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return result;
    }
    finally
    {
      MaterializationGate.Release();
    }
  }

  private static PremadePlanInstallation Map(PremadePlanInstallationEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.TemplateId,
    entity.TemplateVersion,
    entity.TemplateContentSha256,
    entity.CopyNumber,
    entity.WorkoutProgramId,
    entity.CreatedAtUtc);
}
