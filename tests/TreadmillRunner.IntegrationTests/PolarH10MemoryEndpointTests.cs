using System.Net;
using System.Net.Http.Json;
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
  public async Task Status_reports_a_connected_memory_capability_through_the_hosted_gateway()
  {
    Guid enrollmentId = Guid.NewGuid();
    using var application = factory.WithWebHostBuilder(builder => builder.ConfigureServices(services =>
    {
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(new StubPolarH10MemoryClient(
        new PolarH10DeviceRecordingStatus(enrollmentId, "synthetic-device", "Polar H10", false, null)));
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
      services.RemoveAll<IPolarH10MemoryClient>();
      services.AddSingleton<IPolarH10MemoryClient>(new StubPolarH10MemoryClient(new TimeoutException("Synthetic timeout.")));
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
      services.RemoveAll<IPolarH10RecordingStore>();
      services.AddSingleton<IPolarH10RecordingStore>(new StatusFailingPolarH10RecordingStore());
    }));
    using HttpClient client = application.CreateClient();

    using HttpResponseMessage response = await client.GetAsync("/api/polar-h10/status");
    PolarH10StatusResponse? status = await response.Content.ReadFromJsonAsync<PolarH10StatusResponse>();

    Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    Assert.NotNull(status);
    Assert.False(status.MemoryCapability);
    Assert.Equal("Unavailable", status.Connection);
  }

  private sealed class StubPolarH10MemoryClient : IPolarH10MemoryClient
  {
    private readonly PolarH10DeviceRecordingStatus? _status;
    private readonly Exception? _exception;

    public StubPolarH10MemoryClient(PolarH10DeviceRecordingStatus status) => _status = status;
    public StubPolarH10MemoryClient(Exception exception) => _exception = exception;

    public Task<PolarH10DeviceRecordingStatus> GetStatusAsync(Guid? enrollmentId, CancellationToken cancellationToken = default) =>
      _exception is null ? Task.FromResult(_status!) : Task.FromException<PolarH10DeviceRecordingStatus>(_exception);

    public Task StartAsync(Guid enrollmentId, string exerciseId, PolarH10SampleType sampleType, int intervalSeconds, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task StopAsync(Guid enrollmentId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<IReadOnlyList<PolarH10RemoteRecording>> ListAsync(Guid enrollmentId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10MemoryRecord> FetchAsync(Guid enrollmentId, string remotePath, DateTimeOffset startedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task DeleteAsync(Guid enrollmentId, string remotePath, CancellationToken cancellationToken = default) => throw new NotSupportedException();
  }

  private sealed class StatusFailingPolarH10RecordingStore : IPolarH10RecordingStore
  {
    public Task<PolarH10RecordingJob?> FindActiveManualAsync(CancellationToken cancellationToken = default) =>
      Task.FromException<PolarH10RecordingJob?>(new InvalidOperationException("Synthetic storage failure."));

    public Task<PolarH10RecordingJob?> LeaseNextAsync(DateTimeOffset nowUtc, TimeSpan leaseDuration, CancellationToken cancellationToken = default) => Task.FromResult<PolarH10RecordingJob?>(null);
    public Task<PolarH10RecordingJob> EnqueueAsync(Guid? sessionId, Guid? userProfileId, string exerciseId, Guid enrollmentId, string origin, PolarH10SampleType sampleType, int intervalSeconds, DateTimeOffset queuedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> QueueStopAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> QueueDiscardCleanupAsync(Guid sessionId, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingJob?> FindAsync(Guid sessionId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingJob?> FindByIdAsync(Guid id, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingJob?> FindByExerciseAsync(Guid enrollmentId, string exerciseId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> IsSessionActiveAsync(Guid sessionId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<IReadOnlyList<PolarH10LocalRecording>> ListLocalAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkRecordingAsync(Guid id, DateTimeOffset confirmedAtUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task QueueStopByIdAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task StoreDownloadedAsync(Guid id, PolarH10MemoryRecord recording, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<PolarH10RecordingOutcome> MergeDownloadedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkRemoteRemovedAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task CompleteDiscardCleanupAsync(Guid id, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task RetryAsync(Guid id, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task MarkOutcomeAsync(Guid id, PolarH10RecordingOutcome outcome, string? error, DateTimeOffset nowUtc, CancellationToken cancellationToken = default) => throw new NotSupportedException();
  }
}
