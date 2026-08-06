using System.Net;
using System.Net.Http.Json;
using TreadmillRunner.Core.System;

namespace TreadmillRunner.Web;

public sealed class ClientRuntimeState
{
  public const string HeaderName = "X-TreadmillRunner-Client-Build";
  public bool IsConnected { get; private set; } = true;
  public bool UpdateRequired { get; private set; }
  public string ExpectedFingerprint => AppBuildInfo.Fingerprint;
  public string? ServerFingerprint { get; private set; }
  public DateTimeOffset? ServerStartedAtUtc { get; private set; }
  public event Action? Changed;

  public async Task CheckAsync(HttpClient client, CancellationToken cancellationToken = default)
  {
    try
    {
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
    Changed?.Invoke();
  }

  public void MarkUpdateRequired(string? serverFingerprint = null)
  {
    UpdateRequired = true;
    ServerFingerprint = serverFingerprint ?? ServerFingerprint;
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
