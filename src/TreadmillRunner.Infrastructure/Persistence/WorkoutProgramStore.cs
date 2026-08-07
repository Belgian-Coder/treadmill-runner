using Microsoft.EntityFrameworkCore;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using TreadmillRunner.Core.Calendar;
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
  WorkoutProgramProgress? Progress,
  IReadOnlyList<WorkoutProgramScheduleOverride>? ScheduleOverrides = null,
  IReadOnlyList<WorkoutProgramExtraOccurrence>? ExtraOccurrences = null,
  IReadOnlySet<Guid>? CompletedItemIds = null);

public sealed record WorkoutProgramScheduleImpact(
  Guid ProgramItemId,
  int Position,
  DateOnly? CurrentDate,
  DateOnly? NewDate,
  bool IsRepeat = false);

public sealed record WorkoutProgramScheduleChangePreview(
  Guid RunId,
  Guid ProgramItemId,
  WorkoutProgramScheduleAction Action,
  int RunVersion,
  bool CanApply,
  string Message,
  IReadOnlyList<WorkoutProgramScheduleImpact> Impacts,
  IReadOnlyList<DateOnly> CollisionDates);

public sealed record WorkoutProgramDefaultDaysImpact(
  Guid ProgramItemId,
  int Position,
  DateOnly CurrentDate,
  DateOnly NewDate);

public sealed record WorkoutProgramDefaultDaysPreview(
  Guid RunId,
  int RunVersion,
  int CurrentWeekdayMask,
  int NewWeekdayMask,
  DateOnly EffectiveDate,
  bool CanApply,
  string Message,
  string Revision,
  IReadOnlyList<WorkoutProgramDefaultDaysImpact> Impacts,
  IReadOnlyList<DateOnly> CollisionDates,
  int PreservedExceptionCount);

public interface IWorkoutProgramStore
{
  Task<IReadOnlyList<StoredWorkoutProgramProgress>> ListAsync(Guid? userProfileId = null, CancellationToken cancellationToken = default);
  Task<StoredWorkoutProgram?> FindAsync(Guid programId, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRevision> CreateAsync(WorkoutProgramRevision revision, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRevision> AppendRevisionAsync(WorkoutProgramRevision revision, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<bool> SetArchivedAsync(Guid programId, bool isArchived, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRun> StartAsync(Guid runId, Guid userProfileId, Guid programRevisionId, Guid? expectedActiveRunId, int? expectedActiveRunVersion, WorkoutProgramSchedule? schedule, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramRun> RestartAsync(Guid runId, Guid userProfileId, Guid programId, DateTimeOffset nowUtc, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<(WorkoutProgramRun Run, WorkoutProgramItem Item)?> ValidateSelectionAsync(Guid userProfileId, Guid runId, Guid itemId, Guid workoutRevisionId, CancellationToken cancellationToken = default);
  Task<WorkoutProgramScheduleChangePreview> PreviewScheduleChangeAsync(Guid userProfileId, Guid runId, Guid itemId, WorkoutProgramScheduleAction action, DateOnly? targetDate, CancellationToken cancellationToken = default);
  Task<WorkoutProgramScheduleChangePreview> ApplyScheduleChangeAsync(Guid userProfileId, Guid runId, Guid itemId, WorkoutProgramScheduleAction action, DateOnly? targetDate, int expectedRunVersion, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
  Task<WorkoutProgramDefaultDaysPreview> PreviewDefaultDaysChangeAsync(Guid userProfileId, Guid runId, WeekdayFlags weekdays, DateOnly effectiveDate, DateOnly today, CancellationToken cancellationToken = default);
  Task<WorkoutProgramDefaultDaysPreview> ApplyDefaultDaysChangeAsync(Guid userProfileId, Guid runId, WeekdayFlags weekdays, DateOnly effectiveDate, DateOnly today, int expectedRunVersion, string expectedRevision, PersistenceWriteOperation operation, CancellationToken cancellationToken = default);
}

public sealed class WorkoutProgramStore(
  IDbContextFactory<TreadmillRunnerDbContext> contextFactory) : IWorkoutProgramStore
{
  private static readonly SemaphoreSlim ScheduleChangeGate = new(1, 1);
  private static readonly JsonSerializerOptions ScheduleReceiptJsonOptions = new(JsonSerializerDefaults.Web)
  {
    Converters = { new JsonStringEnumConverter() },
  };
  public async Task<IReadOnlyList<StoredWorkoutProgramProgress>> ListAsync(
    Guid? userProfileId = null,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    WorkoutProgramEntity[] programs = await ProgramQuery(context).AsNoTracking()
      .Where(program => userProfileId == null ||
        program.Revisions.OrderByDescending(revision => revision.RevisionNumber)
          .Select(revision => revision.OwnerProfileId).First() == null ||
        program.Revisions.OrderByDescending(revision => revision.RevisionNumber)
          .Select(revision => revision.OwnerProfileId).First() == userProfileId)
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
      IReadOnlyList<WorkoutProgramScheduleOverride>? scheduleOverrides = runEntity is null
        ? null
        : await LoadOverridesAsync(context, runEntity.Id, cancellationToken);
      IReadOnlyList<WorkoutProgramExtraOccurrence>? extraOccurrences = runEntity is null
        ? null
        : await LoadExtrasAsync(context, runEntity.Id, cancellationToken);
      IReadOnlySet<Guid>? completedItemIds = runEntity is null
        ? null
        : await LoadCompletedItemIdsAsync(context, runEntity.Id, cancellationToken);
      result.Add(new StoredWorkoutProgramProgress(program, run, progress, scheduleOverrides, extraOccurrences, completedItemIds));
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
    WorkoutProgramSchedule? schedule,
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
    if (!await context.WorkoutProgramRevisions.AnyAsync(revision =>
      revision.Id == programRevisionId && !revision.WorkoutProgram.IsArchived &&
      (revision.OwnerProfileId == null || revision.OwnerProfileId == userProfileId), cancellationToken))
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
      ScheduledStartDate = schedule?.StartDate,
      ScheduledWeekdayMask = (int)(schedule?.Weekdays ?? WeekdayFlags.None),
      ScheduleTimeZoneId = schedule?.TimeZoneId,
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
    return await StartAsync(runId, userProfileId, program.CurrentRevision.RevisionId, active?.Id, active?.Version, null, nowUtc, operation, cancellationToken);
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

  public async Task<WorkoutProgramScheduleChangePreview> PreviewScheduleChangeAsync(
    Guid userProfileId,
    Guid runId,
    Guid itemId,
    WorkoutProgramScheduleAction action,
    DateOnly? targetDate,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await BuildSchedulePreviewAsync(context, userProfileId, runId, itemId, action, targetDate, cancellationToken);
  }

  public async Task<WorkoutProgramScheduleChangePreview> ApplyScheduleChangeAsync(
    Guid userProfileId,
    Guid runId,
    Guid itemId,
    WorkoutProgramScheduleAction action,
    DateOnly? targetDate,
    int expectedRunVersion,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    await ScheduleChangeGate.WaitAsync(cancellationToken);
    try
    {
      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
      await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
      WorkoutProgramScheduleChangePreview preview = await BuildSchedulePreviewAsync(
        context, userProfileId, runId, itemId, action, targetDate, cancellationToken);
      if (!preview.CanApply) throw new ArgumentException(preview.Message, nameof(action));
      WorkoutProgramRunEntity run = await context.WorkoutProgramRuns
        .SingleAsync(candidate => candidate.Id == runId && candidate.UserProfileId == userProfileId, cancellationToken);
      if (run.Version != expectedRunVersion || preview.RunVersion != expectedRunVersion)
        throw new DbUpdateConcurrencyException("The training plan schedule changed after the preview was shown.");

      if (action == WorkoutProgramScheduleAction.Restore)
      {
        await context.WorkoutProgramScheduleOverrides
          .Where(candidate => candidate.WorkoutProgramRunId == runId && candidate.WorkoutProgramItemId == itemId)
          .ExecuteDeleteAsync(cancellationToken);
      }
      else if (action == WorkoutProgramScheduleAction.Skip)
      {
        await UpsertOverrideAsync(context, runId, itemId, targetDate: null, isSkipped: true, operation.CreatedAtUtc, cancellationToken);
      }
      else
      {
        foreach (WorkoutProgramScheduleImpact impact in preview.Impacts.Where(static impact => !impact.IsRepeat))
          await UpsertOverrideAsync(context, runId, impact.ProgramItemId, impact.NewDate, isSkipped: false, operation.CreatedAtUtc, cancellationToken);
        if (action is WorkoutProgramScheduleAction.Repeat or WorkoutProgramScheduleAction.RepeatAndShift)
        {
          context.WorkoutProgramExtraOccurrences.Add(new WorkoutProgramExtraOccurrenceEntity
          {
            Id = Guid.NewGuid(),
            WorkoutProgramRunId = runId,
            WorkoutProgramItemId = itemId,
            Date = targetDate!.Value,
            CreatedAtUtc = operation.CreatedAtUtc,
          });
        }
      }

      run.Version++;
      WorkoutProgramScheduleChangePreview outcome = preview with { RunVersion = run.Version };
      await PersistenceReceipts.SaveAsync(
        context,
        contextFactory,
        operation with { OutcomeJson = JsonSerializer.Serialize(outcome, ScheduleReceiptJsonOptions) },
        cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return outcome;
    }
    finally
    {
      ScheduleChangeGate.Release();
    }
  }

  public async Task<WorkoutProgramDefaultDaysPreview> PreviewDefaultDaysChangeAsync(
    Guid userProfileId,
    Guid runId,
    WeekdayFlags weekdays,
    DateOnly effectiveDate,
    DateOnly today,
    CancellationToken cancellationToken = default)
  {
    await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
    return await BuildDefaultDaysPreviewAsync(
      context, userProfileId, runId, weekdays, effectiveDate, today, cancellationToken);
  }

  public async Task<WorkoutProgramDefaultDaysPreview> ApplyDefaultDaysChangeAsync(
    Guid userProfileId,
    Guid runId,
    WeekdayFlags weekdays,
    DateOnly effectiveDate,
    DateOnly today,
    int expectedRunVersion,
    string expectedRevision,
    PersistenceWriteOperation operation,
    CancellationToken cancellationToken = default)
  {
    ArgumentException.ThrowIfNullOrWhiteSpace(expectedRevision);
    await ScheduleChangeGate.WaitAsync(cancellationToken);
    try
    {
      await using var context = await contextFactory.CreateDbContextAsync(cancellationToken);
      await PersistenceReceipts.ThrowIfCompletedAsync(context, operation, cancellationToken);
      await using var transaction = await context.Database.BeginTransactionAsync(cancellationToken);
      WorkoutProgramDefaultDaysPreview preview = await BuildDefaultDaysPreviewAsync(
        context, userProfileId, runId, weekdays, effectiveDate, today, cancellationToken);
      if (!preview.CanApply) throw new ArgumentException(preview.Message, nameof(weekdays));
      if (preview.RunVersion != expectedRunVersion || !string.Equals(preview.Revision, expectedRevision, StringComparison.Ordinal))
        throw new DbUpdateConcurrencyException("The training-day preview is no longer current. Review it again.");

      WorkoutProgramRunEntity run = await context.WorkoutProgramRuns
        .SingleAsync(candidate => candidate.Id == runId && candidate.UserProfileId == userProfileId, cancellationToken);
      if (run.Version != expectedRunVersion)
        throw new DbUpdateConcurrencyException("The training plan changed after the preview was shown.");

      WorkoutProgramRevisionEntity revisionEntity = await context.WorkoutProgramRevisions.AsNoTracking()
        .Include(revision => revision.Items)
        .SingleAsync(revision => revision.Id == run.WorkoutProgramRevisionId, cancellationToken);
      WorkoutProgramRevision revision = MapRevision(revisionEntity);
      WorkoutProgramRun currentRun = MapRun(run);
      IReadOnlyList<WorkoutProgramScheduleOverride> overrides = await LoadOverridesAsync(context, runId, cancellationToken);
      IReadOnlyList<WorkoutProgramExtraOccurrence> extras = await LoadExtrasAsync(context, runId, cancellationToken);
      IReadOnlyList<ScheduledWorkoutProgramItem> current = WorkoutProgramScheduleProjector.ProjectAll(
        revision, currentRun, overrides, extras);
      HashSet<Guid> impactedIds = preview.Impacts.Select(static impact => impact.ProgramItemId).ToHashSet();
      HashSet<Guid> existingOverrideIds = overrides.Select(static item => item.ProgramItemId).ToHashSet();

      foreach (ScheduledWorkoutProgramItem occurrence in current.Where(static item => !item.IsRepeat))
      {
        if (impactedIds.Contains(occurrence.Item.Id)) continue;
        if (!existingOverrideIds.Contains(occurrence.Item.Id))
          await UpsertOverrideAsync(context, runId, occurrence.Item.Id, occurrence.Date, false, operation.CreatedAtUtc, cancellationToken);
      }
      foreach (WorkoutProgramDefaultDaysImpact impact in preview.Impacts)
        await UpsertOverrideAsync(context, runId, impact.ProgramItemId, impact.NewDate, false, operation.CreatedAtUtc, cancellationToken);

      DateOnly validStart = NextSelectedDate(run.ScheduledStartDate!.Value, weekdays);
      run.ScheduledStartDate = validStart;
      run.ScheduledWeekdayMask = (int)weekdays;
      run.Version++;
      WorkoutProgramDefaultDaysPreview outcome = preview with { RunVersion = run.Version };
      await PersistenceReceipts.SaveAsync(
        context,
        contextFactory,
        operation with { OutcomeJson = JsonSerializer.Serialize(outcome, ScheduleReceiptJsonOptions) },
        cancellationToken);
      await transaction.CommitAsync(cancellationToken);
      return outcome;
    }
    finally
    {
      ScheduleChangeGate.Release();
    }
  }

  private static async Task<WorkoutProgramDefaultDaysPreview> BuildDefaultDaysPreviewAsync(
    TreadmillRunnerDbContext context,
    Guid userProfileId,
    Guid runId,
    WeekdayFlags weekdays,
    DateOnly effectiveDate,
    DateOnly today,
    CancellationToken cancellationToken)
  {
    WorkoutProgramRunEntity runEntity = await context.WorkoutProgramRuns.AsNoTracking()
      .SingleOrDefaultAsync(run => run.Id == runId && run.UserProfileId == userProfileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Training plan run {runId} was not found for this runner.");
    WeekdayFlags currentWeekdays = (WeekdayFlags)runEntity.ScheduledWeekdayMask;
    if (runEntity.Status != nameof(WorkoutProgramRunStatus.Active) || runEntity.ScheduledStartDate is null)
      return Blocked("Only an active scheduled training plan can change its default days.");
    if (effectiveDate < today) return Blocked("Choose today or a future effective date.");
    if ((int)weekdays is <= 0 or > 127) return Blocked("Select valid training weekdays.");
    int requiredDays = WorkoutProgramScheduleProjector.CountSelectedDays(currentWeekdays);
    if (WorkoutProgramScheduleProjector.CountSelectedDays(weekdays) != requiredDays)
      return Blocked($"Select exactly {requiredDays} training day(s).");
    if (weekdays == currentWeekdays) return Blocked("Choose a different weekly training rhythm.");

    WorkoutProgramRevisionEntity revisionEntity = await context.WorkoutProgramRevisions.AsNoTracking()
      .Include(revision => revision.Items)
      .SingleAsync(revision => revision.Id == runEntity.WorkoutProgramRevisionId, cancellationToken);
    WorkoutProgramRevision revision = MapRevision(revisionEntity);
    WorkoutProgramRun run = MapRun(runEntity);
    IReadOnlyList<WorkoutProgramScheduleOverride> overrides = await LoadOverridesAsync(context, runId, cancellationToken);
    IReadOnlyList<WorkoutProgramExtraOccurrence> extras = await LoadExtrasAsync(context, runId, cancellationToken);
    IReadOnlyList<ScheduledWorkoutProgramItem> effective = WorkoutProgramScheduleProjector.ProjectAll(
      revision, run, overrides, extras);
    HashSet<Guid> completed = (await LoadCompletedItemIdsAsync(context, runId, cancellationToken)).ToHashSet();
    HashSet<Guid> explicitOverrides = overrides.Select(static item => item.ProgramItemId).ToHashSet();
    ScheduledWorkoutProgramItem[] eligible = effective
      .Where(item => !item.IsRepeat && item.Date >= effectiveDate &&
        !completed.Contains(item.Item.Id) && !explicitOverrides.Contains(item.Item.Id))
      .OrderBy(static item => item.Item.Position)
      .ToArray();
    if (eligible.Length == 0) return Blocked("No future generated sessions are eligible; completed runs and explicit exceptions are preserved.");

    var impacts = new List<WorkoutProgramDefaultDaysImpact>(eligible.Length);
    DateOnly firstEligibleDate = eligible[0].Date > effectiveDate ? eligible[0].Date : effectiveDate;
    DateOnly cursor = NextSelectedDate(firstEligibleDate, weekdays);
    foreach (ScheduledWorkoutProgramItem occurrence in eligible)
    {
      impacts.Add(new(occurrence.Item.Id, occurrence.Item.Position, occurrence.Date, cursor));
      cursor = NextSelectedDate(cursor.AddDays(1), weekdays);
    }
    HashSet<Guid> affectedIds = impacts.Select(static impact => impact.ProgramItemId).ToHashSet();
    HashSet<DateOnly> occupied = effective
      .Where(item => item.IsRepeat || !affectedIds.Contains(item.Item.Id))
      .Select(static item => item.Date)
      .ToHashSet();
    DateOnly[] collisions = impacts.Where(impact => occupied.Contains(impact.NewDate))
      .Select(static impact => impact.NewDate).Distinct().Order().ToArray();
    int preserved = effective.Count(item => item.IsRepeat || completed.Contains(item.Item.Id) || explicitOverrides.Contains(item.Item.Id) || item.Date < effectiveDate)
      + overrides.Count(static item => item.IsSkipped);
    string revisionValue = ComputeDefaultDaysRevision(
      runId, runEntity.Version, (int)currentWeekdays, (int)weekdays, effectiveDate, impacts, collisions);
    string message = $"{impacts.Count} future session(s) will move to the new weekly rhythm. {preserved} completed, earlier, repeated, or explicitly adjusted occurrence(s) stay unchanged.";
    if (collisions.Length > 0) message += $" {collisions.Length} date(s) will contain more than one session; nothing is overwritten.";
    return new(runId, runEntity.Version, (int)currentWeekdays, (int)weekdays, effectiveDate, true,
      message, revisionValue, impacts, collisions, preserved);

    WorkoutProgramDefaultDaysPreview Blocked(string reason) => new(
      runId, runEntity.Version, (int)currentWeekdays, (int)weekdays, effectiveDate, false,
      reason, string.Empty, [], [], 0);
  }

  private static DateOnly NextSelectedDate(DateOnly date, WeekdayFlags weekdays)
  {
    DateOnly candidate = date;
    while (!weekdays.HasFlag(WorkoutProgramScheduleProjector.ToFlag(candidate.DayOfWeek)))
      candidate = candidate.AddDays(1);
    return candidate;
  }

  private static string ComputeDefaultDaysRevision(
    Guid runId,
    int version,
    int currentMask,
    int newMask,
    DateOnly effectiveDate,
    IReadOnlyList<WorkoutProgramDefaultDaysImpact> impacts,
    IReadOnlyList<DateOnly> collisions)
  {
    string canonical = string.Join('|',
      runId.ToString("N"), version, currentMask, newMask, effectiveDate.ToString("yyyy-MM-dd"),
      string.Join(';', impacts.Select(static impact => $"{impact.ProgramItemId:N}:{impact.Position}:{impact.CurrentDate:yyyy-MM-dd}:{impact.NewDate:yyyy-MM-dd}")),
      string.Join(';', collisions.Select(static date => date.ToString("yyyy-MM-dd"))));
    return Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(canonical)));
  }

  private static async Task<WorkoutProgramScheduleChangePreview> BuildSchedulePreviewAsync(
    TreadmillRunnerDbContext context,
    Guid userProfileId,
    Guid runId,
    Guid itemId,
    WorkoutProgramScheduleAction action,
    DateOnly? targetDate,
    CancellationToken cancellationToken)
  {
    WorkoutProgramRunEntity runEntity = await context.WorkoutProgramRuns.AsNoTracking()
      .SingleOrDefaultAsync(run => run.Id == runId && run.UserProfileId == userProfileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Training plan run {runId} was not found for this runner.");
    if (runEntity.Status != nameof(WorkoutProgramRunStatus.Active) || runEntity.ScheduledStartDate is null)
      return Blocked("Only an active scheduled training plan can be adjusted.");
    WorkoutProgramRevisionEntity revisionEntity = await context.WorkoutProgramRevisions.AsNoTracking()
      .Include(revision => revision.Items)
      .SingleAsync(revision => revision.Id == runEntity.WorkoutProgramRevisionId, cancellationToken);
    WorkoutProgramRevision revision = MapRevision(revisionEntity);
    WorkoutProgramItem? selectedItem = revision.Items.SingleOrDefault(item => item.Id == itemId);
    if (selectedItem is null) throw new KeyNotFoundException($"Training plan item {itemId} was not found.");
    WorkoutProgramRun run = MapRun(runEntity);
    IReadOnlyList<WorkoutProgramScheduleOverride> overrides = await LoadOverridesAsync(context, runId, cancellationToken);
    IReadOnlyList<WorkoutProgramExtraOccurrence> extras = await LoadExtrasAsync(context, runId, cancellationToken);
    IReadOnlyList<ScheduledWorkoutProgramItem> effective = WorkoutProgramScheduleProjector.ProjectAll(revision, run, overrides, extras);
    IReadOnlyList<ScheduledWorkoutProgramItem> original = WorkoutProgramScheduleProjector.ProjectAll(revision, run);
    HashSet<Guid> completed = await context.WorkoutSessions.AsNoTracking()
      .Where(session => session.WorkoutProgramRunId == runId && session.WorkoutProgramItemId != null && session.State == nameof(SessionState.Completed))
      .Select(session => session.WorkoutProgramItemId!.Value)
      .ToHashSetAsync(cancellationToken);
    bool selectedCompleted = completed.Contains(itemId);
    ScheduledWorkoutProgramItem? current = effective.FirstOrDefault(item => !item.IsRepeat && item.Item.Id == itemId);
    ScheduledWorkoutProgramItem originalItem = original.Single(item => item.Item.Id == itemId);
    var impacts = new List<WorkoutProgramScheduleImpact>();

    if ((action is WorkoutProgramScheduleAction.MoveOne or WorkoutProgramScheduleAction.MoveFollowing or
         WorkoutProgramScheduleAction.Repeat or WorkoutProgramScheduleAction.RepeatAndShift) && targetDate is null)
      return Blocked("Choose a target date.");
    if (targetDate is { } requestedDate && Math.Abs(requestedDate.DayNumber - originalItem.Date.DayNumber) > 366)
      return Blocked("Choose a date within one year of the planned session.");
    if (action is WorkoutProgramScheduleAction.MoveOne or WorkoutProgramScheduleAction.MoveFollowing or WorkoutProgramScheduleAction.Skip or WorkoutProgramScheduleAction.Restore)
    {
      if (selectedCompleted) return Blocked("Completed plan sessions cannot be moved, skipped, or restored. Use Repeat workout instead.");
      if (action != WorkoutProgramScheduleAction.Restore && current is null && action != WorkoutProgramScheduleAction.Skip)
        return Blocked("This session is currently skipped. Restore it before moving it.");
    }
    if (action is WorkoutProgramScheduleAction.Repeat or WorkoutProgramScheduleAction.RepeatAndShift && !selectedCompleted)
      return Blocked("An incomplete session should be rescheduled, not repeated.");

    switch (action)
    {
      case WorkoutProgramScheduleAction.MoveOne:
        if (targetDate == current!.Date) return Blocked("Choose a different date for the moved session.");
        impacts.Add(new(itemId, selectedItem.Position, current.Date, targetDate));
        break;
      case WorkoutProgramScheduleAction.MoveFollowing:
        {
          if (targetDate == current!.Date) return Blocked("Choose a different date for the moved sessions.");
          int offset = targetDate!.Value.DayNumber - current.Date.DayNumber;
          foreach (ScheduledWorkoutProgramItem occurrence in effective.Where(item => !item.IsRepeat && item.Item.Position >= selectedItem.Position))
          {
            if (completed.Contains(occurrence.Item.Id)) return Blocked("A completed later session prevents shifting this part of the plan.");
            impacts.Add(new(occurrence.Item.Id, occurrence.Item.Position, occurrence.Date, occurrence.Date.AddDays(offset)));
          }
          break;
        }
      case WorkoutProgramScheduleAction.Skip:
        impacts.Add(new(itemId, selectedItem.Position, current?.Date ?? originalItem.Date, null));
        break;
      case WorkoutProgramScheduleAction.Restore:
        if (!overrides.Any(item => item.ProgramItemId == itemId)) return Blocked("This session already uses its original schedule.");
        impacts.Add(new(itemId, selectedItem.Position, current?.Date, originalItem.Date));
        break;
      case WorkoutProgramScheduleAction.Repeat:
        impacts.Add(new(itemId, selectedItem.Position, null, targetDate, IsRepeat: true));
        break;
      case WorkoutProgramScheduleAction.RepeatAndShift:
        {
          impacts.Add(new(itemId, selectedItem.Position, null, targetDate, IsRepeat: true));
          DateOnly nextTrainingDate = targetDate!.Value.AddDays(1);
          while (!run.Schedule!.Weekdays.HasFlag(WorkoutProgramScheduleProjector.ToFlag(nextTrainingDate.DayOfWeek)))
            nextTrainingDate = nextTrainingDate.AddDays(1);
          int shiftDays = nextTrainingDate.DayNumber - targetDate.Value.DayNumber;
          foreach (ScheduledWorkoutProgramItem occurrence in effective.Where(item => !item.IsRepeat && item.Item.Position > selectedItem.Position && !completed.Contains(item.Item.Id)))
            impacts.Add(new(occurrence.Item.Id, occurrence.Item.Position, occurrence.Date, occurrence.Date.AddDays(shiftDays)));
          break;
        }
    }

    HashSet<Guid> affectedItems = impacts.Where(static impact => !impact.IsRepeat).Select(static impact => impact.ProgramItemId).ToHashSet();
    HashSet<DateOnly> occupied = effective
      .Where(item => item.IsRepeat || !affectedItems.Contains(item.Item.Id))
      .Select(static item => item.Date)
      .ToHashSet();
    DateOnly[] collisions = impacts.Where(impact => impact.NewDate is { } date && occupied.Contains(date))
      .Select(impact => impact.NewDate!.Value).Distinct().Order().ToArray();
    string message = action switch
    {
      WorkoutProgramScheduleAction.Skip => "This plan step will be skipped and progression will continue to the next step.",
      WorkoutProgramScheduleAction.Restore => "This session will return to its original planned date.",
      WorkoutProgramScheduleAction.Repeat => "An extra attempt will be added without changing later sessions.",
      WorkoutProgramScheduleAction.RepeatAndShift => "An extra attempt will be inserted and the remaining incomplete plan will move forward.",
      WorkoutProgramScheduleAction.MoveFollowing => $"{impacts.Count} session(s) will move by the same number of days.",
      _ => "Only this session will move; later sessions keep their dates.",
    };
    if (collisions.Length > 0) message += $" Warning: {collisions.Length} date(s) will contain more than one session.";
    return new(runId, itemId, action, runEntity.Version, true, message, impacts, collisions);

    WorkoutProgramScheduleChangePreview Blocked(string reason) =>
      new(runId, itemId, action, runEntity.Version, false, reason, [], []);
  }

  private static async Task UpsertOverrideAsync(
    TreadmillRunnerDbContext context,
    Guid runId,
    Guid itemId,
    DateOnly? targetDate,
    bool isSkipped,
    DateTimeOffset now,
    CancellationToken cancellationToken)
  {
    WorkoutProgramScheduleOverrideEntity? entity = await context.WorkoutProgramScheduleOverrides
      .SingleOrDefaultAsync(item => item.WorkoutProgramRunId == runId && item.WorkoutProgramItemId == itemId, cancellationToken);
    if (entity is null)
    {
      context.WorkoutProgramScheduleOverrides.Add(new WorkoutProgramScheduleOverrideEntity
      {
        Id = Guid.NewGuid(),
        WorkoutProgramRunId = runId,
        WorkoutProgramItemId = itemId,
        TargetDate = targetDate,
        IsSkipped = isSkipped,
        UpdatedAtUtc = now,
      });
    }
    else
    {
      entity.TargetDate = targetDate; entity.IsSkipped = isSkipped; entity.UpdatedAtUtc = now;
    }
  }

  private static async Task<IReadOnlyList<WorkoutProgramScheduleOverride>> LoadOverridesAsync(
    TreadmillRunnerDbContext context,
    Guid runId,
    CancellationToken cancellationToken) =>
    await context.WorkoutProgramScheduleOverrides.AsNoTracking()
      .Where(item => item.WorkoutProgramRunId == runId)
      .Select(item => new WorkoutProgramScheduleOverride(item.WorkoutProgramItemId, item.TargetDate, item.IsSkipped))
      .ToArrayAsync(cancellationToken);

  private static async Task<IReadOnlyList<WorkoutProgramExtraOccurrence>> LoadExtrasAsync(
    TreadmillRunnerDbContext context,
    Guid runId,
    CancellationToken cancellationToken) =>
    await context.WorkoutProgramExtraOccurrences.AsNoTracking()
      .Where(item => item.WorkoutProgramRunId == runId)
      .Select(item => new WorkoutProgramExtraOccurrence(item.Id, item.WorkoutProgramItemId, item.Date))
      .ToArrayAsync(cancellationToken);

  private static async Task<IReadOnlySet<Guid>> LoadCompletedItemIdsAsync(
    TreadmillRunnerDbContext context,
    Guid runId,
    CancellationToken cancellationToken) =>
    await context.WorkoutSessions.AsNoTracking()
      .Where(session => session.WorkoutProgramRunId == runId &&
        session.WorkoutProgramItemId != null &&
        session.State == nameof(SessionState.Completed))
      .Select(session => session.WorkoutProgramItemId!.Value)
      .ToHashSetAsync(cancellationToken);

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
    Guid[] skipped = await context.WorkoutProgramScheduleOverrides.AsNoTracking()
      .Where(item => item.WorkoutProgramRunId == runId && item.IsSkipped)
      .Select(item => item.WorkoutProgramItemId)
      .ToArrayAsync(cancellationToken);
    return WorkoutProgramProgressCalculator.Calculate(revision, results, skipped);
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
    TemplateId = revision.TemplateId,
    TemplateVersion = revision.TemplateVersion,
    OwnerProfileId = revision.OwnerProfileId,
    CreatedAtUtc = nowUtc,
    Items = revision.Items.Select(item => new WorkoutProgramItemEntity
    {
      Id = item.Id,
      WorkoutProgramRevisionId = revision.RevisionId,
      WorkoutRevisionId = item.WorkoutRevisionId,
      Position = item.Position,
      WeekNumber = item.WeekNumber,
      SessionNumber = item.SessionNumber,
      Phase = item.Phase,
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
      .Select(item => new WorkoutProgramItem(
        item.Id, item.WorkoutRevisionId, item.Position, item.WeekNumber, item.SessionNumber, item.Phase)).ToArray(),
    entity.TemplateId,
    entity.TemplateVersion,
    entity.OwnerProfileId);

  private static WorkoutProgramRun MapRun(WorkoutProgramRunEntity entity) => new(
    entity.Id,
    entity.UserProfileId,
    entity.WorkoutProgramRevisionId,
    Enum.Parse<WorkoutProgramRunStatus>(entity.Status),
    entity.StartedAtUtc,
    entity.EndedAtUtc,
    entity.Version,
    entity.ScheduledStartDate is { } startDate
      ? new WorkoutProgramSchedule(startDate, (WeekdayFlags)entity.ScheduledWeekdayMask, entity.ScheduleTimeZoneId!)
      : null);
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
      revision.TemplateId ?? string.Empty,
      revision.TemplateVersion ?? string.Empty,
      revision.OwnerProfileId?.ToString("D") ?? string.Empty,
      string.Join(',', revision.Items.OrderBy(static item => item.Position).Select(static item =>
        $"{item.WorkoutRevisionId:D}:{item.WeekNumber}:{item.SessionNumber}:{item.Phase}")),
    });
    return Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(value)));
  }
}
