using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using TreadmillRunner.Core.Sessions;
using TreadmillRunner.Gateway.Polar;
using TreadmillRunner.Infrastructure.Persistence;

namespace TreadmillRunner.IntegrationTests;

public sealed class PolarH10MemoryEndpointTests(PlanningGatewayFactory factory) : IClassFixture<PlanningGatewayFactory>
{
  [Fact]
  public async Task Capability_reports_the_enrolled_H10_without_suspending_live_heart_rate()
  {
    Guid enrollmentId = Guid.NewGuid();
    var memoryClient = new StubPolarH10MemoryClient(
      new PolarH10DeviceRecordingStatus(enrollmentId, "synthetic-device", "Polar H10", false, null));
    var accessCoordinator = new StubMemoryAccessCoordinator(enrollmentId);
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(memoryClient);
      services.RemoveAll<IPolarH10MemoryAccessCoordinator>();
      services.AddSingleton<IPolarH10MemoryAccessCoordinator>(accessCoordinator);
    }));
    using HttpClient client = application.CreateClient();
    await using IAsyncDisposable heldGate = await application.Services
      .GetRequiredService<PolarH10OperationGate>()
      .EnterAsync(CancellationToken.None);

    Task<HttpResponseMessage> responseTask = client.GetAsync("/api/polar-h10/capability");
    using HttpResponseMessage response = await responseTask.WaitAsync(TimeSpan.FromSeconds(10));

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.Equal(1, accessCoordinator.ResolveCalls);
    Assert.Equal(0, accessCoordinator.AcquireCalls);
    Assert.Equal(0, memoryClient.OpenCalls);
  }

  [Fact]
  public async Task Status_reports_a_connected_memory_capability_through_the_hosted_gateway()
  {
    Guid enrollmentId = Guid.NewGuid();
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(new StubPolarH10MemoryClient(
        new PolarH10DeviceRecordingStatus(enrollmentId, "synthetic-device", "Polar H10", false, null)));
      services.RemoveAll<IPolarH10MemoryAccessCoordinator>();
      services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new StubMemoryAccessCoordinator(enrollmentId));
    }));
    using HttpClient client = application.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/polar-h10/status");
    PolarH10StatusResponse? status = await response.Content.ReadFromJsonAsync<PolarH10StatusResponse>();

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.NotNull(status);
    Assert.True(status.MemoryCapability);
    Assert.False(status.IsRecording);
    Assert.Equal("Connected", status.Connection);
    Assert.Equal("synthetic-device", status.DeviceId);
  }

  [Fact]
  public async Task Status_maps_a_bounded_Polar_timeout_to_unavailable()
  {
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      Guid enrollmentId = Guid.NewGuid();
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(new StubPolarH10MemoryClient(new TimeoutException("Synthetic timeout.")));
      services.RemoveAll<IPolarH10MemoryAccessCoordinator>();
      services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new StubMemoryAccessCoordinator(enrollmentId));
    }));
    using HttpClient client = application.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/polar-h10/status");
    PolarH10StatusResponse? status = await response.Content.ReadFromJsonAsync<PolarH10StatusResponse>();

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.NotNull(status);
    Assert.False(status.MemoryCapability);
    Assert.Equal("Unavailable", status.Connection);
  }

  [Fact]
  public async Task Status_does_not_report_connected_when_the_pending_operation_store_fails()
  {
    Guid enrollmentId = Guid.NewGuid();
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(new StubPolarH10MemoryClient(
        new PolarH10DeviceRecordingStatus(enrollmentId, "synthetic-device", "Polar H10", false, null)));
      services.RemoveAll<IPolarH10MemoryAccessCoordinator>();
      services.AddSingleton<IPolarH10MemoryAccessCoordinator>(new StubMemoryAccessCoordinator(enrollmentId));
      services.RemoveAll<IPolarH10RecordingStore>();
      services.AddSingleton<IPolarH10RecordingStore>(new StubPolarH10RecordingStore(failActiveLookup: true));
    }));
    using HttpClient client = application.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/polar-h10/status");
    PolarH10StatusResponse? status = await response.Content.ReadFromJsonAsync<PolarH10StatusResponse>();

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.NotNull(status);
    Assert.False(status.MemoryCapability);
    Assert.Equal("Unavailable", status.Connection);
  }

  [Fact]
  public async Task Session_status_uses_the_string_outcome_contract_consumed_by_history_detail()
  {
    Guid sessionId = Guid.NewGuid();
    Guid profileId = Guid.NewGuid();
    Guid jobId = Guid.NewGuid();
    var job = new PolarH10RecordingJob(
      jobId,
      sessionId,
      profileId,
      Guid.NewGuid(),
      $"tr-{sessionId:N}",
      "Automatic",
      PolarH10SampleType.HeartRate,
      1,
      PolarH10RecordingOutcome.ReviewRequired,
      2,
      null,
      DateTimeOffset.UtcNow.AddMinutes(-20),
      DateTimeOffset.UtcNow.AddMinutes(-20),
      DateTimeOffset.UtcNow.AddMinutes(-1),
      "/recording/SAMPLES.BPB",
      null,
      0,
      0,
      0,
      "Synthetic review reason.",
      1);
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<IPolarH10RecordingStore>();
      services.AddSingleton<IPolarH10RecordingStore>(new StubPolarH10RecordingStore(job));
    }));
    using HttpClient client = application.CreateClient();

    using HttpResponseMessage response = await client.GetAsync($"/api/polar-h10/sessions/{sessionId}");
    string responseJson = await response.Content.ReadAsStringAsync();
    var jsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web);
    HistoryPolarMemoryJob? status = JsonSerializer.Deserialize<HistoryPolarMemoryJob>(responseJson, jsonOptions);
    PolarH10SessionResponse? fullStatus = JsonSerializer.Deserialize<PolarH10SessionResponse>(responseJson, jsonOptions);

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.NotNull(status);
    Assert.NotNull(fullStatus);
    Assert.Equal(jobId, status.Id);
    Assert.Equal(sessionId, status.SessionId);
    Assert.Equal(profileId, status.UserProfileId);
    Assert.Equal("ReviewRequired", status.Outcome);
    Assert.Equal(job.LastError, status.LastError);
    Assert.Equal(job.DeviceEnrollmentId, fullStatus.DeviceEnrollmentId);
    Assert.Equal(job.ExerciseId, fullStatus.ExerciseId);
    Assert.Equal("Automatic", fullStatus.Origin);
    Assert.Equal("HeartRate", fullStatus.SampleType);
    Assert.Equal("ReviewRequired", fullStatus.Outcome);
    Assert.Equal(job.RemotePath, fullStatus.RemotePath);
    Assert.Equal(job.Version, fullStatus.Version);
  }

  private sealed class StubPolarH10MemoryClient : IPolarH10MemoryClient, IPolarH10MemorySession
  {
    private readonly PolarH10DeviceRecordingStatus? _status;
    private readonly Exception? _exception;

    public StubPolarH10MemoryClient(PolarH10DeviceRecordingStatus status) => _status = status;
    public StubPolarH10MemoryClient(Exception exception) => _exception = exception;

    public Guid EnrollmentId => _status?.EnrollmentId ?? Guid.Empty;
    public string DeviceId => _status?.DeviceId ?? "synthetic-device";
    public string DisplayName => _status?.DisplayName ?? "Polar H10";
    public int OpenCalls { get; private set; }
    public Task<IPolarH10MemorySession> OpenAsync(Guid? enrollmentId, CancellationToken cancellationToken = default)
    {
      OpenCalls++;
      return Task.FromResult<IPolarH10MemorySession>(this);
    }
    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(CancellationToken cancellationToken = default) =>
      _exception is null ? Task.FromResult(_status!) : Task.FromException<PolarH10DeviceRecordingStatus>(_exception);

    public Task<PolarH10StartResult> StartAsync(string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task StopAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(CancellationToken cancellationToken = default) => Task.FromResult<IReadOnlyList<PolarH10RemoteRecording>>([]);
    public Task<PolarH10MemoryRecord> FetchAsync(string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task DeleteAsync(string remotePath, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }

  private sealed class StubMemoryAccessCoordinator(Guid enrollmentId) : IPolarH10MemoryAccessCoordinator
  {
    public int ResolveCalls { get; private set; }
    public int AcquireCalls { get; private set; }
    public Task<Guid> ResolveEnrollmentIdAsync(Guid? requestedEnrollmentId, CancellationToken cancellationToken = default)
    {
      ResolveCalls++;
      return Task.FromResult(requestedEnrollmentId ?? enrollmentId);
    }

    public Task<IPolarH10MemoryAccessLease> AcquireAsync(Guid? requestedEnrollmentId, CancellationToken cancellationToken = default)
    {
      cancellationToken.ThrowIfCancellationRequested();
      AcquireCalls++;
      return Task.FromResult<IPolarH10MemoryAccessLease>(new StubMemoryAccessLease(requestedEnrollmentId ?? enrollmentId));
    }
  }

  private sealed class StubMemoryAccessLease(Guid enrollmentId) : IPolarH10MemoryAccessLease
  {
    public Guid EnrollmentId { get; } = enrollmentId;
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
  }

  private sealed record HistoryPolarMemoryJob(
    Guid Id,
    Guid? SessionId,
    Guid? UserProfileId,
    string Outcome,
    int AttemptCount,
    DateTimeOffset? LeaseExpiresAtUtc,
    string? LastError);

  private sealed class StubPolarH10RecordingStore(
    PolarH10RecordingJob? sessionJob = null,
    bool failActiveLookup = false) : IPolarH10RecordingStore
  {
    public Task<PolarH10RecordingJob?> FindActiveManualAsync(CancellationToken cancellationToken = default) =>
      failActiveLookup
        ? Task.FromException<PolarH10RecordingJob?>(new InvalidOperationException("Synthetic storage failure."))
        : Task.FromResult<PolarH10RecordingJob?>(null);

    public Task<PolarH10RecordingJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default) => Task.FromResult<PolarH10RecordingJob?>(null);
    public Task<PolarH10RecordingJob> EnqueueAsync(Guid? sessionId, Guid? userProfileId, string exerciseId, Guid enrollmentId, string origin, PolarH10SampleType sampleType, int intervalSeconds, DateTimeOffset queuedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> QueueStopAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> QueueDiscardCleanupAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingJob?> FindAsync(Guid sessionId, CancellationToken cancellationToken = default) =>
      Task.FromResult(sessionJob?.SessionId == sessionId ? sessionJob : null);
    public Task<PolarH10RecordingJob?> FindByIdAsync(Guid id, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingJob?> FindByExerciseAsync(Guid enrollmentId, string exerciseId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> IsSessionActiveAsync(Guid sessionId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<IReadOnlyList<PolarH10LocalRecording>> ListLocalAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkRecordingAsync(Guid id, DateTimeOffset confirmedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task QueueStopByIdAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> QueueStopByIdIfVersionAsync(Guid id, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task StoreDownloadedAsync(Guid id, PolarH10MemoryRecord recording, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingOutcome> MergeDownloadedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkRemoteRemovedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> MarkRemoteRemovedIfVersionAsync(Guid id, int expectedVersion, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task CompleteDiscardCleanupAsync(Guid id, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task RetryAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkOutcomeAsync(Guid id, PolarH10RecordingOutcome outcome, string? error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> MarkOutcomeIfVersionAsync(Guid id, int expectedVersion, int attemptCount, PolarH10RecordingOutcome outcome, string? error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
  }
}
