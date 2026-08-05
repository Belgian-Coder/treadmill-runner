using Microsoft.EntityFrameworkCore;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Core.Workouts;

namespace TreadmillRunner.Infrastructure.Persistence;

public sealed record StoredWorkoutProgram(
  Guid Id,
  bool IsArchived,
  DateTimeOffset CreatedAtUtc,
  WorkoutProgramRevision CurrentRevision);

public sealed record StoredWorkoutProgramProgress(
  StoredWorkoutProgram Program,
  WorkoutProgramRun? Run,
  WorkoutProgramProgress? Progress);

public interface IWorkoutProgramStore
{
  Task<IReadOnlyList<StoredWorkoutProgramProgress>> ListAsync(Guid? userProfileId = null, CancellationToken cancellationToken = default);
  Task<StoredWorkoutProgram?> FindAsync(Guid programId, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRevision> CreateAsync(WorkoutProgramRevision revision, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRevision> AppendRevisionAsync(WorkoutProgramRevision revision, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> SetArchivedAsync(Guid programId, bool isArchived, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRun> StartAsync(Guid runId, Guid userProfileId, Guid programRevisionId, Guid? expectedActiveRunId, int? expectedActiveRunVersion, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRun> RestartAsync(Guid runId, Guid userProfileId, Guid programId, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<(WorkoutProgramRun Run, WorkoutProgramItem Item)?> ValidateSelectionAsync(Guid userProfileId, Guid runId, Guid itemId, Guid workoutRevisionId, CancellationToken cancellationToken = default);
}

public sealed class WorkoutProgramStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IWorkoutProgramStore
{
  public async Task<IReadOnlyList<StoredWorkoutProgramProgress>> ListAsync(
    Guid? userProfileId = null,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutProgramEntity[] programs = await ProgramQuery(context).AsNoTracking()
      .OrderBy(program => program.Revisions.OrderByDescending(revision => revision.RevisionNumber).Select(revision => revision.Name).First())
      .ToArrayAsync(cancellationToken);
    WorkoutProgramRunEntity[] activeRuns = userProfileId is null
      ? []
      : await context.WorkoutProgramRuns.AsNoTracking()
        .Where(run => run.UserProfileId == userProfileId && run.Status == nameof(WorkoutProgramRunStatus.Active))
        .ToArrayAsync(cancellationToken);

    var result = new List<StoredWorkoutProgramProgress>(programs.Length);
    foreach (WorkoutProgramEntity entity in programs)
    {
      WorkoutProgramRunEntity? runEntity = activeRuns.SingleOrDefault(run =>
        entity.Revisions.Any(revision => revision.Id == run.WorkoutProgramRevisionId));
      StoredWorkoutProgram program = runEntity is null
        ? MapProgram(entity)
        : MapProgram(entity, runEntity.WorkoutProgramRevisionId);
      WorkoutProgramRun? run = runEntity is null ? null : MapRun(runEntity);
      WorkoutProgramProgress? progress = runEntity is null
        ? null
        : await CalculateProgressAsync(context, program.CurrentRevision, runEntity.Id, cancellationToken);
      result.Add(new StoredWorkoutProgramProgress(program, run, progress));
    }

    return result;
  }

  public async Task<StoredWorkoutProgram?> FindAsync(Guid programId, CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutProgramEntity? entity = await ProgramQuery(context).AsNoTracking()
      .SingleOrDefaultAsync(program => program.Id == programId, cancellationToken);
    return entity is null ? null : MapProgram(entity);
  }

  public async Task<WorkoutProgramRevision> CreateAsync(
    WorkoutProgramRevision revision,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(revision);
    if (revision.RevisionNumber != 1) throw new ArgumentException("A new program must begin at revision one.", nameof(revision));
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await ValidateWorkoutReferencesAsync(context, revision, cancellationToken);
    var program = new WorkoutProgramEntity
    {
      Id = revision.ProgramId,
      CreatedAtUtc = nowUtc,
      Revisions = [CreateRevisionEntity(revision, nowUtc)],
    };
    context.WorkoutPrograms.Add(program);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return revision;
  }

  public async Task<WorkoutProgramRevision> AppendRevisionAsync(
    WorkoutProgramRevision revision,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentNullException.ThrowIfNull(revision);
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    WorkoutProgramEntity program = await context.WorkoutPrograms
      .Include(candidate => candidate.Revisions)
      .SingleOrDefaultAsync(candidate => candidate.Id == revision.ProgramId, cancellationToken)
      ?? throw new KeyNotFoundException($"Workout program {revision.ProgramId} was not found.");
    int expectedRevision = program.Revisions.Max(static candidate => candidate.RevisionNumber) + 1;
    if (revision.RevisionNumber != expectedRevision)
    {
      throw new DbUpdateConcurrencyException($"Expected program revision {expectedRevision}.");
    }
    await ValidateWorkoutReferencesAsync(context, revision, cancellationToken);
    context.WorkoutProgramRevisions.Add(CreateRevisionEntity(revision, operation.CreatedAtUtc));
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return revision;
  }

  public async Task<bool> SetArchivedAsync(
    Guid programId,
    bool isArchived,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    WorkoutProgramEntity? program = await context.WorkoutPrograms.SingleOrDefaultAsync(candidate => candidate.Id == programId, cancellationToken);
    if (program is null)
    {
      await PersistenceReceipts.SaveAsync(context, contextFactory, operation.ForNotFound(), cancellationToken);
      return false;
    }
    program.IsArchived = isArchived;
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    return true;
  }

  public async Task<WorkoutProgramRun> StartAsync(
    Guid runId,
    Guid userProfileId,
    Guid programRevisionId,
    Guid? expectedActiveRunId,
    int? expectedActiveRunVersion,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    if (runId == Guid.Empty) throw new ArgumentException("Program run ID is required.", nameof(runId));
    if (userProfileId == Guid.Empty) throw new ArgumentException("Profile ID is required.", nameof(userProfileId));
    if (programRevisionId == Guid.Empty) throw new ArgumentException("Program revision ID is required.", nameof(programRevisionId));
    if (expectedActiveRunId.HasValue != expectedActiveRunVersion.HasValue)
      throw new ArgumentException("Expected active run ID and version must be supplied together.");
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
    await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
    if (!await context.UserProfiles.AnyAsync(profile => profile.Id == userProfileId && !profile.IsArchived, cancellationToken))
    {
      throw new KeyNotFoundException($"Profile {userProfileId} was not found.");
    }
    if (!await context.WorkoutProgramRevisions.AnyAsync(revision => revision.Id == programRevisionId && !revision.WorkoutProgram.IsArchived, cancellationToken))
    {
      throw new KeyNotFoundException($"Program revision {programRevisionId} was not found.");
    }
    WorkoutProgramRunEntity[] activeRuns = await context.WorkoutProgramRuns
      .Where(run => run.UserProfileId == userProfileId && run.Status == nameof(WorkoutProgramRunStatus.Active))
      .ToArrayAsync(cancellationToken);
    if (activeRuns.Length > 1 || expectedActiveRunId is null && activeRuns.Length != 0 ||
        expectedActiveRunId is { } expectedRunId &&
        (activeRuns.Length != 1 || activeRuns[0].Id != expectedRunId || activeRuns[0].Version != expectedActiveRunVersion))
    {
      throw new DbUpdateConcurrencyException("The active training plan changed after confirmation was shown.");
    }
    await context.WorkoutProgramRuns
      .Where(run => run.UserProfileId == userProfileId && run.Status == nameof(WorkoutProgramRunStatus.Active))
      .ExecuteUpdateAsync(setters => setters
        .SetProperty(run => run.Status, nameof(WorkoutProgramRunStatus.Abandoned))
        .SetProperty(run => run.EndedAtUtc, nowUtc)
        .SetProperty(run => run.Version, run => run.Version + 1), cancellationToken);
    var entity = new WorkoutProgramRunEntity
    {
      Id = runId,
      UserProfileId = userProfileId,
      WorkoutProgramRevisionId = programRevisionId,
      Status = nameof(WorkoutProgramRunStatus.Active),
      StartedAtUtc = nowUtc,
      Version = 1,
    };
    context.WorkoutProgramRuns.Add(entity);
    await PersistenceReceipts.SaveAsync(context, contextFactory, operation, cancellationToken);
    await transaction.CommitAsync(cancellationToken);
    return MapRun(entity);
  }

  public async Task<WorkoutProgramRun> RestartAsync(
    Guid runId,
    Guid userProfileId,
    Guid programId,
    DateTimeOffset nowUtc,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    StoredWorkoutProgram program = await FindAsync(programId, cancellationToken)
      ?? throw new KeyNotFoundException($"Workout program {programId} was not found.");
    WorkoutProgramRunEntity? active = null;
    await using (var context = await contextFactory.CreateDbContextAsync(cancellationToken))
    {
      active = await context.WorkoutProgramRuns.AsNoTracking().SingleOrDefaultAsync(
        run => run.UserProfileId == userProfileId && run.Status == nameof(WorkoutProgramRunStatus.Active), cancellationToken);
    }
    return await StartAsync(runId, userProfileId, program.CurrentRevision.RevisionId, active?.Id, active?.Version, nowUtc, operation, cancellationToken);
  }

  public async Task<(WorkoutProgramRun Run, WorkoutProgramItem Item)?> ValidateSelectionAsync(
    Guid userProfileId,
    Guid runId,
    Guid itemId,
    Guid workoutRevisionId,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutProgramRunEntity? runEntity = await context.WorkoutProgramRuns.AsNoTracking()
      .SingleOrDefaultAsync(run => run.Id == runId && run.UserProfileId == userProfileId && run.Status == nameof(WorkoutProgramRunStatus.Active), cancellationToken);
    if (runEntity is null) return null;
    WorkoutProgramRevisionEntity revisionEntity = await context.WorkoutProgramRevisions.AsNoTracking()
      .Include(revision => revision.Items)
      .SingleAsync(revision => revision.Id == runEntity.WorkoutProgramRevisionId, cancellationToken);
    WorkoutProgramRevision revision = MapRevision(revisionEntity);
    WorkoutProgramProgress progress = await CalculateProgressAsync(context, revision, runEntity.Id, cancellationToken);
    WorkoutProgramItem? next = progress.NextItem;
    return next is not null && next.Id == itemId && next.WorkoutRevisionId == workoutRevisionId
      ? (MapRun(runEntity), next)
      : null;
  }

  private static IQueryable<WorkoutProgramEntity> ProgramQuery(TreadmillRunnerDbContext context) =>
    context.WorkoutPrograms
      .Include(program => program.Revisions)
      .ThenInclude(revision => revision.Items);

  private static async Task ValidateWorkoutReferencesAsync(
    TreadmillRunnerDbContext context,
    WorkoutProgramRevision revision,
    CancellationToken cancellationToken)
  {
    Guid[] requested = revision.Items.Select(static item => item.WorkoutRevisionId).Distinct().ToArray();
    int found = await context.WorkoutRevisions.CountAsync(candidate =>
      requested.Contains(candidate.Id) && candidate.Workout.Kind == nameof(WorkoutKind.Structured), cancellationToken);
    if (found != requested.Length) throw new ArgumentException("One or more workout revisions were not found.", nameof(revision));
  }

  private static async Task<WorkoutProgramProgress> CalculateProgressAsync(
    TreadmillRunnerDbContext context,
    WorkoutProgramRevision revision,
    Guid runId,
    CancellationToken cancellationToken)
  {
    var rows = await context.WorkoutSessions.AsNoTracking()
      .Where(session => session.WorkoutProgramRunId == runId && session.WorkoutProgramItemId != null && session.EndedAtUtc != null)
      .Select(session => new
      {
        ItemId = session.WorkoutProgramItemId!.Value,
        session.State,
        EndedAt = session.EndedAtUtc!.Value,
      })
      .ToArrayAsync(cancellationToken);
    WorkoutProgramSessionResult[] results = rows.Select(row => new WorkoutProgramSessionResult(
      row.ItemId,
      Enum.Parse<SessionState>(row.State),
      row.EndedAt)).ToArray();
    return WorkoutProgramProgressCalculator.Calculate(revision, results);
  }

  private static WorkoutProgramRevisionEntity CreateRevisionEntity(WorkoutProgramRevision revision, DateTimeOffset nowUtc) => new()
  {
    Id = revision.RevisionId,
    WorkoutProgramId = revision.ProgramId,
    RevisionNumber = revision.RevisionNumber,
    Name = revision.Name,
    Description = revision.Description,
    Category = revision.Category,
    ContentSha256 = WorkoutProgramCanonicalizer.ComputeSha256(revision),
    CreatedAtUtc = nowUtc,
    Items = revision.Items.Select(item => new WorkoutProgramItemEntity
    {
      Id = item.Id,
      WorkoutProgramRevisionId = revision.RevisionId,
      WorkoutRevisionId = item.WorkoutRevisionId,
      Position = item.Position,
    }).ToList(),
  };

  private static StoredWorkoutProgram MapProgram(WorkoutProgramEntity entity, Guid? selectedRevisionId = null)
  {
    WorkoutProgramRevisionEntity latest = selectedRevisionId is { } revisionId
      ? entity.Revisions.Single(revision => revision.Id == revisionId)
      : entity.Revisions.MaxBy(static revision => revision.RevisionNumber)
      ?? throw new InvalidOperationException("Workout program has no revision.");
    return new StoredWorkoutProgram(entity.Id, entity.IsArchived, entity.CreatedAtUtc, MapRevision(latest));
  }

  private static WorkoutProgramRevision MapRevision(WorkoutProgramRevisionEntity entity) => new(
    entity.WorkoutProgramId,
    entity.Id,
    entity.RevisionNumber,
    entity.Name,
    entity.Description,
    entity.Category,
    entity.Items.OrderBy(static item => item.Position)
      .Select(item => new WorkoutProgramItem(item.Id, item.WorkoutRevisionId, item.Position)).ToArray());

  private static WorkoutProgramRun MapRun(WorkoutProgramRunEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.WorkoutProgramRevisionId,
    Enum.Parse<WorkoutProgramRunStatus>(entity.Status),
    entity.StartedAtUtc,
    entity.EndedAtUtc,
    entity.Version);
}

public static class WorkoutProgramCanonicalizer
{
  public static string ComputeSha256(WorkoutProgramRevision revision)
  {
    string value = string.Join('\n', new[]
    {
      revision.Name,
      revision.Description ?? string.Empty,
      revision.Category,
      string.Join(',', revision.Items.OrderBy(static item => item.Position).Select(static item => item.WorkoutRevisionId.ToString("D"))),
    });
    return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value)));
  }
}
