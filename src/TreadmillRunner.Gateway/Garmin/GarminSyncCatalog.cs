using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Workouts;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Garmin;

public sealed class GarminSyncCatalog(
  IWorkoutStore workoutStore,
  IWorkoutProgramStore programStore,
  ICalendarStore calendarStore,
  IOptions<GarminOptions> options,
  TimeProvider timeProvider)
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

  public async Task<IReadOnlyList<GarminSyncDocument>> BuildAsync(Guid profileId, CancellationToken cancellationToken)
  {
    var result = new List<GarminSyncDocument>();
    IReadOnlyList<StoredWorkout> workouts = await workoutStore.ListAsync(cancellationToken);
    foreach (StoredWorkout workout in workouts.Where(static workout => !workout.IsArchived && workout.Kind == WorkoutKind.Structured))
    {
      string payload = JsonSerializer.Serialize(new
      {
        schemaVersion = 1,
        type = "workout",
        sourceId = workout.Id,
        revisionId = workout.LatestRevisionId,
        revisionNumber = workout.LatestRevisionNumber,
        title = workout.Name,
        definition = JsonSerializer.Deserialize<JsonElement>(workout.LatestDefinitionJson),
      }, JsonOptions);
      result.Add(new GarminSyncDocument("Workout", workout.Id, workout.LatestContentSha256, payload));
    }

    IReadOnlyList<StoredWorkoutProgramProgress> programs = await programStore.ListAsync(profileId, cancellationToken);
    foreach (StoredWorkoutProgramProgress stored in programs.Where(static program => !program.Program.IsArchived))
    {
      WorkoutProgramRevision revision = stored.Program.CurrentRevision;
      string payload = JsonSerializer.Serialize(new
      {
        schemaVersion = 1,
        type = "trainingPlan",
        sourceId = revision.ProgramId,
        revisionId = revision.RevisionId,
        revisionNumber = revision.RevisionNumber,
        revision.Name,
        revision.Description,
        revision.Category,
        workouts = revision.Items.Select(item => new { item.Position, item.WorkoutRevisionId }),
      }, JsonOptions);
      result.Add(new GarminSyncDocument("TrainingPlan", revision.ProgramId, $"{revision.RevisionNumber}:{revision.RevisionId:N}", payload));
    }

    DateOnly today = DateOnly.FromDateTime(timeProvider.GetLocalNow().DateTime);
    DateOnly through = today.AddDays(Math.Clamp(options.Value.FutureCalendarDays, 1, 366));
    IReadOnlyList<VersionedCalendarSeries> series = await calendarStore.ListByProfileAsync(profileId, cancellationToken);
    foreach (VersionedCalendarSeries stored in series)
    {
      var occurrences = new List<object>();
      for (DateOnly date = today; date <= through; date = date.AddDays(1))
      {
        var selection = TreadmillRunner.Core.Calendar.TrainingDaySelectionResolver.ResolveDay([stored.Series], profileId, date);
        if (selection.Options.Count > 0)
        {
          occurrences.Add(new
          {
            date,
            workouts = selection.Options.Select(option => new { option.WorkoutRevisionId, option.DisplayOrder }),
          });
        }
      }

      string payload = JsonSerializer.Serialize(new
      {
        schemaVersion = 1,
        type = "calendar",
        sourceId = stored.Series.Id,
        scheduleGroupId = stored.Series.ScheduleGroupId,
        stored.Series.Name,
        stored.Series.TimeZoneId,
        occurrences,
      }, JsonOptions);
      string version = $"{stored.Version}:{Hash(payload)}";
      result.Add(new GarminSyncDocument("Calendar", stored.Series.Id, version, payload));
    }

    return result;
  }

  private static string Hash(string value) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
}
