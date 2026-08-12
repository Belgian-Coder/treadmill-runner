using System.Net;
using System.Net.Http.Json;
using TreadmillRunner.Core.System;

namespace TreadmillRunner.Web;

public sealed class ClientRuntimeState
{
  private static readonly TimeSpan CheckFreshness = TimeSpan.FromSeconds(5);
  private readonly SemaphoreSlim checkGate = new(1, 1);
  private DateTimeOffset lastCheckAttemptUtc = DateTimeOffset.MinValue;

  public const string HeaderName = "X-TreadmillRunner-Client-Build";
  public bool IsConnected { get; private set; } = true;
  public bool UpdateRequired { get; private set; }
  public string ExpectedFingerprint => AppBuildInfo.Fingerprint;
  public string? ServerFingerprint { get; private set; }
  public DateTimeOffset? ServerStartedAtUtc { get; private set; }
  public event Action? Changed;

  public async Task CheckAsync(
    HttpClient client,
    CancellationToken cancellationToken = default,
    bool force = false)
  {
    if (!force && DateTimeOffset.UtcNow - lastCheckAttemptUtc < CheckFreshness) return;

    try
    {
      await checkGate.WaitAsync(cancellationToken);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      return;
    }

    try
    {
      if (!force && DateTimeOffset.UtcNow - lastCheckAttemptUtc < CheckFreshness) return;
      lastCheckAttemptUtc = DateTimeOffset.UtcNow;
      using HttpResponseMessage response = await client.GetAsync($"api/system/version?client={ExpectedFingerprint}", cancellationToken);
      response.EnsureSuccessStatusCode();
      SystemVersionView? version = await response.Content.ReadFromJsonAsync<SystemVersionView>(cancellationToken);
      IsConnected = true;
      ServerFingerprint = version?.BuildFingerprint;
      ServerStartedAtUtc = version?.ServiceStartedAtUtc;
      UpdateRequired = !string.Equals(ServerFingerprint, ExpectedFingerprint, StringComparison.Ordinal);
    }
    catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
    {
      return;
    }
    catch (Exception exception) when (exception is HttpRequestException or TaskCanceledException)
    {
      IsConnected = false;
    }
    finally
    {
      checkGate.Release();
    }
    Changed?.Invoke();
  }

  public void MarkUpdateRequired(string? serverFingerprint = null)
  {
    UpdateRequired = true;
    ServerFingerprint = serverFingerprint ?? ServerFingerprint;
    Changed?.Invoke();
  }

  public void SetConnected(bool connected)
  {
    if (IsConnected == connected) return;
    IsConnected = connected;
    Changed?.Invoke();
  }

  public sealed record SystemVersionView(string ReleaseVersion, string BuildFingerprint, DateTimeOffset ServiceStartedAtUtc);
}

public sealed class ClientBuildFingerprintHandler(ClientRuntimeState runtime) : DelegatingHandler
{
  protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
  {
    bool mutation = request.Method != HttpMethod.Get && request.Method != HttpMethod.Head && request.Method != HttpMethod.Options;
    if (mutation && runtime.UpdateRequired)
    {
      return new HttpResponseMessage(HttpStatusCode.Conflict)
      {
        RequestMessage = request,
        Content = JsonContent.Create(new { code = "ClientUpdateRequired", detail = "Reload the application before continuing." }),
      };
    }
    if (mutation) request.Headers.TryAddWithoutValidation(ClientRuntimeState.HeaderName, runtime.ExpectedFingerprint);
    HttpResponseMessage response = await base.SendAsync(request, cancellationToken);
    if (response.StatusCode == HttpStatusCode.Conflict &&
        response.Headers.TryGetValues("X-TreadmillRunner-Server-Build", out IEnumerable<string>? values))
    {
      runtime.MarkUpdateRequired(values.FirstOrDefault());
    }
    return response;
  }
}
