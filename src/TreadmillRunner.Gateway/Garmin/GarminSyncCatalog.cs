using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using TreadmillRunner.Core.Calendar;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminSyncCatalog(
  IWorkoutStore workoutStore,
  IWorkoutProgramStore programStore,
  ICalendarStore calendarStore)
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<IReadOnlyList<GarminSyncDocument>> BuildSessionAsync(
    Guid profileId,
    DateOnly date,
    Guid workoutRevisionId,
    CancellationToken cancellationToken)
  {
    if (!await IsPlannedAsync(profileId, date, workoutRevisionId, cancellationToken))
      throw new KeyNotFoundException("That workout is no longer planned for the selected runner on this date.");
    StoredWorkoutRevision revision = await workoutStore.FindRevisionAsync(workoutRevisionId, cancellationToken)
      ?? throw new KeyNotFoundException("The planned workout revision was not found.");
    JsonElement definition = JsonSerializer.Deserialize<JsonElement>(revision.DefinitionJson);
    string title = definition.TryGetProperty("name", out JsonElement name)
      ? name.GetString() ?? "Planned workout"
      : "Planned workout";
    string workoutPayload = JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      type = "workout",
      sourceId = revision.Id,
      revisionId = revision.Id,
      revisionNumber = revision.RevisionNumber,
      title,
      definition,
      origin = "calendarOnDemand",
    }, JsonOptions);
    Guid occurrenceId = DeterministicOccurrenceId(profileId, date, workoutRevisionId);
    string calendarPayload = JsonSerializer.Serialize(new
    {
      schemaVersion = 1,
      type = "calendar",
      sourceId = occurrenceId,
      name = title,
      origin = "calendarOnDemand",
      occurrences = new[]
      {
        new
        {
          date,
          workouts = new[] { new { workoutRevisionId, displayOrder = 1 } },
        },
      },
    }, JsonOptions);
    return
    [
      new GarminSyncDocument("Workout", revision.Id, revision.ContentSha256, workoutPayload),
      new GarminSyncDocument("Calendar", occurrenceId, Hash(calendarPayload), calendarPayload),
    ];
  }

  private async Task<bool> IsPlannedAsync(
    Guid profileId,
    DateOnly date,
    Guid workoutRevisionId,
    CancellationToken cancellationToken)
  {
    IReadOnlyList<VersionedCalendarSeries> series = await calendarStore.ListByProfileAsync(profileId, cancellationToken);
    TrainingDaySelection recurring = TrainingDaySelectionResolver.ResolveDay(
      series.Select(static item => item.Series).ToArray(), profileId, date);
    if (recurring.Options.Any(option => option.WorkoutRevisionId == workoutRevisionId)) return true;

    IReadOnlyList<StoredWorkoutProgramProgress> programs = await programStore.ListAsync(profileId, cancellationToken);
    return programs.Where(static program => program.Run is { Status: WorkoutProgramRunStatus.Active, Schedule: not null })
      .SelectMany(program => WorkoutProgramScheduleProjector.ProjectAll(
        program.Program.CurrentRevision,
        program.Run!,
        program.ScheduleOverrides,
        program.ExtraOccurrences))
      .Any(item => item.Date == date && item.Item.AllowsWorkoutRevision(workoutRevisionId));
  }

  private static Guid DeterministicOccurrenceId(Guid profileId, DateOnly date, Guid workoutRevisionId)
  {
    byte[] hash = SHA256.HashData(Encoding.UTF8.GetBytes($"{profileId:N}:{date:yyyy-MM-dd}:{workoutRevisionId:N}"));
    return new Guid(hash.AsSpan(0, 16));
  }

  private static string Hash(string value) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
}
