using System.Globalization;
using System.Text;
using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

public sealed record PolarH10StatusResponse(
  bool MemoryCapability,
  bool IsRecording,
  string Connection,
  string? DeviceId,
  string? DisplayName,
  DateTimeOffset? LastSeenUtc,
  bool ManualOperationPending = false)
{
  public bool Available => MemoryCapability;
}

public sealed record PolarH10RecordingResponse(
  string Id,
  string? Title,
  string Status,
  DateTimeOffset? StartedAtUtc,
  DateTimeOffset? EndedAtUtc,
  IReadOnlyList<PolarH10SampleResponse>? Samples = null,
  IReadOnlyList<uint>? RrIntervalsMilliseconds = null,
  bool CanDeleteRemote = false);

public sealed record PolarH10SampleResponse(DateTimeOffset CapturedAtUtc, ushort? BeatsPerMinute);
public sealed record StartPolarH10RecordingRequest(int IntervalSeconds = 1, bool RrIntervals = false);

public static class PolarH10MemoryEndpoints
{
  public static IEndpointRouteBuilder MapPolarH10Memory(this IEndpointRouteBuilder endpoints)
  {
    RouteGroupBuilder group = endpoints.MapGroup("/api/polar-h10").AddEndpointFilter<PolarH10OperationFilter>();
    group.MapGet("/status", StatusAsync);
    group.MapGet("/recordings", ListAsync);
    group.MapPost("/recordings/start", StartAsync);
    group.MapPost("/recordings/stop", StopAsync);
    group.MapPost("/recordings/{recordingId}/download", DownloadAsync);
    group.MapPost("/recordings/{recordingId}/delete-remote", DeleteRemoteAsync);
    group.MapGet("/recordings/{recordingId}/export.csv", ExportCsvAsync);
    group.MapGet("/sessions/{sessionId:guid}", GetSessionAsync);
    group.MapPost("/sessions/{sessionId:guid}/retry", RetrySessionAsync);
    group.MapPost("/sessions/{sessionId:guid}/skip", SkipSessionAsync);
    return endpoints;
  }

  private static async Task<IResult> StatusAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    try
    {
      PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(null, cancellationToken);
      bool pending = await store.FindActiveManualAsync(cancellationToken) is not null;
      return Results.Ok(new PolarH10StatusResponse(true, status.IsRecording, "Connected", status.DeviceId, status.DisplayName, clock.GetUtcNow(), pending));
    }
    catch (Exception exception) when (exception is InvalidOperationException or IOException or TimeoutException)
    {
      return Results.Ok(new PolarH10StatusResponse(false, false, "Unavailable", null, null, null));
    }
  }

  private static async Task<IResult> ListAsync(
    string? source,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    if (string.Equals(source, "local", StringComparison.OrdinalIgnoreCase))
    {
      IReadOnlyList<PolarH10LocalRecording> local = await store.ListLocalAsync(cancellationToken);
      return Results.Ok(new { items = local.Select(MapLocal).ToArray() });
    }
    if (!string.Equals(source, "remote", StringComparison.OrdinalIgnoreCase)) return Results.BadRequest(new { error = "Source must be remote or local." });
    try
    {
      PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(null, cancellationToken);
      IReadOnlyList<PolarH10RemoteRecording> rows = await client.ListAsync(status.EnrollmentId, cancellationToken);
      var results = new List<PolarH10RecordingResponse>(rows.Count);
      foreach (PolarH10RemoteRecording row in rows)
      {
        string exerciseId = ExerciseId(row.RemotePath);
        PolarH10RecordingJob? stored = await store.FindByExerciseAsync(status.EnrollmentId, exerciseId, cancellationToken);
        results.Add(new(exerciseId, exerciseId, stored?.Outcome.ToString() ?? "Available",
          stored?.StartConfirmedAtUtc ?? stored?.StartRequestedAtUtc, null, CanDeleteRemote: false));
      }
      return Results.Ok(new { items = results });
    }
    catch (Exception exception) when (exception is InvalidOperationException or IOException or TimeoutException)
    {
      return Results.Problem("The exact H10 recording list is unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
  }

  private static async Task<IResult> StartAsync(
    StartPolarH10RecordingRequest request,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    if (!request.RrIntervals && request.IntervalSeconds is not (1 or 5)) return Results.BadRequest(new { error = "Heart-rate interval must be 1 or 5 seconds." });
    if (await store.FindActiveManualAsync(cancellationToken) is not null)
      return Results.Conflict(new { error = "A manual H10 recording operation is already pending. Refresh or stop it before starting another." });
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(null, cancellationToken);
    if (status.IsRecording) return Results.Conflict(new { error = "The H10 is already recording. It was left untouched." });
    DateTimeOffset now = clock.GetUtcNow();
    string exerciseId = $"manual-{now:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}"[..47];
    PolarH10RecordingJob job = await store.EnqueueAsync(null, null, exerciseId, status.EnrollmentId, "Manual",
      request.RrIntervals ? PolarH10SampleType.RrInterval : PolarH10SampleType.HeartRate,
      request.RrIntervals ? 1 : request.IntervalSeconds, now, cancellationToken);
    worker.Wake();
    return Results.Accepted($"/api/polar-h10/recordings/{job.Id}", new { job.Id, job.ExerciseId });
  }

  private static async Task<IResult> StopAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(null, cancellationToken);
    PolarH10RecordingJob? pending = await store.FindActiveManualAsync(cancellationToken);
    if (!status.IsRecording)
    {
      if (pending is null) return Results.NoContent();
      if (pending.StopRequestedAtUtc is null && pending.Outcome is PolarH10RecordingOutcome.StartPending or PolarH10RecordingOutcome.Retryable)
      {
        await store.MarkOutcomeAsync(pending.Id, PolarH10RecordingOutcome.NotStarted,
          "The queued manual recording was cancelled before it started.", clock.GetUtcNow(), cancellationToken);
        return Results.NoContent();
      }
      await store.QueueStopByIdAsync(pending.Id, clock.GetUtcNow(), cancellationToken);
      worker.Wake();
      return Results.Accepted();
    }
    if (string.IsNullOrWhiteSpace(status.ExerciseId)) return Results.Conflict(new { error = "The active H10 recording has no verified identifier and was left untouched." });
    PolarH10RecordingJob? job = await store.FindByExerciseAsync(status.EnrollmentId, status.ExerciseId, cancellationToken);
    if (job is null) return Results.Conflict(new { error = "The active H10 recording is not owned by this gateway and was left untouched." });
    if (job.Origin != "Manual") return Results.Conflict(new { error = "The active H10 recording belongs to a workout and was left untouched." });
    await store.QueueStopByIdAsync(job.Id, clock.GetUtcNow(), cancellationToken);
    worker.Wake();
    return Results.Accepted();
  }

  private static async Task<IResult> DownloadAsync(
    string recordingId,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    ValidateExerciseId(recordingId);
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(null, cancellationToken);
    string exactPath = $"/{recordingId}/SAMPLES.BPB";
    if (!(await client.ListAsync(status.EnrollmentId, cancellationToken)).Any(item => string.Equals(item.RemotePath, exactPath, StringComparison.Ordinal)))
      return Results.NotFound();
    PolarH10RecordingJob job = await store.FindByExerciseAsync(status.EnrollmentId, recordingId, cancellationToken)
      ?? await store.EnqueueAsync(null, null, recordingId, status.EnrollmentId, "Manual", PolarH10SampleType.HeartRate, 1, clock.GetUtcNow(), cancellationToken);
    if (job.Outcome != PolarH10RecordingOutcome.Retained)
      await store.QueueStopByIdAsync(job.Id, clock.GetUtcNow(), cancellationToken);
    worker.Wake();
    return Results.Accepted($"/api/polar-h10/recordings/{job.Id}");
  }

  private static async Task<IResult> DeleteRemoteAsync(
    string recordingId,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10RecordingStore store,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    if (!Guid.TryParse(recordingId, out Guid id)) return Results.BadRequest(new { error = "A verified local recording ID is required." });
    PolarH10RecordingJob? job = await store.FindByIdAsync(id, cancellationToken);
    if (job is null) return Results.NotFound();
    if (job.Outcome != PolarH10RecordingOutcome.Retained || string.IsNullOrWhiteSpace(job.PayloadSha256) || string.IsNullOrWhiteSpace(job.RemotePath))
      return Results.Conflict(new { error = "A verified retained local copy is required before remote deletion." });
    PolarH10DeviceRecordingStatus status = await client.GetStatusAsync(job.DeviceEnrollmentId, cancellationToken);
    if (status.IsRecording)
      return Results.Conflict(new { error = "The H10 is currently recording. Stop it before deleting any remote recording." });
    await client.DeleteAsync(job.DeviceEnrollmentId, job.RemotePath, cancellationToken);
    if ((await client.ListAsync(job.DeviceEnrollmentId, cancellationToken)).Any(item => string.Equals(item.RemotePath, job.RemotePath, StringComparison.Ordinal)))
      return Results.Conflict(new { error = "The recording remains on the H10; refresh and retry." });
    await store.MarkRemoteRemovedAsync(job.Id, clock.GetUtcNow(), cancellationToken);
    return Results.NoContent();
  }

  private static async Task<IResult> ExportCsvAsync(
    string recordingId,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10RecordingStore store,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    if (!Guid.TryParse(recordingId, out Guid id)) return Results.BadRequest();
    PolarH10LocalRecording? row = (await store.ListLocalAsync(cancellationToken)).SingleOrDefault(item => item.Job.Id == id);
    if (row is null) return Results.NotFound();
    var csv = new StringBuilder("capturedAtUtc,beatsPerMinute,rrIntervalMilliseconds\n");
    foreach (PolarH10HeartRateSample sample in row.Samples)
      csv.Append(sample.CapturedAtUtc.ToString("O", CultureInfo.InvariantCulture)).Append(',').Append(sample.BeatsPerMinute).Append(',').AppendLine();
    DateTimeOffset rrTime = row.StartedAtUtc ?? DateTimeOffset.UnixEpoch;
    foreach (uint rr in row.RrIntervalsMilliseconds)
    {
      csv.Append(rrTime.ToString("O", CultureInfo.InvariantCulture)).Append(",,").Append(rr).AppendLine();
      rrTime = rrTime.AddMilliseconds(rr);
    }
    return Results.File(Encoding.UTF8.GetBytes(csv.ToString()), "text/csv; charset=utf-8", $"polar-h10-{id:N}.csv");
  }

  private static async Task<IResult> GetSessionAsync(Guid sessionId, IOptions<PolarH10MemoryOptions> options, IPolarH10RecordingStore store, CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    PolarH10RecordingJob? job = await store.FindAsync(sessionId, cancellationToken);
    return job is null ? Results.NoContent() : Results.Ok(job);
  }

  private static async Task<IResult> RetrySessionAsync(
    Guid sessionId, IOptions<PolarH10MemoryOptions> options, IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker, TimeProvider clock, CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    PolarH10RecordingJob? job = await store.FindAsync(sessionId, cancellationToken);
    if (job is null) return Results.NotFound();
    if (job.Outcome is PolarH10RecordingOutcome.Completed or PolarH10RecordingOutcome.Skipped or PolarH10RecordingOutcome.NotStarted)
      return Results.Conflict(new { error = "This H10 recovery is already terminal." });
    await store.RetryAsync(job.Id, clock.GetUtcNow(), cancellationToken);
    worker.Wake();
    return Results.Accepted();
  }

  private static async Task<IResult> SkipSessionAsync(
    Guid sessionId, IOptions<PolarH10MemoryOptions> options, IPolarH10RecordingStore store,
    IGarminActivityUploadWakeSignal garminWorker, TimeProvider clock, CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    PolarH10RecordingJob? job = await store.FindAsync(sessionId, cancellationToken);
    if (job is null) return Results.NotFound();
    if (job.Outcome is PolarH10RecordingOutcome.Merged or PolarH10RecordingOutcome.RemovalPending or PolarH10RecordingOutcome.Completed)
      return Results.Conflict(new { error = "The H10 history recovery already completed; remote cleanup will continue automatically." });
    await store.MarkOutcomeAsync(job.Id, PolarH10RecordingOutcome.Skipped, "H10 recovery was explicitly skipped by the operator; any remote recording was retained.", clock.GetUtcNow(), cancellationToken);
    garminWorker.Wake();
    return Results.NoContent();
  }

  private static PolarH10RecordingResponse MapLocal(PolarH10LocalRecording row) => new(
    row.Job.Id.ToString("D"), row.Job.ExerciseId, row.Job.Outcome.ToString(), row.StartedAtUtc, row.EndedAtUtc,
    row.Samples.Select(sample => new PolarH10SampleResponse(sample.CapturedAtUtc, sample.BeatsPerMinute)).ToArray(),
    row.RrIntervalsMilliseconds,
    row.Job.Outcome == PolarH10RecordingOutcome.Retained && row.Job.RemotePath is not null && row.Job.RemovalCount == 0);

  private static string ExerciseId(string remotePath)
  {
    string[] parts = remotePath.Split('/', StringSplitOptions.RemoveEmptyEntries);
    if (parts.Length != 2 || !parts[1].Equals("SAMPLES.BPB", StringComparison.OrdinalIgnoreCase))
      throw new InvalidOperationException("The H10 returned an unsupported exercise path.");
    ValidateExerciseId(parts[0]);
    return parts[0];
  }

  private static void ValidateExerciseId(string value)
  {
    if (string.IsNullOrWhiteSpace(value) || value.Length > 64 || value.Contains('/') || value.Contains('\\') || value.Contains('\0'))
      throw new ArgumentException("The H10 exercise identifier is invalid.", nameof(value));
  }
}
