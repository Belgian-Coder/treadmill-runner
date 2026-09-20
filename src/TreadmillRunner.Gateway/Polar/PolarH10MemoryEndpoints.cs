using System.Globalization;
using System.Text;
using Microsoft.Extensions.Options;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Garmin;
using TreadmillRunner.Infrastructure.Bluetooth;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.Gateway.Polar;

public sealed record PolarH10StatusResponse(
  bool MemoryCapability,
  bool IsRecording,
  string Connection,
  string? DeviceId,
  string? DisplayName,
  DateTimeOffset? LastSeenUtc,
  bool ManualOperationPending = false,
  string? ExerciseId = null)
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
public sealed record PolarH10SessionResponse(
  Guid Id,
  Guid? SessionId,
  Guid? UserProfileId,
  Guid DeviceEnrollmentId,
  string ExerciseId,
  string Origin,
  string SampleType,
  int IntervalSeconds,
  string Outcome,
  int AttemptCount,
  DateTimeOffset? LeaseExpiresAtUtc,
  DateTimeOffset? StartRequestedAtUtc,
  DateTimeOffset? StartConfirmedAtUtc,
  DateTimeOffset? StopRequestedAtUtc,
  string? RemotePath,
  string? PayloadSha256,
  int PayloadBytes,
  int MergeCount,
  int RemovalCount,
  string? LastError,
  int Version);

public static class PolarH10MemoryEndpoints
{
  public static IEndpointRouteBuilder MapPolarH10Memory(this IEndpointRouteBuilder endpoints)
  {
    endpoints.MapGroup("/api/polar-h10").MapGet("/capability", CapabilityAsync);
    RouteGroupBuilder group = endpoints.MapGroup("/api/polar-h10").AddEndpointFilter<PolarH10OperationFilter>();
    group.MapGet("/status", StatusAsync);
    group.MapGet("/overview", OverviewAsync);
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

  private static async Task<IResult> CapabilityAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    try
    {
      await accessCoordinator.ResolveEnrollmentIdAsync(null, cancellationToken);
      return Results.Ok(new { available = true, memoryCapability = true });
    }
    catch (InvalidOperationException)
    {
      return Results.Ok(new { available = false, memoryCapability = false });
    }
  }

  private static async Task<IResult> StatusAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    IPolarH10RecordingStore store,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    using var operationCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    operationCancellation.CancelAfter(TimeSpan.FromSeconds(20));
    try
    {
      await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(null, operationCancellation.Token);
      await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, operationCancellation.Token);
      PolarH10DeviceRecordingStatus status = await session.GetStatusAsync(operationCancellation.Token);
      bool pending = await store.FindActiveManualAsync(operationCancellation.Token) is not null;
      return Results.Ok(new PolarH10StatusResponse(
        true, status.IsRecording, "Connected", status.DeviceId, status.DisplayName, clock.GetUtcNow(), pending, status.ExerciseId));
    }
    catch (Exception exception) when (
      exception is InvalidOperationException or IOException or TimeoutException or WindowsBleException ||
      exception is OperationCanceledException && !cancellationToken.IsCancellationRequested)
    {
      return Results.Ok(new PolarH10StatusResponse(false, false, "Unavailable", null, null, null));
    }
  }

  private static async Task<IResult> OverviewAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    IPolarH10RecordingStore store,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    using var operationCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    operationCancellation.CancelAfter(TimeSpan.FromSeconds(20));
    try
    {
      await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(null, operationCancellation.Token);
      await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, operationCancellation.Token);
      PolarH10DeviceRecordingStatus deviceStatus = await session.GetStatusAsync(operationCancellation.Token);
      IReadOnlyList<PolarH10RemoteRecording> rows = await session.ListAsync(operationCancellation.Token);
      bool pending = await store.FindActiveManualAsync(operationCancellation.Token) is not null;
      var items = new List<PolarH10RecordingResponse>(rows.Count);
      foreach (PolarH10RemoteRecording row in rows)
      {
        string exerciseId = ExerciseId(row.RemotePath);
        PolarH10RecordingJob? stored = await store.FindByExerciseAsync(session.EnrollmentId, exerciseId, operationCancellation.Token);
        items.Add(new(exerciseId, exerciseId, stored?.Outcome.ToString() ?? "Available",
          stored?.StartConfirmedAtUtc ?? stored?.StartRequestedAtUtc, null, CanDeleteRemote: false));
      }
      var status = new PolarH10StatusResponse(true, deviceStatus.IsRecording, "Connected", deviceStatus.DeviceId,
        deviceStatus.DisplayName, clock.GetUtcNow(), pending, deviceStatus.ExerciseId);
      return Results.Ok(new { status, items });
    }
    catch (Exception exception) when (
      exception is InvalidOperationException or IOException or TimeoutException or WindowsBleException ||
      exception is OperationCanceledException && !cancellationToken.IsCancellationRequested)
    {
      return Results.Problem("The exact H10 overview is unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
  }

  private static async Task<IResult> ListAsync(
    string? source,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
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
      await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(null, cancellationToken);
      await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, cancellationToken);
      IReadOnlyList<PolarH10RemoteRecording> rows = await session.ListAsync(cancellationToken);
      var results = new List<PolarH10RecordingResponse>(rows.Count);
      foreach (PolarH10RemoteRecording row in rows)
      {
        string exerciseId = ExerciseId(row.RemotePath);
        PolarH10RecordingJob? stored = await store.FindByExerciseAsync(session.EnrollmentId, exerciseId, cancellationToken);
        results.Add(new(exerciseId, exerciseId, stored?.Outcome.ToString() ?? "Available",
          stored?.StartConfirmedAtUtc ?? stored?.StartRequestedAtUtc, null, CanDeleteRemote: false));
      }
      return Results.Ok(new { items = results });
    }
    catch (Exception exception) when (exception is InvalidOperationException or IOException or TimeoutException or WindowsBleException)
    {
      return Results.Problem("The exact H10 recording list is unavailable.", statusCode: StatusCodes.Status503ServiceUnavailable);
    }
  }

  private static async Task<IResult> StartAsync(
    StartPolarH10RecordingRequest request,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    if (!request.RrIntervals && request.IntervalSeconds is not (1 or 5)) return Results.BadRequest(new { error = "Heart-rate interval must be 1 or 5 seconds." });
    if (await store.FindActiveManualAsync(cancellationToken) is not null)
      return Results.Conflict(new { error = "A manual H10 recording operation is already pending. Refresh or stop it before starting another." });
    Guid enrollmentId = await accessCoordinator.ResolveEnrollmentIdAsync(null, cancellationToken);
    DateTimeOffset now = clock.GetUtcNow();
    string exerciseId = $"manual-{now:yyyyMMdd-HHmmss}-{Guid.NewGuid():N}"[..47];
    PolarH10RecordingJob job = await store.EnqueueAsync(null, null, exerciseId, enrollmentId, "Manual",
      request.RrIntervals ? PolarH10SampleType.RrInterval : PolarH10SampleType.HeartRate,
      request.RrIntervals ? 1 : request.IntervalSeconds, now, cancellationToken);
    worker.Wake();
    return Results.Accepted($"/api/polar-h10/recordings/{job.Id}", new { job.Id, job.ExerciseId });
  }

  private static async Task<IResult> StopAsync(
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    PolarH10RecordingJob? pending = await store.FindActiveManualAsync(cancellationToken);
    if (pending is not null)
    {
      if (pending.Outcome == PolarH10RecordingOutcome.StartPending)
      {
        await store.MarkOutcomeAsync(pending.Id, PolarH10RecordingOutcome.NotStarted,
          "The queued manual recording was cancelled before it started.", clock.GetUtcNow(), cancellationToken);
        return Results.NoContent();
      }
      await store.QueueStopByIdAsync(pending.Id, clock.GetUtcNow(), cancellationToken);
      worker.Wake();
      return Results.Accepted();
    }
    using var operationCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    operationCancellation.CancelAfter(TimeSpan.FromSeconds(20));
    try
    {
      await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(null, operationCancellation.Token);
      await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, operationCancellation.Token);
      PolarH10DeviceRecordingStatus status = await session.GetStatusAsync(operationCancellation.Token);
      if (!status.IsRecording) return Results.NoContent();
      if (string.IsNullOrWhiteSpace(status.ExerciseId)) return Results.Conflict(new { error = "The active H10 recording has no verified identifier and was left untouched." });
      PolarH10RecordingJob? job = await store.FindByExerciseAsync(status.EnrollmentId, status.ExerciseId, operationCancellation.Token);
      if (job is null) return Results.Conflict(new { error = "The active H10 recording is not owned by this gateway and was left untouched." });
      if (job.Origin != "Manual")
      {
        if (job.SessionId is not { } sessionId)
          return Results.Conflict(new { error = "The gateway owns this H10 recording, but it is not linked to a workout. Review or download it before removing the remote copy." });
        if (await store.IsSessionActiveAsync(sessionId, operationCancellation.Token))
          return Results.Conflict(new { error = "The active H10 recording belongs to the current workout. Stop the workout from the Run screen before archiving its H10 recording." });
      }
      await store.QueueStopByIdAsync(job.Id, clock.GetUtcNow(), operationCancellation.Token);
      worker.Wake();
      return Results.Accepted();
    }
    catch (Exception exception) when (
      exception is InvalidOperationException or IOException or TimeoutException or WindowsBleException ||
      exception is OperationCanceledException && !cancellationToken.IsCancellationRequested)
    {
      return Results.Json(
        new { error = $"The exact H10 recording could not be inspected or stopped: {exception.GetBaseException().Message}" },
        statusCode: StatusCodes.Status503ServiceUnavailable);
    }
  }

  private static async Task<IResult> DownloadAsync(
    string recordingId,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
    IPolarH10RecordingStore store,
    PolarH10MemoryWorker worker,
    TimeProvider clock,
    CancellationToken cancellationToken)
  {
    if (!options.Value.Enabled) return Results.NotFound();
    ValidateExerciseId(recordingId);
    await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(null, cancellationToken);
    await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, cancellationToken);
    string exactPath = $"/{recordingId}/SAMPLES.BPB";
    if (!(await session.ListAsync(cancellationToken)).Any(item => string.Equals(item.RemotePath, exactPath, StringComparison.Ordinal)))
      return Results.NotFound();
    PolarH10RecordingJob job = await store.FindByExerciseAsync(session.EnrollmentId, recordingId, cancellationToken)
      ?? await store.EnqueueAsync(null, null, recordingId, session.EnrollmentId, "Manual", PolarH10SampleType.HeartRate, 1, clock.GetUtcNow(), cancellationToken);
    if (job.Outcome != PolarH10RecordingOutcome.Retained)
      await store.QueueStopByIdAsync(job.Id, clock.GetUtcNow(), cancellationToken);
    worker.Wake();
    return Results.Accepted($"/api/polar-h10/recordings/{job.Id}");
  }

  private static async Task<IResult> DeleteRemoteAsync(
    string recordingId,
    IOptions<PolarH10MemoryOptions> options,
    IPolarH10MemoryClient client,
    IPolarH10MemoryAccessCoordinator accessCoordinator,
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
    await using IPolarH10MemoryAccessLease access = await accessCoordinator.AcquireAsync(job.DeviceEnrollmentId, cancellationToken);
    await using IPolarH10MemorySession session = await client.OpenAsync(access.EnrollmentId, cancellationToken);
    PolarH10DeviceRecordingStatus status = await session.GetStatusAsync(cancellationToken);
    if (status.IsRecording)
      return Results.Conflict(new { error = "The H10 is currently recording. Stop it before deleting any remote recording." });
    await session.DeleteAsync(job.RemotePath, cancellationToken);
    if ((await session.ListAsync(cancellationToken)).Any(item => string.Equals(item.RemotePath, job.RemotePath, StringComparison.Ordinal)))
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
    return job is null
      ? Results.NoContent()
      : Results.Ok(new PolarH10SessionResponse(
        job.Id,
        job.SessionId,
        job.UserProfileId,
        job.DeviceEnrollmentId,
        job.ExerciseId,
        job.Origin,
        job.SampleType.ToString(),
        job.IntervalSeconds,
        job.Outcome.ToString(),
        job.AttemptCount,
        job.LeaseExpiresAtUtc,
        job.StartRequestedAtUtc,
        job.StartConfirmedAtUtc,
        job.StopRequestedAtUtc,
        job.RemotePath,
        job.PayloadSha256,
        job.PayloadBytes,
        job.MergeCount,
        job.RemovalCount,
        job.LastError,
        job.Version));
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
